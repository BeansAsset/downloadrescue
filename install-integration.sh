#!/bin/sh
# Sets DownloadRescue up for your user (no sudo):
#   - checks what it needs, and shows the install command if anything's missing
#   - DownloadRescue in your apps menu, with its icon
#
#   ./install-integration.sh            set it all up
#   ./install-integration.sh --remove   undo all of it
here=$(cd "$(dirname "$0")" && pwd)
if [ "$1" = "--remove" ]; then
  exec python3 "$here/tools/integrate.py" remove
fi
exec python3 "$here/tools/integrate.py" install
