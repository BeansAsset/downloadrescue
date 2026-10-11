"""Finding downloads that stopped part way.

Chrome-based browsers (Brave, Chrome, Chromium, Edge, Vivaldi, Opera) keep a
record of every download: where the partial file is, how big the whole file
should be, and, for downloads fetched in parallel pieces, exactly which byte
ranges arrived. That record is read from a copy of the browser's History
file; only the downloads table is used, and nothing is changed.

Other unfinished files in the Downloads folder (.crdownload, .part) are
listed too, without that extra knowledge.
"""

import os
import shutil
import sqlite3
import tempfile
import urllib.parse
from dataclasses import dataclass, field

from gi.repository import GLib

HOME = os.path.expanduser("~")
BROWSERS = {
    "Brave": ".config/BraveSoftware/Brave-Browser",
    "Chrome": ".config/google-chrome",
    "Chromium": ".config/chromium",
    "Edge": ".config/microsoft-edge",
    "Vivaldi": ".config/vivaldi",
    "Opera": ".config/opera",
}
INTERRUPTED = (3, 4)          # Chrome's download states for "stopped by a problem"
PARTIAL_ENDINGS = (".crdownload", ".part", ".partial", ".download")
EPOCH_DIFF = 11644473600       # seconds between 1601 (Chrome's clock) and 1970


@dataclass
class Found:
    path: str
    name: str                      # what the finished file will be called
    have: int                      # bytes known to be on disk
    total: int | None
    good: list = field(default_factory=list)
    browser: str | None = None
    old_url: str | None = None
    etag: str | None = None
    when: float | None = None      # when it stopped (Unix time)


def _profiles(root):
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        history = os.path.join(root, name, "History")
        if (name == "Default" or name.startswith("Profile ")) and os.path.isfile(history):
            out.append(history)
    return out


def _from_history(history, browser):
    with tempfile.TemporaryDirectory(prefix="rescue-") as tmp:
        copy = os.path.join(tmp, "History")
        shutil.copyfile(history, copy)       # the browser may have it open
        for extra in ("-wal",):
            if os.path.exists(history + extra):
                shutil.copyfile(history + extra, copy + extra)
        db = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = db.execute(
                "SELECT id, current_path, target_path, received_bytes, total_bytes, etag, end_time "
                "FROM downloads WHERE state IN (?, ?)", INTERRUPTED).fetchall()
            found = []
            for did, current, target, received, total, etag, end_time in rows:
                if not current or not os.path.isfile(current):
                    continue
                size = os.path.getsize(current)
                slices = db.execute("SELECT offset, received_bytes FROM downloads_slices "
                                    "WHERE download_id = ?", (did,)).fetchall()
                if slices:
                    good = [(off, off + got) for off, got in slices if got > 0]
                else:
                    good = [(0, received)] if received > 0 else []
                good = [(s, min(e, size)) for s, e in good if s < size]
                chain = db.execute("SELECT url FROM downloads_url_chains WHERE id = ? "
                                   "ORDER BY chain_index DESC LIMIT 1", (did,)).fetchone()
                when = end_time / 1e6 - EPOCH_DIFF if end_time else os.path.getmtime(current)
                found.append(Found(path=current, name=os.path.basename(target or current),
                                   have=sum(e - s for s, e in good), total=total or None, good=good,
                                   browser=browser, old_url=chain[0] if chain else None,
                                   etag=etag or None, when=when))
            return found
        finally:
            db.close()


def downloads_dir():
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.join(HOME, "Downloads")


def loose_file(path):
    """A partial file the browsers don't know about (or chosen by hand)."""
    name = os.path.basename(path)
    for ending in PARTIAL_ENDINGS:
        if name.endswith(ending):
            name = name[: -len(ending)]
            break
    size = os.path.getsize(path)
    return Found(path=path, name=name, have=size, total=None, when=os.path.getmtime(path))


def find_all():
    """Every rescuable download, newest first."""
    found, seen = [], set()
    for browser, rel in BROWSERS.items():
        for history in _profiles(os.path.join(HOME, rel)):
            try:
                for f in _from_history(history, browser):
                    if f.path not in seen:
                        seen.add(f.path)
                        found.append(f)
            except (sqlite3.Error, OSError):
                continue
    folder = downloads_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        path = os.path.join(folder, name)
        if name.endswith(PARTIAL_ENDINGS) and path not in seen and os.path.isfile(path) \
                and os.path.getsize(path) > 0:
            seen.add(path)
            found.append(loose_file(path))
    return sorted(found, key=lambda f: f.when or 0, reverse=True)


def host_of(url):
    try:
        return urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None
