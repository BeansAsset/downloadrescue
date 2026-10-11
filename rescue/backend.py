"""What the window can ask for, shared by the Linux and Mac windows.

The page (web/window.js) sends {id, cmd, args}; handle() runs cmd_<name>
and the answer goes back with resolve(). Events from a running rescue are
pushed with emit(). Each window supplies the few things that differ by
system: running code on the UI thread, the file picker, the clipboard,
showing and opening files, and whether the system is dark.
"""

from __future__ import annotations

import os
import threading
import time
import urllib.parse

from . import engine, finder, naming
from .engine import Failed, Partial, Refused

LATER = object()   # a command that answers later calls resolve() itself


def kind_of(found):
    """A short label for the file's type: ISO, ZIP, PDF..."""
    ext = os.path.splitext(found.name or "")[1].lstrip(".")
    if not ext:
        try:
            with open(found.path, "rb") as f:
                ext = (naming.sniff(f.read(40000)) or "").lstrip(".")
        except OSError:
            ext = ""
    return ext.upper()[:5] if ext and len(ext) <= 8 else "FILE"


def describe(found):
    return {
        "id": found.path, "name": found.name, "have": found.have, "total": found.total,
        "kind": kind_of(found),
        "good": found.good or ([(0, found.have)] if found.have else []),
        "browser": found.browser, "when": found.when,
        "old_host": finder.host_of(found.old_url) if found.old_url else None,
        "old_url": found.old_url,
        "resuming": os.path.exists(found.path + ".rescue"),
    }


class Backend:
    # -- supplied by each window --------------------------------------------------------
    def on_ui(self, fn):                 # run fn() on the UI thread
        raise NotImplementedError

    def resolve(self, req_id, result):   # answer a command
        raise NotImplementedError

    def emit(self, name, data):          # push an event to the page
        raise NotImplementedError

    def pick_file(self, start_dir, done):    # done(path or None)
        raise NotImplementedError

    def clipboard_text(self):
        raise NotImplementedError

    def reveal(self, path):              # show the file in the file manager
        raise NotImplementedError

    def open_path(self, path):
        raise NotImplementedError

    def is_dark(self):
        raise NotImplementedError

    # -- shared --------------------------------------------------------------------------
    def setup_backend(self):
        self.found = {}
        self.current = None

    def handle(self, msg):
        """Run one message from the page. Errors go back to the page."""
        req_id = msg.get("id")
        try:
            handler = getattr(self, "cmd_" + str(msg.get("cmd", "")).replace("-", "_"))
            result = handler(req_id, *msg.get("args", []))
        except Exception as e:  # report to the page instead of crashing
            if os.environ.get("RESCUE_DEBUG"):
                raise
            result = {"error": str(e)}
        if req_id is not None and result is not LATER:
            self.resolve(req_id, result)

    def cmd_ready(self, _id):
        self.page_ready = True
        return {"dark": self.is_dark(), "downloads": self.cmd_list(None)}

    def cmd_list(self, _id):
        found = finder.find_all()
        self.found.update({f.path: f for f in found})
        return [describe(f) for f in found]

    def cmd_choose(self, req_id):
        """Pick a partial file by hand."""
        def done(path):
            result = None
            if path and os.path.isfile(path):
                found = self.found.get(path) or finder.loose_file(path)
                self.found[path] = found
                result = describe(found)
            self.resolve(req_id, result)
        self.pick_file(finder.downloads_dir(), done)
        return LATER

    def cmd_paste(self, _id):
        return (self.clipboard_text() or "").strip()

    def cmd_rescue(self, _id, path, url, sha256):
        found = self.found.get(path)
        if found is None:
            return {"error": "That download isn't there any more."}
        if urllib.parse.urlsplit(url.strip()).scheme not in ("http", "https"):
            return {"error": "Paste a link that starts with http:// or https://."}
        partial = Partial(path=found.path, total=found.total, good=list(found.good), name=found.name,
                          etag=found.etag, browser=found.browser)
        last = {"t": 0.0}

        def event(kind, **details):
            # Progress can come thousands of times a second; the page only
            # needs about ten updates a second (and always the last one).
            if kind == "progress":
                now = time.monotonic()
                if now - last["t"] < 0.1 and details["done"] < details["total"]:
                    return
                last["t"] = now
            self.on_ui(lambda: self.emit(kind, details))

        rescue = self.current = engine.Rescue(partial, url, sha256, on_event=event)

        def work():
            try:
                final, how = rescue.run()
                event("done", path=final, name=os.path.basename(final), how=how)
            except Refused as e:
                event("refused", text=str(e))
            except Failed as e:
                event("failed", text=str(e), kept=os.path.exists(partial.path + ".rescue"))
            except OSError as e:
                event("failed", text=f"Couldn't write the file: {e.strerror or e}.", kept=True)
        threading.Thread(target=work, daemon=True).start()
        return True

    def cmd_cancel(self, _id):
        if self.current is not None:
            self.current.cancelled = True
        return True

    def cmd_show(self, _id, path):
        self.reveal(path)
        return True

    def cmd_open(self, _id, path):
        self.open_path(path)
        return True
