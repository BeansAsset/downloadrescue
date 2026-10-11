"""Set DownloadRescue up for the current user, on whichever distro this is.

    python3 tools/integrate.py install    menu entry and icon
    python3 tools/integrate.py remove     undo it
    python3 tools/integrate.py check      only check what's missing

Never uses sudo. If something it needs is missing, it prints the command for
this distro's package manager and stops.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ID = "io.github.beansasset.DownloadRescue"
NAME = "DownloadRescue"
COMMAND = os.path.join(HERE, "downloadrescue-app")
HOME = os.path.expanduser("~")
DATA = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
APPS_FILE = os.path.join(DATA, "applications", APP_ID + ".desktop")
ICON_FILE = os.path.join(DATA, "icons", "hicolor", "scalable", "apps", APP_ID + ".svg")


# -- what this system is ----------------------------------------------------------

def read_os_release(path="/etc/os-release"):
    info = {}
    try:
        with open(path) as f:
            for line in f:
                if "=" in line:
                    key, _, value = line.strip().partition("=")
                    info[key] = value.strip('"')
    except OSError:
        pass
    return info


def distro_family(os_release):
    ids = [os_release.get("ID", "")] + os_release.get("ID_LIKE", "").split()
    for family, members in (("debian", {"debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin"}),
                            ("fedora", {"fedora", "rhel", "centos", "nobara"}),
                            ("arch", {"arch", "manjaro", "endeavouros", "garuda"}),
                            ("suse", {"opensuse", "suse", "opensuse-tumbleweed", "opensuse-leap"})):
        if any(i in members for i in ids):
            return family
    return None


PACKAGES = {
    # what DownloadRescue needs -> package name in each family
    "debian": {"gi": "python3-gi", "gtk": "gir1.2-gtk-3.0", "webkit": "gir1.2-webkit2-4.1"},
    "fedora": {"gi": "python3-gobject", "gtk": "gtk3", "webkit": "webkit2gtk4.1"},
    "arch": {"gi": "python-gobject", "gtk": "gtk3", "webkit": "webkit2gtk-4.1"},
    "suse": {"gi": "python3-gobject", "gtk": "typelib-1_0-Gtk-3_0", "webkit": "typelib-1_0-WebKit2-4_1"},
}
INSTALL = {"debian": "sudo apt install", "fedora": "sudo dnf install",
           "arch": "sudo pacman -S --needed", "suse": "sudo zypper install"}


def install_command(family, missing):
    if family not in PACKAGES or not missing:
        return None
    names = " ".join(PACKAGES[family][m] for m in missing)
    return f"{INSTALL[family]} {names}"


def _has(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def missing_requirements():
    missing = []
    try:
        import gi
    except ImportError:
        missing += ["gi", "gtk", "webkit"]
    else:
        try:
            gi.require_version("Gtk", "3.0")
        except ValueError:
            missing.append("gtk")
        for version in ("4.1", "4.0"):
            try:
                gi.require_version("WebKit2", version)
                break
            except ValueError:
                continue
        else:
            missing.append("webkit")
    return missing


# -- files: menu entry, icon, login item ----------------------------------------------

def write_files():
    os.makedirs(os.path.dirname(APPS_FILE), exist_ok=True)
    os.makedirs(os.path.dirname(ICON_FILE), exist_ok=True)
    shutil.copyfile(os.path.join(HERE, "data", APP_ID + ".svg"), ICON_FILE)
    # Running desktops cache which icons exist; a newer timestamp on the
    # theme folder makes them look again, so the icon shows up right away.
    os.utime(os.path.dirname(os.path.dirname(os.path.dirname(ICON_FILE))))
    with open(APPS_FILE, "w") as f:
        f.write(f"""[Desktop Entry]
Type=Application
Name={NAME}
Comment=Finish a failed download from a new link
Exec={COMMAND}
Icon={APP_ID}
StartupWMClass={APP_ID}
Categories=Utility;
Keywords=download;resume;rescue;failed;interrupted;partial;crdownload;mirror;
StartupNotify=false
""")


def remove_files():
    for path in (APPS_FILE, ICON_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


# -- commands ------------------------------------------------------------------------------

def check():
    missing = missing_requirements()
    if not missing:
        return True
    family = distro_family(read_os_release())
    cmd = install_command(family, missing)
    print(f"{NAME} needs a few things first.")
    if cmd:
        print(f"Install them with:\n\n    {cmd}\n")
    else:
        print("Install these with your package manager: Python GObject, GTK 3 and WebKitGTK (4.1 or 4.0).")
    print("Then run this again.")
    return False


def install():
    if not check():
        return 1
    write_files()
    print(f"Added {NAME} to your apps menu.")
    return 0


def remove():
    remove_files()
    print(f"Removed {NAME}'s menu entry.")
    return 0


def main(argv):
    action = argv[1] if len(argv) > 1 else "install"
    if action == "install":
        return install()
    if action == "remove":
        return remove()
    if action == "check":
        return 0 if check() else 1
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
