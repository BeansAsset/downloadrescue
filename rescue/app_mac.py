"""The window on a Mac: Cocoa and WebKit (WKWebView), showing web/window.html.

Uses PyObjC, the standard bridge from Python to macOS:

    python3 -m pip install --user pyobjc-framework-WebKit

The page talks to Python through window.webkit.messageHandlers, the same
way it does on Linux, so the page and backend.py are shared unchanged.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

try:
    import objc
    from AppKit import (NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                        NSColor, NSImage, NSMenu, NSMenuItem, NSOpenPanel, NSPasteboard,
                        NSPasteboardTypeString, NSWindow, NSWindowStyleMaskClosable,
                        NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
                        NSWorkspace)
    from Foundation import NSURL, NSBundle, NSJSONSerialization, NSMakeRect, NSObject, NSSize
    from PyObjCTools import AppHelper
    from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration
except ImportError:
    sys.exit("DownloadRescue's Mac window needs PyObjC. Install it with:\n\n"
             "    python3 -m pip install --user pyobjc-framework-WebKit\n")

from . import APP_NAME  # noqa: E402
from .backend import Backend  # noqa: E402

DARK_AQUA = "NSAppearanceNameDarkAqua"


def system_is_dark():
    forced = os.environ.get("RESCUE_THEME")
    if forced in ("light", "dark"):
        return forced == "dark"
    appearance = NSApplication.sharedApplication().effectiveAppearance()
    match = appearance.bestMatchFromAppearancesWithNames_(["NSAppearanceNameAqua", DARK_AQUA])
    return match == DARK_AQUA


class MessageHandler(NSObject):
    """Receives what the page posts to window.webkit.messageHandlers.app."""

    def initWithOwner_(self, owner):
        self = objc.super(MessageHandler, self).init()
        if self is None:
            return None
        self.owner = owner
        return self

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        # The page posts a plain object; JSON is the simplest faithful way
        # to turn Cocoa's dictionaries and numbers into Python ones.
        data, _err = NSJSONSerialization.dataWithJSONObject_options_error_(message.body(), 0, None)
        if data is None:
            return
        msg = json.loads(bytes(data).decode("utf-8"))
        if isinstance(msg, dict):
            self.owner.handle(msg)


class AppDelegate(NSObject):
    def applicationShouldTerminateAfterLastWindowClosed_(self, _app):
        return True


class MacWindow(Backend):
    def __init__(self):
        self.setup_backend()
        self.page_ready = False
        self.dark = system_is_dark()

        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                 NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 600, 680), style, NSBackingStoreBuffered, False)
        self.window.setTitle_(APP_NAME)
        self.window.setMinSize_(NSSize(480, 520))
        self.window.setBackgroundColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.11, 0.11, 0.118, 1) if self.dark
            else NSColor.whiteColor())
        self.window.center()
        self.window.setReleasedWhenClosed_(False)

        controller = WKUserContentController.alloc().init()
        self.handler = MessageHandler.alloc().initWithOwner_(self)
        controller.addScriptMessageHandler_name_(self.handler, "app")
        config = WKWebViewConfiguration.alloc().init()
        config.setUserContentController_(controller)
        self.view = WKWebView.alloc().initWithFrame_configuration_(self.window.contentView().bounds(), config)
        self.view.setAutoresizingMask_(18)                 # width and height follow the window
        try:
            self.view.setValue_forKey_(False, "drawsBackground")   # no white flash before the page draws
        except Exception:  # an undocumented setting; without it there's just a brief flash
            pass
        self.window.contentView().addSubview_(self.view)

        page = NSURL.fileURLWithPath_(os.path.join(HERE, "web", "window.html"))
        # The page loads its font and the app icon from the project folder.
        self.view.loadFileURL_allowingReadAccessToURL_(page, NSURL.fileURLWithPath_isDirectory_(ROOT, True))
        self.window.makeKeyAndOrderFront_(None)

    # -- what differs on a Mac -----------------------------------------------------------------
    def _js(self, code):
        self.view.evaluateJavaScript_completionHandler_(code, None)

    def on_ui(self, fn):
        AppHelper.callAfter(fn)

    def resolve(self, req_id, result):
        self._js(f"App.resolve({json.dumps(req_id)}, {json.dumps(result)})")

    def emit(self, name, data=None):
        if self.page_ready:
            self._js(f"App.emit({json.dumps(name)}, {json.dumps(data)})")

    def is_dark(self):
        return self.dark

    def pick_file(self, start_dir, done):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setMessage_("Choose the unfinished download (.crdownload, .part, ...)")
        panel.setDirectoryURL_(NSURL.fileURLWithPath_isDirectory_(start_dir, True))

        def answered(response):
            urls = panel.URLs()
            done(urls[0].path() if response == 1 and urls else None)   # 1 = OK
        panel.beginSheetModalForWindow_completionHandler_(self.window, answered)

    def clipboard_text(self):
        return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)

    def reveal(self, path):
        NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(path)])

    def open_path(self, path):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(path))


def _menus(app):
    """The app menu (Quit) and an Edit menu: without one, Cmd+V and the other
    editing shortcuts don't reach the text fields."""
    bar = NSMenu.alloc().init()
    app_item = NSMenuItem.alloc().init()
    bar.addItem_(app_item)
    app_menu = NSMenu.alloc().init()
    app_menu.addItemWithTitle_action_keyEquivalent_(f"Hide {APP_NAME}", "hide:", "h")
    app_menu.addItem_(NSMenuItem.separatorItem())
    app_menu.addItemWithTitle_action_keyEquivalent_(f"Quit {APP_NAME}", "terminate:", "q")
    app_item.setSubmenu_(app_menu)

    edit_item = NSMenuItem.alloc().init()
    bar.addItem_(edit_item)
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), (None, None, None),
                               ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                               ("Select All", "selectAll:", "a")):
        if title is None:
            edit.addItem_(NSMenuItem.separatorItem())
        else:
            edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
    edit_item.setSubmenu_(edit)

    window_item = NSMenuItem.alloc().init()
    bar.addItem_(window_item)
    window_menu = NSMenu.alloc().initWithTitle_("Window")
    window_menu.addItemWithTitle_action_keyEquivalent_("Minimize", "performMiniaturize:", "m")
    window_menu.addItemWithTitle_action_keyEquivalent_("Close", "performClose:", "w")
    window_item.setSubmenu_(window_menu)
    app.setMainMenu_(bar)


def run():
    # Show "DownloadRescue" in the menu bar, not "Python".
    info = NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = APP_NAME
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    icon = os.path.join(ROOT, "data", "DownloadRescue.icns")
    if os.path.exists(icon):
        app.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icon))
    _menus(app)
    window = MacWindow()
    app.activateIgnoringOtherApps_(True)
    AppHelper.runEventLoop()
    del window, delegate
    return 0
