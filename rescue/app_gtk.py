"""The window on Linux: GTK and WebKitGTK, showing web/window.html."""

from __future__ import annotations

import json
import os
import sys

from .bridge import WebWindow  # sets up GTK and WebKit versions

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME  # noqa: E402
from .backend import Backend  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(os.path.dirname(HERE), "data", APP_ID + ".svg")


def prefers_dark():
    """Whether the desktop asks apps to be dark: the standard setting first
    (the settings portal), then the GTK theme's name. RESCUE_THEME=light or
    dark overrides it."""
    forced = os.environ.get("RESCUE_THEME")
    if forced in ("light", "dark"):
        return forced == "dark"
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                              "org.freedesktop.portal.Settings", "Read",
                              GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                              None, Gio.DBusCallFlags.NONE, 800, None)
        value = reply.unpack()[0]
        while isinstance(value, GLib.Variant):
            value = value.unpack()
        if value in (1, 2):
            return value == 1
    except GLib.Error:
        pass
    theme = Gtk.Settings.get_default().get_property("gtk-theme-name") or ""
    return "dark" in theme.lower()


class Window(WebWindow, Backend):
    page = "window.html"

    def __init__(self, app):
        self.dark = prefers_dark()
        background = (0.11, 0.11, 0.118, 1) if self.dark else (1, 1, 1, 1)
        super().__init__(app, background=background, title=APP_NAME)
        self.set_default_size(600, 680)
        self.setup_backend()

    def _on_message(self, _ucm, js_result):
        try:
            msg = json.loads(js_result.get_js_value().to_json(0))
        except (ValueError, TypeError):
            return
        if isinstance(msg, dict):
            self.handle(msg)

    # -- what differs on Linux -------------------------------------------------------------
    def on_ui(self, fn):
        GLib.idle_add(lambda: (fn(), False)[1])

    def is_dark(self):
        return self.dark

    def pick_file(self, start_dir, done):
        dialog = Gtk.FileChooserNative.new("Open a Partial Download", self, Gtk.FileChooserAction.OPEN,
                                           "Open", "Cancel")
        dialog.set_current_folder(start_dir)

        def answered(d, response):
            done(d.get_filename() if response == Gtk.ResponseType.ACCEPT else None)
            d.destroy()
        dialog.connect("response", answered)
        dialog.show()

    def clipboard_text(self):
        return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()

    def reveal(self, path):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")),
                          None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri(), None)

    def open_path(self, path):
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", prefers_dark())

    def do_command_line(self, command_line):
        if self.window is None:
            self.window = Window(self)
            self.window.connect("destroy", lambda *_: setattr(self, "window", None))
            self.window.show_all()
        self.window.present()
        return 0


def run():
    # Name the windows after the app id, so the panel matches them to the
    # menu entry and shows the app's icon instead of a generic one.
    GLib.set_prgname(APP_ID)
    Gdk.set_program_class(APP_ID)
    if os.path.exists(ICON):
        Gtk.Window.set_default_icon_from_file(ICON)
    return Application().run(sys.argv[:1])
