"""Shutdown and safe-close behavior tests for drain, veto, and probes."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import (  # noqa: E402
    OperationState,
    QueueUnavailable,
)
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.shutdown import ShutdownCoordinator, ShutdownState  # noqa: E402
from dayz_serverman.bridge.contracts import CONTRACT_VERSION  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.host.shutdown import SafeCloseController  # noqa: E402
from dayz_serverman.observability.structured_log import StructuredLogger  # noqa: E402


def request(method: str, parameters: dict[str, object]) -> dict[str, object]:
    """Build a bridge request envelope for the given method."""
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "shutdown-request",
        "method": method,
        "parameters": parameters,
    }


class FakeWindow:
    """Window double that records scripts and supports destruction."""
    def __init__(self, frontend_clean: bool = True) -> None:
        """Start with no scripts and an undestroyed window."""
        self.scripts: list[str] = []
        self.destroyed = threading.Event()
        self.frontend_clean = frontend_clean

    def evaluate_js(self, script: str) -> object:
        """Record the script and answer the frontend close probe."""
        self.scripts.append(script)
        if script == "window.ServerManTransitions.requestNativeClose()":
            return self.frontend_clean
        return None

    def destroy(self) -> None:
        """Mark the window as destroyed."""
        self.destroyed.set()


class FakeClosingEvent:
    """Closing-event double that fires registered handlers."""
    def __init__(self) -> None:
        """Start with no registered handlers."""
        self.handlers: list[object] = []

    def __iadd__(self, handler: object):
        """Register a handler and keep the event object."""
        self.handlers.append(handler)
        return self

    def fire(self) -> bool:
        """Fire every handler and report whether any vetoed."""
        return any(handler() is False for handler in self.handlers)


class NativeEventWindow(FakeWindow):
    """Window double whose native close fires the closing event."""
    def __init__(self, frontend_clean: bool = True) -> None:
        """Attach a closing event to the base window double."""
        super().__init__(frontend_clean)
        self.events = type("Events", (), {})()
        self.events.closing = FakeClosingEvent()

    def request_native_close(self) -> bool:
        """Fire the closing event and report the outcome."""
        return self.events.closing.fire()

    def destroy(self) -> None:
        """Destroy only when the closing event does not veto."""
        if not self.events.closing.fire():
            self.destroyed.set()


def wait_until(predicate, timeout: float = 1.0) -> bool:
    """Poll the predicate until it holds or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class ShutdownBehaviorTests(unittest.TestCase):
    """Shutdown coordinator and safe-close controller contracts."""
    def setUp(self) -> None:
        """Create an operation manager and shutdown coordinator per test."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_shutdown_")
        self.root = Path(self.temporary.name)
        self.logger = StructuredLogger(self.root / "logs" / "manager.jsonl")
        self.operations = OperationManager(OperationStore(self.root / "operations"), logger=self.logger)
        self.shutdown = ShutdownCoordinator(self.operations, self.logger)

    def tearDown(self) -> None:
        """Shut down operations and remove the temporary tree."""
        self.operations.shutdown(2)
        self.temporary.cleanup()

    def test_drain_cancels_queue_rejects_new_work_and_waits_for_unsafe_active(self) -> None:
        """Drain cancels queued work, rejects new work, and waits for active work."""
        started = threading.Event()
        release = threading.Event()
        active = self.operations.submit(
            "UNSAFE_ACTIVE",
            lambda _context: (started.set(), release.wait(2), {"done": True})[-1],
        )
        self.assertTrue(started.wait(1))
        queued = self.operations.submit("QUEUED", lambda _context: {})

        # Shutdown cancels the queued item and waits for the active one
        snapshot = self.shutdown.request_shutdown()
        self.assertEqual(snapshot.state, ShutdownState.WAITING_FOR_SAFE_POINT)
        self.assertEqual(self.operations.get(queued.operation_id).state, OperationState.CANCELLED)
        self.assertEqual(self.operations.get(active.operation_id).state, OperationState.RUNNING)
        with self.assertRaises(QueueUnavailable):
            self.operations.submit("REJECTED", lambda _context: {})
        self.assertGreater(len(self.operations.read_events(0)[0]), 0)

        # The active work finishing lets the drain complete
        release.set()
        self.assertEqual(self.shutdown.wait_for_close(2).state, ShutdownState.CLOSED)
        self.assertEqual(self.operations.get(active.operation_id).state, OperationState.SUCCEEDED)

    def test_drain_requests_active_cancellation_only_at_declared_safe_point(self) -> None:
        """Drain requests active cancellation only at a declared safe point."""
        started = threading.Event()
        release = threading.Event()

        def work(context):
            """Block until released, then reach the declared safe point."""
            started.set()
            release.wait(2)
            context.checkpoint("between-items", 50)
            return {}

        operation = self.operations.submit(
            "SAFE_ACTIVE",
            work,
            safe_points=frozenset({"between-items"}),
        )
        self.assertTrue(started.wait(1))
        # The active run is flagged for cancellation at its safe point
        self.shutdown.request_shutdown()
        self.assertEqual(self.operations.get(operation.operation_id).state, OperationState.CANCELLING)
        release.set()
        self.assertEqual(self.shutdown.wait_for_close(2).state, ShutdownState.CLOSED)
        self.assertEqual(self.operations.get(operation.operation_id).state, OperationState.CANCELLED)

    def test_native_close_is_deferred_until_active_work_finishes(self) -> None:
        """A native close is deferred until active work finishes."""
        started = threading.Event()
        release = threading.Event()
        self.operations.submit(
            "CLOSE_FIXTURE",
            lambda _context: (started.set(), release.wait(2), {})[-1],
        )
        self.assertTrue(started.wait(1))
        # Defer the native close while the fixture operation runs
        window = FakeWindow()
        controller = SafeCloseController(window, self.shutdown)
        self.assertFalse(controller.on_closing())
        self.assertTrue(wait_until(
            lambda: any("WAITING_FOR_SAFE_POINT" in script for script in window.scripts)
        ))
        release.set()
        self.assertTrue(window.destroyed.wait(2))
        self.assertEqual(self.shutdown.snapshot().state, ShutdownState.CLOSED)

    def test_managed_server_guard_keeps_normal_close_fail_closed(self) -> None:
        """A managed server guard keeps the normal close fail closed."""
        safe = False
        guarded = ShutdownCoordinator(self.operations, self.logger, lambda: safe)
        blocked = guarded.request_shutdown()
        self.assertEqual(blocked.state, ShutdownState.WAITING_FOR_SAFE_POINT)
        self.assertEqual(blocked.blocking_reason, "MANAGED_SERVER_ACTIVE")
        safe = True
        self.assertEqual(guarded.snapshot().state, ShutdownState.CLOSED)

    def test_dirty_native_close_does_not_start_backend_drain(self) -> None:
        """A dirty frontend veto does not start the backend drain."""
        # A dirty frontend vetoes the close before any drain
        window = FakeWindow(frontend_clean=False)
        controller = SafeCloseController(window, self.shutdown)
        self.assertFalse(controller.on_closing())
        self.assertTrue(wait_until(lambda: bool(window.scripts)))
        self.assertEqual(self.shutdown.snapshot().state, ShutdownState.OPEN)
        self.assertEqual(window.scripts, ["window.ServerManTransitions.requestNativeClose()"])
        self.assertFalse(window.destroyed.is_set())
        self.assertFalse(controller.on_closing())
        self.assertTrue(wait_until(lambda: len(window.scripts) == 2))
        self.assertEqual(self.shutdown.snapshot().state, ShutdownState.OPEN)
        # After the frontend turns clean the close proceeds
        window.frontend_clean = True
        self.assertFalse(controller.on_closing())
        self.assertTrue(window.destroyed.wait(1))
        self.assertEqual(self.shutdown.snapshot().state, ShutdownState.CLOSED)

    def test_native_event_vetoes_then_reenters_only_after_safe_approval(self) -> None:
        """The closing event vetoes once, then re-entry succeeds after approval."""
        window = NativeEventWindow()
        controller = SafeCloseController(window, self.shutdown)
        window.events.closing += controller.on_closing

        # The vetoed close defers until the backend reaches a safe point
        self.assertTrue(window.request_native_close())
        self.assertTrue(window.destroyed.wait(1))
        self.assertEqual(self.shutdown.snapshot().state, ShutdownState.CLOSED)
        self.assertEqual(
            window.scripts.count("window.ServerManTransitions.requestNativeClose()"), 1,
        )

    def test_native_event_dirty_veto_does_not_drain_or_destroy(self) -> None:
        """The dirty veto never drains or destroys the window."""
        window = NativeEventWindow(frontend_clean=False)
        controller = SafeCloseController(window, self.shutdown)
        window.events.closing += controller.on_closing

        # The dirty veto leaves the window alive without a drain
        self.assertTrue(window.request_native_close())
        self.assertTrue(wait_until(lambda: bool(window.scripts)))
        self.assertEqual(self.shutdown.snapshot().state, ShutdownState.OPEN)
        self.assertFalse(window.destroyed.is_set())

    def test_frontend_close_probe_failure_fails_closed_without_drain(self) -> None:
        """A failing frontend probe vetoes the close without draining."""
        class BrokenWindow(FakeWindow):
            """Window double whose script evaluation always fails."""
            def evaluate_js(self, _script: str) -> object:
                """Fail the frontend probe with a synthetic browser error."""
                raise RuntimeError("synthetic browser failure")
        controller = SafeCloseController(BrokenWindow(), self.shutdown)
        self.assertFalse(controller.on_closing())
        self.assertEqual(self.shutdown.snapshot().state, ShutdownState.OPEN)

    def test_smoke_close_drains_then_bypasses_frontend_probe_once(self) -> None:
        """Smoke close drains, then bypasses the frontend probe once."""
        window = FakeWindow(frontend_clean=False)
        controller = SafeCloseController(window, self.shutdown)
        # The smoke close drains and destroys without probing again
        self.assertTrue(controller.close_after_shutdown(1))
        self.assertTrue(window.destroyed.is_set())
        self.assertEqual(self.shutdown.snapshot().state, ShutdownState.CLOSED)
        self.assertTrue(controller.on_closing())
        self.assertNotIn(
            "window.ServerManTransitions.requestNativeClose()", window.scripts,
        )

    def test_smoke_close_never_destroys_before_safe_drain(self) -> None:
        """Smoke close never destroys the window before a safe drain."""
        started = threading.Event(); release = threading.Event()
        self.operations.submit(
            "SMOKE_DRAIN_FIXTURE",
            lambda _context: (started.set(), release.wait(2), {})[-1],
        )
        self.assertTrue(started.wait(1))
        window = FakeWindow()
        controller = SafeCloseController(window, self.shutdown)
        # The first attempt must fail while the fixture operation runs
        self.assertFalse(controller.close_after_shutdown(0.01))
        self.assertFalse(window.destroyed.is_set())
        release.set()
        self.assertTrue(controller.close_after_shutdown(1))
        self.assertTrue(window.destroyed.is_set())

    def test_bridge_queries_remain_available_and_controls_are_narrow_during_drain(self) -> None:
        """Bridge queries stay available and controls narrow during a drain."""
        composition = build_composition(self.root / "manager")
        started = threading.Event()
        release = threading.Event()
        composition.operations.submit(
            "BRIDGE_DRAIN_FIXTURE",
            lambda _context: (started.set(), release.wait(2), {})[-1],
        )
        self.assertTrue(started.wait(1))
        try:
            # Queries stay available while mutations are rejected
            closing = composition.bridge.dispatch(request("request_shutdown", {}))
            self.assertEqual(closing["value"]["state"], "WAITING_FOR_SAFE_POINT")
            self.assertTrue(composition.bridge.dispatch(request("get_application_snapshot", {}))["success"])
            self.assertTrue(composition.bridge.dispatch(
                request("read_operation_events", {"after_sequence": 0, "maximum": 100})
            )["success"])
            rejected = composition.bridge.dispatch(request("save_settings", {"expected_revision": None}))
            self.assertEqual(rejected["error"]["code"], "MUTATION_CONFLICT")
            # Malformed and unknown control requests fail narrowly
            extra = composition.bridge.dispatch(request("request_shutdown", {"force": True}))
            malformed = composition.bridge.dispatch(
                request("request_operation_cancellation", {"operation_id": "<bad>"})
            )
            unknown = composition.bridge.dispatch(
                request("request_operation_cancellation", {"operation_id": "unknown-operation"})
            )
            self.assertEqual(extra["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(malformed["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(unknown["error"]["code"], "NOT_FOUND")
        finally:
            release.set()
            composition.shutdown.wait_for_close(2)


if __name__ == "__main__":
    unittest.main()
