"""Host results of the 5.4 close dialogs: a cancel keeps the windows, a close destroys, answers have no deadline."""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dayz_serverman.application.shutdown import ShutdownState
from dayz_serverman.host.shutdown import SafeCloseController
from tests.test_editor_windows import FakeEditor, registry_fixture


SETTLE = "window.ServerManMapEditor.settleForClose('{}')"
JOIN = "window.ServerManMapEditor.settleForClose('application', true)"
RELEASE = "window.ServerManMapEditor.releaseClose()"


def wait_until(predicate, seconds=1.0):
    """Bound the test orchestration; the production wait for a final answer has no limit."""
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    return predicate()


def main_controller(registry):
    """Return a main close controller with a clean shell and an observable shutdown."""
    main, order = FakeEditor(), []
    original = main.evaluate_js

    def evaluate(script, callback=None):
        """Allow the shell close and keep every other script on the fake window."""
        if script == "window.ServerManTransitions.requestNativeClose()":
            order.append("shell")
            return True
        return original(script, callback)

    def shutdown():
        """Record the backend shutdown boundary."""
        order.append("shutdown")
        return SimpleNamespace(state=ShutdownState.CLOSED, to_dict=lambda: {})

    main.evaluate_js = evaluate
    return SafeCloseController(main, SimpleNamespace(request_shutdown=shutdown), editors=registry), main, order


class EditorCloseResultTests(unittest.TestCase):
    """The document answers after its dialog; the host only acts on the answer."""

    def test_keep_editing_keeps_the_editor_and_a_later_close_destroys_it(self):
        """An editor close answered "cancel" keeps the window open and unreleased; "close" destroys only it."""
        registry, webview, _, logger = registry_fixture()
        registry.open("alpha")
        window, entry = webview.windows[0], registry._entries["alpha"]
        window.answer = "cancel"
        self.assertFalse(window.events.closing.fire())
        entry.close._close_worker.join(1)
        self.assertIn(SETTLE.format("editor"), window.actions)
        self.assertNotIn("destroy", window.actions)
        self.assertNotIn(RELEASE, window.actions)
        self.assertEqual((entry.state, registry._closing), ("OPEN", False))
        # A later close of the same editor, answered "close", destroys it without a shutdown
        window.answer = "close"
        self.assertFalse(window.events.closing.fire())
        entry.close._close_worker.join(1)
        self.assertIn("destroy", window.actions)
        self.assertNotIn("alpha", registry._entries)
        self.assertEqual(logger.events, [])

    def test_application_close_waits_for_late_answers_without_a_deadline(self):
        """Dialog answers after the acknowledgement limit still decide; both "close" destroy before the shutdown."""
        registry, webview, _, logger = registry_fixture()
        registry.open("alpha")
        registry.open("beta")
        for window in webview.windows:
            window.answer = None
        controller, main, order = main_controller(registry)
        with patch("dayz_serverman.host.editor_close.ACK_TIMEOUT", 0.03):
            worker = threading.Thread(target=controller._request_close, daemon=True)
            worker.start()
            for window in webview.windows:
                self.assertTrue(wait_until(lambda current=window: current.callback is not None))
                time.sleep(0.1)
                self.assertTrue(worker.is_alive())
                self.assertEqual(order, ["shell"])
                self.assertIn(SETTLE.format("application"), window.actions)
                window.callback("close")
            worker.join(1)
        self.assertEqual(order, ["shell", "shutdown"])
        self.assertTrue(all("destroy" in window.actions for window in webview.windows))
        self.assertEqual(main.actions[-1], "destroy")
        self.assertFalse(any(isinstance(action, tuple) for window in webview.windows for action in window.actions))
        self.assertEqual(logger.events, [])

    def test_application_keep_editing_keeps_every_window_and_releases_all(self):
        """A late "cancel" of the second editor keeps all windows, starts no shutdown and releases both documents."""
        registry, webview, _, logger = registry_fixture()
        registry.open("alpha")
        registry.open("beta")
        for window in webview.windows:
            window.answer = None
        controller, main, order = main_controller(registry)
        with patch("dayz_serverman.host.editor_close.ACK_TIMEOUT", 0.03):
            worker = threading.Thread(target=controller._request_close, daemon=True)
            worker.start()
            for window, answer in zip(webview.windows, ("close", "cancel")):
                self.assertTrue(wait_until(lambda current=window: current.callback is not None))
                time.sleep(0.1)
                window.callback(answer)
            worker.join(1)
        self.assertEqual(order, ["shell"])
        for window in [main, *webview.windows]:
            self.assertNotIn("destroy", window.actions)
        for window in webview.windows:
            self.assertEqual(window.actions[-1], RELEASE)
        self.assertEqual([entry.state for entry in registry._entries.values()], ["OPEN", "OPEN"])
        self.assertEqual(registry.open("alpha"), {"opened": False, "focused": True})
        self.assertEqual(logger.events, [])

    def test_main_close_restores_a_minimized_editor_in_its_earlier_size(self):
        """QA-5.4-F2: restore or maximize a minimized editor, then show it, before its settle; never un-maximize."""
        registry, webview, profiles, _ = registry_fixture()
        profiles.names["gamma"] = "Gamma"
        for identifier in ("alpha", "beta", "gamma"):
            registry.open(identifier)
        alpha, beta, gamma = webview.windows
        alpha.events.minimized.fire()
        beta.events.maximized.fire()
        beta.events.minimized.fire()
        gamma.events.minimized.fire()
        gamma.events.restored.fire()
        gamma.events.maximized.fire()
        self.assertTrue(registry.settle_for_application())
        settle = SETTLE.format("application")
        self.assertEqual(alpha.actions, ["restore", "show", settle])
        self.assertEqual(beta.actions, ["maximize", "show", settle])
        self.assertEqual(gamma.actions, ["show", settle])

    def test_main_close_joins_an_open_editor_dialog(self):
        """QA-5.4-F6 and F7: the joining main close sends the join form once; the one answer ends both waits."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        window, entry = webview.windows[0], registry._entries["alpha"]
        window.answer = None
        self.assertFalse(window.events.closing.fire())
        self.assertTrue(wait_until(lambda: window.callback is not None))
        callback, results = window.callback, []
        worker = threading.Thread(target=lambda: results.append(registry.settle_for_application()), daemon=True)
        worker.start()
        self.assertTrue(wait_until(lambda: JOIN in window.actions))
        self.assertIs(window.callback, callback)
        self.assertTrue(worker.is_alive())
        callback("close")
        worker.join(1)
        entry.close._close_worker.join(1)
        self.assertEqual(results, [True])
        self.assertEqual(window.actions.count(JOIN), 1)
        self.assertNotIn(SETTLE.format("application"), window.actions)
        self.assertNotIn("destroy", window.actions)

    def test_a_join_during_the_cancel_answer_sends_only_the_join_form(self):
        """HP3: the main close joins while the editor settle publishes "cancel"; no new close request reaches the document."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        window, entry = webview.windows[0], registry._entries["alpha"]
        window.answer = None
        original, joined, results = entry.close._state_changed, threading.Event(), []

        def state_changed(state):
            """Start the main close while the owner publishes the answer, before the attempt ends."""
            if state == "OPEN" and not joined.is_set():
                joined.set()
                threading.Thread(target=lambda: results.append(registry.settle_for_application()), daemon=True).start()
                time.sleep(0.2)
            original(state)

        entry.close._state_changed = state_changed
        self.assertFalse(window.events.closing.fire())
        self.assertTrue(wait_until(lambda: window.callback is not None))
        window.callback("cancel")
        self.assertTrue(wait_until(lambda: bool(results), 5))
        self.assertEqual(results, [False])
        self.assertNotIn(SETTLE.format("application"), window.actions)
        self.assertIn(RELEASE, window.actions)

    def test_a_blocking_join_notice_does_not_hold_the_main_close(self):
        """H12: the notice runs on its own thread, so a document that does not return it cannot hold the answer."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        window = webview.windows[0]
        window.answer = None
        gate, original, results = threading.Event(), window.evaluate_js, []

        def evaluate(script, callback=None):
            """Block only the join notice until the test ends."""
            value = original(script, callback)
            if script == JOIN:
                gate.wait(5)
            return value

        window.evaluate_js = evaluate
        try:
            self.assertFalse(window.events.closing.fire())
            self.assertTrue(wait_until(lambda: window.callback is not None))
            threading.Thread(target=lambda: results.append(registry.settle_for_application()), daemon=True).start()
            self.assertTrue(wait_until(lambda: JOIN in window.actions))
            window.callback("close")
            self.assertTrue(wait_until(lambda: results == [True], 2))
        finally:
            gate.set()


if __name__ == "__main__":
    unittest.main()
