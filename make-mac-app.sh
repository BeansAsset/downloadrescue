#!/bin/sh
# Makes DownloadRescue.app in your Applications folder (Mac only), so it
# opens from Launchpad, Spotlight and the Dock like any other app. The app
# runs the code in this folder, so keep the folder where it is.
#
#   ./make-mac-app.sh            make (or update) the app
#   ./make-mac-app.sh --remove   delete the app again
set -e
here=$(cd "$(dirname "$0")" && pwd)
app="$HOME/Applications/DownloadRescue.app"

if [ "$(uname)" != Darwin ]; then
  echo "This is for macOS. On Linux, run ./install-integration.sh instead."
  exit 1
fi
if [ "$1" = "--remove" ]; then
  rm -rf "$app"
  echo "Removed $app"
  exit 0
fi

python=$(command -v python3 || true)
if [ -z "$python" ] || ! "$python" -c "import sys; sys.exit(sys.version_info < (3, 9))" 2>/dev/null; then
  echo "DownloadRescue needs Python 3.9 or newer. Install Apple's developer tools with:"
  echo
  echo "    xcode-select --install"
  echo
  exit 1
fi
if ! "$python" -c "import AppKit, WebKit" 2>/dev/null; then
  echo "DownloadRescue's window needs PyObjC, the bridge from Python to macOS. Install it with:"
  echo
  echo "    $python -m pip install --user pyobjc-framework-WebKit"
  echo
  echo "Then run this again."
  exit 1
fi

mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$here/data/DownloadRescue.icns" "$app/Contents/Resources/DownloadRescue.icns"
version=$("$python" -c "import sys; sys.path.insert(0, '$here'); import rescue; print(rescue.VERSION)")
cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>io.github.beansasset.DownloadRescue</string>
  <key>CFBundleName</key><string>DownloadRescue</string>
  <key>CFBundleDisplayName</key><string>DownloadRescue</string>
  <key>CFBundleExecutable</key><string>DownloadRescue</string>
  <key>CFBundleIconFile</key><string>DownloadRescue</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF
cat > "$app/Contents/MacOS/DownloadRescue" <<EOF
#!/bin/sh
exec "$python" "$here/downloadrescue-app" "\$@"
EOF
chmod +x "$app/Contents/MacOS/DownloadRescue"
touch "$app"
echo "Made $app"
echo "Open it from Launchpad or Spotlight, or: open \"$app\""
