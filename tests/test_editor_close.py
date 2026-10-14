"""Promise settlement, editor cancellation and application-close ordering."""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dayz_serverman.application.shutdown import ShutdownState
from dayz_serverman.host.editor_close import EditorCloseController
from dayz_serverman.host.api import HostApi
from dayz_serverman.host.shutdown import SafeCloseController
from tests.test_editor_windows import FakeEditor, FakeLogger, registry_fixture


def wait_until(predicate, seconds=1):
    """Bound test orchestration while production final-answer waits remain unlimited."""
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    return predicate()


class EditorCloseTests(unittest.TestCase):
    """Partition the document handshake by acknowledgement and final answer."""

    def make_controller(self):
        """Return an isolated loaded editor, controller and observable state transitions."""
        window, logger, states, requests = FakeEditor(), FakeLogger(), [], []
        controller = EditorCloseController(window, "alpha", "Alpha", logger,
                                           states.append, lambda: requests.append("close"))
        return controller, window, logger, states, requests

    def test_loaded_close_stays_read_only_until_release(self):
        """Publish settling and closing, then release the document on cancellation."""
        controller, window, _, states, _ = self.make_controller()
        self.assertEqual(controller.settle("application"), "close")
        self.assertEqual(states, ["SETTLING", "CLOSING"])
        self.assertIn("window.ServerManMapEditor.settleForClose('application')", window.actions)
        controller.release_close()
        self.assertEqual(window.actions[-1], "window.ServerManMapEditor.releaseClose()")
        self.assertEqual(states[-1], "OPEN")

    def test_unloaded_editor_needs_no_script_or_confirmation(self):
        """A document that has not loaded cannot contain an edit."""
        controller, window, logger, _, _ = self.make_controller()
        window.events.loaded.clear()
        self.assertEqual(controller.settle("editor"), "close")
        self.assertEqual(window.actions, [])
        self.assertEqual(logger.events, [])

    def test_native_close_veto_reuses_worker_without_shutdown(self):
        """Native closing asks only its own document and reuses an in-flight worker."""
        controller, window, _, _, requests = self.make_controller()
        window.answer = None
        self.assertFalse(controller.on_closing())
        self.assertTrue(wait_until(lambda: window.callback is not None))
        worker = controller._close_worker
        self.assertFalse(controller.on_closing())
        self.assertIs(worker, controller._close_worker)
        window.callback("close")
        worker.join(1)
        self.assertEqual(requests, ["close"])
        self.assertFalse(hasattr(controller, "_shutdown"))

    def test_final_answer_outlives_ack_deadline_and_is_shared(self):
        """Acknowledged dialogs have no deadline and a second settle shares their answer."""
        controller, window, logger, states, _ = self.make_controller()
        window.answer = None
        answers, join = [], "window.ServerManMapEditor.settleForClose('application', true)"
        # A 1 s limit that the test waits out: a late helper thread under machine load stays far inside it (QA-5.4-F10)
        with patch("dayz_serverman.host.editor_close.ACK_TIMEOUT", 1.0):
            first = threading.Thread(target=lambda: answers.append(controller.settle("editor")), daemon=True)
            first.start()
            self.assertTrue(wait_until(lambda: window.callback is not None, 5))
            second = threading.Thread(target=lambda: answers.append(controller.settle("application")), daemon=True)
            second.start()
            notified = wait_until(lambda: join in window.actions, 5)
            time.sleep(1.2)
            alive, actions = (first.is_alive(), second.is_alive()), list(window.actions)
            window.callback("cancel")
            first.join(5)
            second.join(5)
        self.assertTrue(notified)
        self.assertEqual(alive, (True, True))
        # The joining application close tells the document once, in the join form, without a second callback wait
        self.assertEqual(actions, ["window.ServerManMapEditor.settleForClose('editor')", join])
        self.assertEqual(answers, ["cancel", "cancel"])
        self.assertEqual(states, ["SETTLING", "OPEN"])
        self.assertEqual(logger.events, [])

    def test_unknown_ack_error_and_unknown_callback_use_owned_confirmation(self):
        """Only exact True acknowledges; an invalid final result needs administrator consent."""
        cases = [(1, "close", False, "unknown_acknowledgement"),
                 (None, "close", False, "unknown_acknowledgement"),
                 (True, "close", True, "evaluation_error"),
                 (True, {"unknown": True}, False, "unknown_callback")]
        for ack, answer, error, cause in cases:
            with self.subTest(cause=cause, ack=ack):
                controller, window, logger, _, _ = self.make_controller()
                window.ack, window.answer, window.error = ack, answer, error
                window.confirmation = False
                self.assertEqual(controller.settle("application"), "cancel")
                self.assertEqual(window.actions[-1], ("DayZ-ServerMan",
                    "The map editor for Alpha does not respond. "
                    "Changes that are not saved may be lost. Close it?"))
                self.assertEqual(logger.events, [("map_editor.settle_failed", {
                    "level": "WARNING", "fields": {"profile_id": "alpha", "cause": cause}})])

    def test_ack_timeout_uses_confirmation_and_ignores_late_callback(self):
        """An unresponsive evaluator must not hold the five-second acknowledgement deadline."""
        controller, window, logger, _, _ = self.make_controller()
        window.evaluate_gate = threading.Event()
        try:
            with patch("dayz_serverman.host.editor_close.ACK_TIMEOUT", 0.03):
                started = time.monotonic()
                self.assertEqual(controller.settle("editor"), "close")
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertEqual(logger.events[0][1]["fields"]["cause"], "acknowledgement_timeout")
        finally:
            window.evaluate_gate.set()

    def test_closed_ends_ack_or_final_answer_wait(self):
        """Native closure releases both handshake waits without a dialog."""
        for block_ack in (False, True):
            with self.subTest(block_ack=block_ack):
                controller, window, logger, _, _ = self.make_controller()
                window.answer = None
                if block_ack:
                    window.evaluate_gate = threading.Event()
                answers = []
                worker = threading.Thread(target=lambda: answers.append(controller.settle("application")))
                worker.start()
                self.assertTrue(wait_until(lambda: window.callback is not None))
                controller.on_closed()
                worker.join(1)
                if window.evaluate_gate is not None:
                    window.evaluate_gate.set()
                self.assertFalse(worker.is_alive())
                self.assertEqual(answers, ["close"])
                self.assertEqual(logger.events, [])


class ApplicationEditorCloseTests(unittest.TestCase):
    """Protect shell-first ordering and the all-editor cancellation boundary."""

    def make_main(self, registry, clean=True):
        """Create an observable shutdown service and shell without backend operations."""
        main, order = FakeEditor(), []
        original = main.evaluate_js
        def evaluate(script, callback=None):
            """Record the shell guard before any editor settlement."""
            if script == "window.ServerManTransitions.requestNativeClose()":
                order.append("shell")
                return clean
            return original(script, callback)
        main.evaluate_js = evaluate
        def shutdown():
            """Record the backend shutdown boundary."""
            order.append("shutdown")
            return SimpleNamespace(state=ShutdownState.CLOSED, to_dict=lambda: {})
        service = SimpleNamespace(request_shutdown=shutdown)
        return SafeCloseController(main, service, editors=registry), main, order

    def test_shell_cancel_leaves_editors_and_shutdown_untouched(self):
        """A shell refusal must precede even begin_close."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        controller, main, order = self.make_main(registry, False)
        controller._request_close()
        self.assertEqual(order, ["shell"])
        self.assertEqual(webview.windows[0].actions, [])
        self.assertFalse(registry._closing)
        self.assertNotIn("destroy", main.actions)

    def test_editor_cancel_releases_all_without_any_destruction(self):
        """A later editor cancellation restores earlier closing documents and allows opening."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        registry.open("beta")
        webview.windows[1].answer = "cancel"
        controller, main, order = self.make_main(registry)
        controller._request_close()
        self.assertEqual(order, ["shell"])
        self.assertFalse(registry._closing)
        for window in [main, *webview.windows]:
            self.assertNotIn("destroy", window.actions)
        for entry in registry._entries.values():
            self.assertEqual(entry.state, "OPEN")
            self.assertIn("window.ServerManMapEditor.releaseClose()", entry.window.actions)

    def test_approved_close_destroys_editors_before_shutdown_and_main(self):
        """Settle in opening order and destroy every editor before backend shutdown."""
        registry, webview, _, _ = registry_fixture()
        registry.open("beta")
        registry.open("alpha")
        controller, main, order = self.make_main(registry)
        original_shutdown = controller._shutdown.request_shutdown
        def check_shutdown():
            """Assert the ordering at the actual shutdown call boundary."""
            self.assertTrue(all("destroy" in w.actions for w in webview.windows))
            self.assertFalse(registry._entries)
            return original_shutdown()
        controller._shutdown.request_shutdown = check_shutdown
        controller._request_close()
        self.assertEqual(order, ["shell", "shutdown"])
        self.assertEqual(main.actions[-1], "destroy")
        self.assertTrue(registry._closing)

    def test_application_settle_keeps_opening_order_and_refuses_new_editor(self):
        """Use opening order rather than profile sorting and retain the close barrier."""
        registry, webview, _, _ = registry_fixture()
        registry.open("beta")
        registry.open("alpha")
        order = []
        for entry in registry._entries.values():
            original = entry.close.settle
            def settle(reason, bound=entry, method=original):
                """Observe ordering at each real settlement boundary."""
                order.append(bound.close.profile_id)
                self.assertTrue(registry._closing)
                return method(reason)
            entry.close.settle = settle
        self.assertTrue(registry.settle_for_application())
        self.assertEqual(order, ["beta", "alpha"])
        api = HostApi(None)
        api._set_map_editor_opener(registry.open)
        self.assertEqual(api.open_map_editor("alpha")["error"]["details"]["reason"], "application_closing")
        self.assertEqual(len(webview.windows), 2)

    def test_native_request_cannot_destroy_a_cancelled_application_generation(self):
        """A native request waiting on the application settle cannot destroy after release."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        entry = registry._entries["alpha"]
        registry.begin_close()
        self.assertEqual(entry.close.settle("application"), "close")
        entry.close._close_requested()
        self.assertNotIn("destroy", webview.windows[0].actions)
        registry.end_close()
        entry.close._close_requested()
        self.assertNotIn("destroy", webview.windows[0].actions)

    def test_destroy_timeout_logs_and_continues(self):
        """A missing native closed event must not stop destruction of the next editor."""
        registry, webview, _, logger = registry_fixture()
        registry.open("alpha")
        registry.open("beta")
        webview.windows[0].send_closed = False
        with patch("dayz_serverman.host.editor_windows.DESTROY_TIMEOUT", 0.03):
            registry.destroy_all()
        self.assertIn("destroy", webview.windows[1].actions)
        self.assertEqual(logger.events, [("map_editor.destroy_timeout", {
            "level": "WARNING", "fields": {"profile_id": "alpha"}})])

    def test_smoke_destroy_skips_all_document_settling(self):
        """Owned smoke cleanup destroys editors even when their documents would cancel."""
        registry, webview, _, _ = registry_fixture()
        registry.open("alpha")
        webview.windows[0].answer = "cancel"
        controller, _, order = self.make_main(registry)
        self.assertTrue(controller.close_after_shutdown(1))
        self.assertEqual(webview.windows[0].actions, ["destroy"])
        self.assertEqual(order, ["shutdown"])
