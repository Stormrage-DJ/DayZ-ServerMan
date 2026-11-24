"""Crash restart bridge (plan 2.2): the owner cancel method and the cancel on an operator start or restart."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.crash_restart_support import Rig

from dayz_serverman.application.crash_restart_bridge import CrashRestartHandlers  # noqa: E402
from dayz_serverman.bridge.contracts import ErrorCode  # noqa: E402
from dayz_serverman.bridge.facade import ApplicationCallError  # noqa: E402
from dayz_serverman.bridge_composition import OBSERVER_READ_METHODS, OWNER_ONLY_METHODS  # noqa: E402
from dayz_serverman.cli.registry import EXCLUDED_METHODS, EXCLUSION_REASONS  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402
from dayz_serverman.session_observer import open_observer_session  # noqa: E402


class ScriptedSource:
    """Handler source with the four lifecycle handlers; each call appends its name to calls."""

    def __init__(self, flow=None, refuse: Exception | None = None) -> None:
        """Build the ordered table. With a flow, each call also records the flow state; refuse fails start_server."""
        self.calls: list[str] = []
        self.seen: list[object] = []
        self._flow = flow
        self._refuse = refuse
        self.table = {name: self._make(name) for name in
                      ("get_server_status", "start_server", "stop_server", "restart_server")}

    def _make(self, name: str):
        """Return a handler that records its name and returns an empty result."""
        def handler(parameters):
            """Record the call, and the flow state when a flow is bound."""
            del parameters
            self.calls.append(name)
            if self._flow is not None:
                self.seen.append(self._flow.read())
            if name == "start_server" and self._refuse is not None:
                raise self._refuse
            return {}
        return handler

    def handlers(self) -> dict:
        """Return the table."""
        return dict(self.table)


def pending_rig() -> Rig:
    """Return a rig whose READY launch ran and ended, so a restart is pending."""
    rig = Rig()
    rig.running()
    rig.at(0)
    rig.ended()
    rig.at(5)
    return rig


class CancelHandlerTests(unittest.TestCase):
    """The cancel method follows criteria 2 and 8."""

    def setUp(self) -> None:
        """Build a rig with a pending restart and the handler table over it."""
        self.rig = pending_rig()
        self.source = ScriptedSource()
        self.table = CrashRestartHandlers(self.source, self.rig.flow).handlers()

    def test_cancel_crash_restart_cancels_a_pending_restart(self) -> None:
        """A pending restart is cancelled; no restart follows."""
        self.assertEqual(self.table["cancel_crash_restart"]({}), {"cancelled": True})
        self.assertIsNone(self.rig.flow.read())
        for seconds in (10, 15, 20):
            self.rig.at(seconds)
        self.assertEqual(self.rig.lane.submits, [])
        self.assertEqual(self.rig.lifecycle.state, ServerState.STOPPED)

    def test_cancel_with_nothing_pending_returns_false(self) -> None:
        """Nothing pending gives False without an error."""
        self.assertEqual(self.table["cancel_crash_restart"]({}), {"cancelled": True})
        self.assertEqual(self.table["cancel_crash_restart"]({}), {"cancelled": False})
        idle = CrashRestartHandlers(ScriptedSource(), Rig().flow).handlers()
        self.assertEqual(idle["cancel_crash_restart"]({}), {"cancelled": False})

    def test_cancel_takes_no_parameters(self) -> None:
        """A parameter is refused and cancels nothing."""
        with self.assertRaises(ApplicationCallError) as caught:
            self.table["cancel_crash_restart"]({"x": 1})
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_REQUEST)
        self.assertEqual(self.rig.flow.read().kind, "pending")

    def test_the_table_keeps_the_order_and_passes_other_handlers_through(self) -> None:
        """The cancel method sits after restart_server; stop is the same object."""
        self.assertEqual(
            list(self.table),
            ["get_server_status", "start_server", "stop_server", "restart_server", "cancel_crash_restart"],
        )
        self.assertIs(self.table["stop_server"], self.source.table["stop_server"])


class OperatorPrecedenceTests(unittest.TestCase):
    """An operator start or restart cancels a pending crash restart first; stop does not."""

    def build(self, refuse: Exception | None = None) -> None:
        """Build a rig with a pending restart and a table whose source sees the flow state."""
        self.rig = pending_rig()
        self.source = ScriptedSource(self.rig.flow, refuse)
        self.table = CrashRestartHandlers(self.source, self.rig.flow).handlers()

    def test_start_during_the_wait_cancels_the_restart(self) -> None:
        """The cancel comes before the start handler; no crash restart follows."""
        self.build()
        self.table["start_server"]({})
        self.assertEqual(self.source.calls, ["start_server"])
        self.assertEqual(self.source.seen, [None])
        self.rig.at(10)
        self.rig.at(15)
        self.assertEqual(self.rig.lane.submits, [])

    def test_restart_during_the_wait_cancels_the_restart(self) -> None:
        """The same holds for an operator restart."""
        self.build()
        self.table["restart_server"]({})
        self.assertEqual(self.source.calls, ["restart_server"])
        self.assertEqual(self.source.seen, [None])
        self.rig.at(10)
        self.rig.at(15)
        self.assertEqual(self.rig.lane.submits, [])

    def test_a_refused_start_still_cancels(self) -> None:
        """A start that the handler refuses still cancels the pending restart."""
        error = ApplicationCallError(ErrorCode.REVISION_CONFLICT, "stale")
        self.build(refuse=error)
        with self.assertRaises(ApplicationCallError) as caught:
            self.table["start_server"]({})
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.rig.flow.read())

    def test_stop_server_does_not_cancel(self) -> None:
        """A stop leaves the pending restart alone."""
        self.build()
        self.table["stop_server"]({})
        self.assertEqual(self.source.calls, ["stop_server"])
        self.assertEqual(self.rig.flow.read().kind, "pending")


class SessionTests(unittest.TestCase):
    """The method belongs to the owner session only and is not a command."""

    def setUp(self) -> None:
        """Make a disposable manager root below a temporary folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_crash_sessions_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Manager"
        self.request = {
            "contract_version": 1, "request_id": "x", "method": "cancel_crash_restart", "parameters": {},
        }

    def test_the_owner_answers_with_nothing_cancelled(self) -> None:
        """A real owner composition has nothing pending."""
        owner = build_composition(self.root)
        self.addCleanup(owner.operations.shutdown, 2)
        answer = owner.bridge.dispatch(self.request)
        self.assertTrue(answer["success"])
        self.assertEqual(answer["value"], {"cancelled": False})

    def test_an_observer_session_refuses_the_method(self) -> None:
        """An observer holds no cancel method and creates no folder."""
        session = open_observer_session(self.root)
        self.addCleanup(session.close)
        answer = session.composition.bridge.dispatch(self.request)
        self.assertEqual(answer["error"]["code"], "INVALID_REQUEST")
        with self.assertRaises(ValueError):
            session.call("cancel_crash_restart", {})
        self.assertFalse(self.root.exists())

    def test_the_method_is_owner_only_and_not_a_command(self) -> None:
        """The method is an owner method and is excluded from the CLI with a reason."""
        self.assertIn("cancel_crash_restart", OWNER_ONLY_METHODS)
        self.assertNotIn("cancel_crash_restart", OBSERVER_READ_METHODS)
        self.assertIn("cancel_crash_restart", EXCLUDED_METHODS)
        self.assertTrue(EXCLUSION_REASONS["cancel_crash_restart"].strip())


if __name__ == "__main__":
    unittest.main()
