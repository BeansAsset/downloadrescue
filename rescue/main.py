"""DownloadRescue: finish a download whose link died, from a new link.

    downloadrescue-app                         open the window
    downloadrescue-app PARTIAL-FILE NEW-LINK   rescue in the terminal
        [--sha256 DIGEST]                      check the result against this

The window lists downloads that stopped part way (from Chrome-based
browsers, and unfinished files in Downloads). All the care about not
damaging the file is in engine.py.
"""

import os
import sys
import threading
import time
import urllib.parse

from .bridge import LATER, WebWindow  # sets up GTK and WebKit versions

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, engine, finder  # noqa: E402
from .engine import Failed, Partial, Refused  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(os.path.dirname(HERE), "data", APP_ID + ".svg")


def describe(found):
    return {
        "id": found.path, "name": found.name, "have": found.have, "total": found.total,
        "good": found.good or ([(0, found.have)] if found.have else []),
        "browser": found.browser, "when": found.when,
        "old_host": finder.host_of(found.old_url) if found.old_url else None,
        "old_url": found.old_url,
        "resuming": os.path.exists(found.path + ".rescue"),
    }


class Window(WebWindow):
    page = "window.html"

    def __init__(self, app):
        super().__init__(app, background=(0.133, 0.133, 0.149, 1), title=APP_NAME)
        self.set_default_size(620, 640)
        self.found = {}
        self.current = None

    def cmd_ready(self, _id):
        self.page_ready = True
        return self.cmd_list(None)

    def cmd_list(self, _id):
        found = finder.find_all()
        self.found.update({f.path: f for f in found})
        return [describe(f) for f in found]

    def cmd_choose(self, req_id):
        """Pick a partial file by hand."""
        dialog = Gtk.FileChooserNative.new("Open a Partial Download", self, Gtk.FileChooserAction.OPEN,
                                           "Open", "Cancel")
        dialog.set_current_folder(finder.downloads_dir())

        def answered(d, response):
            result = None
            if response == Gtk.ResponseType.ACCEPT:
                path = d.get_filename()
                if path and os.path.isfile(path):
                    found = self.found.get(path) or finder.loose_file(path)
                    self.found[path] = found
                    result = describe(found)
            self.resolve(req_id, result)
            d.destroy()
        dialog.connect("response", answered)
        dialog.show()
        return LATER

    def cmd_paste(self, _id):
        text = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() or ""
        return text.strip()

    def cmd_rescue(self, _id, path, url, sha256):
        found = self.found.get(path)
        if found is None:
            return {"error": "That download isn't there any more."}
        if urllib.parse.urlsplit(url.strip()).scheme not in ("http", "https"):
            return {"error": "Paste a link that starts with http:// or https://."}
        partial = Partial(path=found.path, total=found.total, good=list(found.good), name=found.name,
                          etag=found.etag, browser=found.browser)

        def event(kind, **details):
            GLib.idle_add(lambda: (self.emit(kind, details), False)[1])

        self.current = engine.Rescue(partial, url, sha256, on_event=event)
        rescue = self.current

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
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")),
                          None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri(), None)
        return True

    def cmd_open(self, _id, path):
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        return True


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

    def do_command_line(self, command_line):
        if self.window is None:
            self.window = Window(self)
            self.window.connect("destroy", lambda *_: setattr(self, "window", None))
            self.window.show_all()
        self.window.present()
        return 0


def terminal(args):
    """downloadrescue-app PARTIAL NEW-LINK [--sha256 DIGEST]"""
    sha = None
    if "--sha256" in args:
        i = args.index("--sha256")
        sha = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    if len(args) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path, url = os.path.abspath(args[0]), args[1]
    if not os.path.isfile(path):
        print(f"No such file: {path}", file=sys.stderr)
        return 2
    known = {f.path: f for f in finder.find_all()}.get(path) or finder.loose_file(path)
    partial = Partial(path=path, total=known.total, good=list(known.good), name=known.name, etag=known.etag)
    last = {"t": 0}

    def event(kind, **d):
        if kind == "step" and d["state"] != "doing":
            mark = {"ok": "ok", "note": "--", "bad": "!!"}[d["state"]]
            print(f"\r\033[K  {mark}  {d['text']}", flush=True)
        elif kind == "progress" and time.monotonic() - last["t"] > 0.5:
            last["t"] = time.monotonic()
            print(f"      {d['done'] * 100 // max(1, d['total'])}% of {d['total']:,} bytes", end="\r", flush=True)
    try:
        final, how = engine.Rescue(partial, url, sha, on_event=event).run()
    except Refused as e:
        print(f"\nNot rescued: {e}\nYour download wasn't changed.")
        return 1
    except Failed as e:
        print(f"\n{e}")
        return 1
    print(f"\nDone: {final}\nChecked by {how}.")
    return 0


def main():
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        return terminal(args)
    GLib.set_prgname(APP_ID)
    Gdk.set_program_class(APP_ID)
    if os.path.exists(ICON):
        Gtk.Window.set_default_icon_from_file(ICON)
    return Application().run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
