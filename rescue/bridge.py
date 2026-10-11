"""A GTK window holding one of the web pages, and the bridge to it.

The page sends {id, cmd, args}; the window runs cmd_<name>(req_id, *args)
and answers with App.resolve(id, result). Pushing to the page is
App.emit(name, data). A command that finishes later returns LATER and
calls resolve() itself.
"""

import json
import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
except ValueError:   # older distros ship only the 4.0 API, which works too
    gi.require_version("WebKit2", "4.0")
from gi.repository import Gdk, Gtk, WebKit2  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
DEBUG = bool(os.environ.get("RESCUE_DEBUG"))
LATER = object()


class WebWindow(Gtk.Window):
    page = "index.html"

    def __init__(self, app, background=(0, 0, 0, 1), **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.set_application(app)
        self.page_ready = False
        self.view = self._make_view(background)
        self.add(self.view)

    def _make_view(self, background):
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("app")
        ucm.connect("script-message-received::app", self._on_message)
        view = WebKit2.WebView(user_content_manager=ucm)
        s = view.get_settings()
        s.set_allow_file_access_from_file_urls(True)
        s.set_enable_developer_extras(DEBUG)
        s.set_enable_write_console_messages_to_stdout(DEBUG)
        s.set_javascript_can_open_windows_automatically(False)
        s.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
        view.set_background_color(Gdk.RGBA(*background))
        view.connect("decide-policy", self._on_policy)
        view.connect("context-menu", lambda *_: not DEBUG)
        view.load_uri("file://" + os.path.join(WEB, self.page))
        return view

    def _on_policy(self, _view, decision, kind):
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith("file://" + WEB):
                decision.ignore()
                return True
        elif kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False

    def emit(self, name, data=None):
        if self.page_ready:
            self.view.evaluate_javascript(
                f"App.emit({json.dumps(name)}, {json.dumps(data)})", -1, None, None, None, None, None)

    def resolve(self, req_id, result):
        self.view.evaluate_javascript(
            f"App.resolve({json.dumps(req_id)}, {json.dumps(result)})", -1, None, None, None, None, None)

    def _on_message(self, _ucm, js_result):
        msg = {}
        try:
            msg = json.loads(js_result.get_js_value().to_json(0))
            handler = getattr(self, "cmd_" + msg.get("cmd", "").replace("-", "_"))
            result = handler(msg.get("id"), *msg.get("args", []))
        except Exception as e:  # report to the page instead of crashing
            if DEBUG:
                raise
            result = {"error": str(e)}
        if msg.get("id") is not None and result is not LATER:
            self.resolve(msg["id"], result)
