"""Crash restart status field (plan 2.3): get_server_status carries the additive auto_restart field."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.crash_restart_support import WALL_TEXT, Rig

from dayz_serverman.application.crash_restart_bridge import CrashRestartHandlers  # noqa: E402
from dayz_serverman.application.phase_wording import server_status_text  # noqa: E402
from dayz_serverman.bridge.contracts import ErrorCode  # noqa: E402
from dayz_serverman.bridge.facade import ApplicationCallError  # noqa: E402
from dayz_serverman.cli.commands.status import players_line  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.session_observer import open_observer_session  # noqa: E402

# The status reply of a stopped server before the field existed, as captured in tests/test_lifecycle_bridge.py
OLD_REPLY = {
    "state": "STOPPED", "process_id": None, "diagnostic_code": None, "readiness": None,
    "query_port": None, "profile_id": None, "started_at": None, "players": None, "max_players": None,
}


class ScriptedStatusSource:
    """Handler source whose status handler returns the old reply, or raises the scripted error."""

    def __init__(self, error: Exception | None = None) -> None:
        """Keep the error that the status handler raises."""
        self._error = error

    def handlers(self) -> dict:
        """Return the status handler and a restart handler, so that the cancel method is added."""
        def status(parameters):
            """Return a copy of the old reply, or raise."""
            del parameters
            if self._error is not None:
                raise self._error
            return dict(OLD_REPLY)
        return {"get_server_status": status, "restart_server": lambda parameters: {}}


class StatusFieldTests(unittest.TestCase):
    """The field follows the flow state; the other status fields stay as they were."""

    def setUp(self) -> None:
        """Build a rig and the wrapped status handler."""
        self.rig = Rig()
        self.table = CrashRestartHandlers(ScriptedStatusSource(), self.rig.flow).handlers()
        self.status = self.table["get_server_status"]

    def pending(self) -> None:
        """Run the polls to a pending restart: the launch ran at 0 s and the end is seen at 5 s."""
        self.rig.running()
        self.rig.at(0)
        self.rig.ended()
        self.rig.at(5)

    def test_no_crash_gives_a_null_field_and_keeps_the_other_fields(self) -> None:
        """Without a crash the field is null and the reply is the old reply plus the field."""
        self.assertEqual(self.status({}), {**OLD_REPLY, "auto_restart": None})

    def test_during_the_wait_the_kind_is_pending(self) -> None:
        """A pending restart shows kind pending without a reason."""
        self.pending()
        self.assertEqual(
            self.status({}), {**OLD_REPLY, "auto_restart": {"kind": "pending", "reason": None, "at": WALL_TEXT}},
        )

    def test_after_the_restart_the_kind_is_restarted(self) -> None:
        """A new launch after the crash start shows kind restarted."""
        self.pending()
        self.rig.at(10)
        self.rig.at(15)
        self.rig.running(token="tok-b", handle="h-b", ready=False)
        self.rig.at(20)
        self.assertEqual(
            self.status({})["auto_restart"], {"kind": "restarted", "reason": None, "at": WALL_TEXT},
        )

    def gave_up(self) -> None:
        """Run the polls to the loop guard: three restarts in the last 10 minutes refuse the next one."""
        self.rig.flow._restart_times = [100.0, 200.0, 300.0]
        self.rig.running()
        self.rig.at(380)
        self.rig.ended()
        self.rig.at(400)

    def test_after_the_guard_the_kind_is_gave_up(self) -> None:
        """A refused restart shows kind gave_up with the reason."""
        self.gave_up()
        self.assertEqual(
            self.status({})["auto_restart"], {"kind": "gave_up", "reason": "guard_reached", "at": WALL_TEXT},
        )

    def test_a_finished_notice_stays_until_the_next_crash(self) -> None:
        """A cancel with nothing pending leaves the gave_up notice as it is."""
        self.gave_up()
        self.assertEqual(self.status({})["auto_restart"]["kind"], "gave_up")
        self.assertEqual(self.table["cancel_crash_restart"]({}), {"cancelled": False})
        self.assertEqual(
            self.status({})["auto_restart"], {"kind": "gave_up", "reason": "guard_reached", "at": WALL_TEXT},
        )

    def test_an_error_of_the_status_handler_passes_through(self) -> None:
        """The wrapper adds nothing to an error of the source handler."""
        error = ApplicationCallError(ErrorCode.INVALID_REQUEST, "bad")
        table = CrashRestartHandlers(ScriptedStatusSource(error), self.rig.flow).handlers()
        with self.assertRaises(ApplicationCallError) as caught:
            table["get_server_status"]({})
        self.assertIs(caught.exception, error)


class StatusSessionTests(unittest.TestCase):
    """A real owner composition and an observer session answer with a null field."""

    def setUp(self) -> None:
        """Make a disposable manager root below a temporary folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_crash_status_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Manager"

    def test_the_owner_reply_has_the_field_and_the_old_keys(self) -> None:
        """The owner reply keeps every old key and adds auto_restart with null."""
        owner = build_composition(self.root)
        self.addCleanup(owner.operations.shutdown, 2)
        answer = owner.bridge.dispatch(
            {"contract_version": 1, "request_id": "x", "method": "get_server_status", "parameters": {}})
        self.assertTrue(answer["success"])
        self.assertIsNone(answer["value"]["auto_restart"])
        self.assertEqual(set(answer["value"]), set(OLD_REPLY) | {"auto_restart"})

    def test_an_observer_sees_null(self) -> None:
        """An observer session has no crash restart state to show."""
        session = open_observer_session(self.root)
        self.addCleanup(session.close)
        reply = session.call("get_server_status", {})
        self.assertIn("auto_restart", reply)
        self.assertIsNone(reply["auto_restart"])


class OldReaderTests(unittest.TestCase):
    """The Python readers of the status reply ignore the new key."""

    def test_the_readers_accept_an_earlier_reply(self) -> None:
        """The text and the players line do not change with the new field."""
        replies = (
            OLD_REPLY,
            {**OLD_REPLY, "auto_restart": None},
            {**OLD_REPLY, "auto_restart": {"kind": "pending", "reason": None, "at": WALL_TEXT}},
        )
        for reply in replies:
            with self.subTest(reply=reply):
                self.assertEqual(server_status_text(reply), server_status_text(OLD_REPLY))
                self.assertEqual(players_line(reply), players_line(OLD_REPLY))


if __name__ == "__main__":
    unittest.main()
