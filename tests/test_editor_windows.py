"""Window identity, host envelopes and registry races with a fake native backend."""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.domain.profiles import validate_profile_id
from dayz_serverman.host.api import HostApi
from dayz_serverman.host.editor_windows import EditorWindowRegistry
from dayz_serverman.repositories.profiles import ProfileNotFound


FRONTEND = Path(__file__).resolve().parents[1] / "runnable/src/frontend"


class FakeEvent(threading.Event):
    """Dispatch native event handlers synchronously for deterministic race tests."""

    def __init__(self) -> None:
        """Start with no handlers or event result."""
        super().__init__()
        self.handlers = []

    def __iadd__(self, handler):
        """Register a lifecycle handler."""
        self.handlers.append(handler)
        return self

    def fire(self) -> bool:
        """Return whether handlers allow the event, then publish its completion."""
        allowed = all(handler() is not False for handler in self.handlers)
        self.set()
        return allowed


class FakeEditor:
    """Record window actions and control acknowledgement and callback independently."""

    def __init__(self) -> None:
        """Make a loaded document with the accepted immediate close response."""
        self.events = SimpleNamespace(loaded=FakeEvent(), closing=FakeEvent(), closed=FakeEvent())
        self.events.loaded.set()
        self.actions = []
        self.ack = True
        self.answer = "close"
        self.callback = None
        self.evaluate_gate = None
        self.error = False
        self.confirmation = True
        self.send_closed = True

    def evaluate_js(self, script, callback=None):
        """Record scripts and keep the final answer separate from acknowledgement."""
        self.actions.append(script)
        if callback is None:
            return True
        self.callback = callback
        if self.evaluate_gate is not None:
            self.evaluate_gate.wait(2)
        if self.error:
            raise OSError("private path must not leak")
        if self.answer is not None:
            callback(self.answer)
        return self.ack

    def create_confirmation_dialog(self, title, text):
        """Capture the exact owned dialog and return the administrator's answer."""
        self.actions.append((title, text))
        return self.confirmation

    def restore(self):
        """Record restoration before focus."""
        self.actions.append("restore")

    def show(self):
        """Record foreground presentation."""
        self.actions.append("show")

    def destroy(self):
        """Respect the closing refusal and optionally send the native closed event."""
        if self.events.closing.fire():
            self.actions.append("destroy")
            if self.send_closed:
                self.events.closed.fire()

    def toggle_fullscreen(self):
        """Record fullscreen on the bound editor only."""
        self.actions.append("fullscreen")


class FakeWebview:
    """Keep creation options and distinct windows for each native create."""

    def __init__(self):
        """Start with an empty window inventory."""
        self.windows = []
        self.options = []

    def create_window(self, title, **options):
        """Capture the complete window contract."""
        window = FakeEditor()
        self.windows.append(window)
        self.options.append({"title": title, **options})
        return window


class FakeProfiles:
    """Provide profile identities without any manager or server filesystem."""

    def __init__(self):
        """Start with two independently bound profiles."""
        self.names = {"alpha": "Alpha", "beta": "Beta"}

    def read(self, profile_id):
        """Validate IDs and refuse missing records before registry conflict checks."""
        identifier = validate_profile_id(profile_id)
        if identifier not in self.names:
            raise ProfileNotFound(identifier)
        return SimpleNamespace(values=SimpleNamespace(profile_id=identifier, display_name=self.names[identifier]))


class FakeLogger:
    """Keep structured lifecycle events for focused assertions."""

    def __init__(self):
        """Start with no events."""
        self.events = []

    def emit(self, event, **payload):
        """Capture fields and severity."""
        self.events.append((event, payload))


def registry_fixture():
    """Return one started registry and its isolated native and profile doubles."""
    webview, profiles, logger = FakeWebview(), FakeProfiles(), FakeLogger()
    registry = EditorWindowRegistry(webview, None, profiles, None, FRONTEND, logger,
                                    work_area=lambda _: (100, 200, 1300, 950))
    registry.mark_started()
    return registry, webview, profiles, logger


class EditorWindowTests(unittest.TestCase):
    """Protect one-window ownership and the safe host-only action."""

    def test_creation_options_and_profile_bound_api(self):
        """Create a centred editor with text selection and immutable API identity."""
        registry, webview, profiles, _ = registry_fixture()
        self.assertEqual(registry.open("alpha"), {"opened": True, "focused": False})
        options = webview.options[0]
        expected = {"title": "Map editor - Alpha", "width": 1200, "height": 750,
                    "x": 100, "y": 200, "min_size": (960, 600), "resizable": True,
                    "confirm_close": False, "text_select": True, "zoomable": False}
        self.assertEqual({key: options[key] for key in expected}, expected)
        self.assertIn("ServerManMapEditor", options["html"])
        profiles.names["alpha"] = "Renamed"
        self.assertEqual(registry.open("alpha"), {"opened": False, "focused": True})
        self.assertEqual(webview.windows[0].actions, ["restore", "show"])
        self.assertEqual(options["js_api"]._profile_id, "alpha")
        options["js_api"].toggle_full_screen()
        self.assertEqual(webview.windows[0].actions[-1], "fullscreen")
        self.assertEqual(len(webview.windows), 1)

    def test_duplicate_open_is_serialized(self):
        """Concurrent browser callbacks must create exactly one native editor."""
        registry, webview, _, _ = registry_fixture()
        barrier = threading.Barrier(9)
        answers = []
        def open_editor():
            """Start all callbacks at the same boundary."""
            barrier.wait()
            answers.append(registry.open("alpha"))
        threads = [threading.Thread(target=open_editor) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(webview.windows), 1)
        self.assertEqual(sum(answer["opened"] for answer in answers), 1)

    def test_host_envelopes_unknown_profile_first_and_no_paths(self):
        """Opening remains host-only, with safe identity and conflict failures."""
        registry, _, _, _ = registry_fixture()
        api = HostApi(None)
        api._set_map_editor_opener(registry.open)
        self.assertTrue(api.open_map_editor("alpha")["success"])
        registry.begin_close()
        missing = api.open_map_editor("missing")
        self.assertEqual(missing["error"]["code"], "NOT_FOUND")
        closing = api.open_map_editor("beta")
        self.assertEqual(closing["error"], {"code": "MUTATION_CONFLICT", "retryable": False,
            "message": "The application is closing.",
            "details": {"reason": "application_closing", "cause": "SHUTTING_DOWN"}})
        for value in (None, {}, "D:/private", "../alpha"):
            self.assertEqual(api.open_map_editor(value)["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(missing["contract_version"], 1)
        self.assertNotEqual(missing["request_id"], closing["request_id"])

    def test_unbound_and_creation_failures_are_safe(self):
        """Hide native exceptions and do not leave an orphaned registry entry."""
        api = HostApi(None)
        self.assertEqual(api.open_map_editor("alpha")["error"]["code"], "INTERNAL_FAILURE")
        def fail(_):
            """Simulate a native error with sensitive detail."""
            raise RuntimeError("D:/private/secret")
        api._set_map_editor_opener(fail)
        self.assertNotIn("private", str(api.open_map_editor("alpha")))
        registry, _, _, _ = registry_fixture()
        registry._started = False
        api._set_map_editor_opener(registry.open)
        self.assertEqual(api.open_map_editor("alpha")["error"]["code"], "INTERNAL_FAILURE")

    def test_insufficient_work_area_returns_safe_geometry_refusal_before_creation(self):
        """Do not shrink the accepted minimum or leave an entry after a fit refusal."""
        registry, webview, _, _ = registry_fixture()
        registry._work_area = lambda _: (0, 0, 959, 600)
        api = HostApi(None)
        api._set_map_editor_opener(registry.open)
        answer = api.open_map_editor("alpha")
        self.assertFalse(answer["success"])
        self.assertEqual(answer["error"]["code"], "INTERNAL_FAILURE")
        self.assertEqual(answer["error"]["message"],
            "The map editor cannot fit in this monitor's work area at the current display scale.")
        self.assertEqual(webview.windows, [])
        self.assertEqual(registry._entries, {})

    def test_closed_editor_is_removed_and_can_reopen(self):
        """A closed generation must release the profile for a new editor."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        registry._entries["alpha"].close.destroy_approved()
        self.assertNotIn("alpha", registry._entries)
        self.assertTrue(registry.open("alpha")["opened"])
        self.assertEqual(len(webview.windows), 2)

    def test_late_closed_generation_cannot_rebind_or_destroy_reopened_editor(self):
        """Late controller callbacks remain bound to their original window generation."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        old = registry._entries["alpha"]
        old.close.destroy_approved()
        registry.open("alpha")
        new = registry._entries["alpha"]
        old.close._state_changed("CLOSING")
        old.close._close_requested()
        self.assertEqual(new.state, "OPEN")
        self.assertNotIn("destroy", webview.windows[1].actions)

    def test_native_editor_close_destroys_only_its_window_and_releases_guard(self):
        """The editor's native event must never target the main window or shutdown."""
        registry, webview, _, logger = registry_fixture()
        registry.open("alpha")
        window = webview.windows[0]
        controller = registry._entries["alpha"].close
        window.destroy()
        self.assertTrue(controller.closed.wait(1))
        self.assertEqual(window.actions.count("destroy"), 1)
        self.assertNotIn("alpha", registry._entries)
        with registry.hold_for_deletion("alpha"):
            self.assertEqual(registry._deleting, {"alpha"})
        self.assertEqual(logger.events, [])
