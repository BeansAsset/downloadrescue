"""DownloadRescue in a terminal. Needs nothing but Python: no window toolkit.

    downloadrescue-app PARTIAL-FILE NEW-LINK [--sha256 DIGEST]
"""

from __future__ import annotations

import os
import sys
import time

from . import engine, finder
from .engine import Failed, Partial, Refused


def run(args):
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
    last = {"t": 0.0}

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
