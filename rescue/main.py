"""DownloadRescue: finish a download whose link died, from a new link.

    downloadrescue-app                         open the window
    downloadrescue-app PARTIAL-FILE NEW-LINK   rescue in a terminal
        [--sha256 DIGEST]                      check the result against this

The window is GTK on Linux (app_gtk.py) and Cocoa on a Mac (app_mac.py);
both show the same page and share backend.py. The terminal command
(cli.py) needs neither. All the care about not damaging the file is in
engine.py.
"""

import sys


def main():
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        from .cli import run
        return run(args)
    if sys.platform == "darwin":
        from .app_mac import run
    else:
        from .app_gtk import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
