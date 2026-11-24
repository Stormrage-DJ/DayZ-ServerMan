"""Crash scenarios (plan 3.2): the acceptance criteria over the real lifecycle, ownership and lane."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.crash_restart_bridge import CrashRestartHandlers  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402
from tests.crash_scenario_support import ScenarioCase  # noqa: E402
from tests.test_server_ownership import ADOPT_NS, PROFILE, Session  # noqa: E402


class CrashScenarioTests(ScenarioCase):
    """Timelines in seconds are typed by hand from the criteria."""

    def test_a_ready_server_that_ends_outside_the_manager_is_restarted(self) -> None:
        """Criteria 1 and 2: Stopped with a pending restart, then one start and the restarted state."""
        session = self.launched()
        chain = self.chain(session)
        self.ready(session)
        chain.at(0)
        self.crash(session)
        chain.at(5)
        self.assertEqual(session.readiness.status().state, ServerState.STOPPED)
        self.assertEqual(chain.flow.read().kind, "pending")
        chain.at(10)
        self.assertEqual(len(session.launcher.requests), 1)
        chain.at(15)
        chain.settle()
        self.assertEqual(len(session.launcher.requests), 2)
        chain.at(20)
        self.assertEqual(chain.flow.read().kind, "restarted")
        self.assertEqual(session.readiness.status().state, ServerState.RUNNING_MANAGED)
        self.assertEqual(chain.logger.events(), ["server_crash.restart_pending", "server_crash.restarted"])

    def test_a_server_adopted_by_the_open_window_is_restarted(self) -> None:
        """Criterion 1: a server that another session started and this window adopted is restarted."""
        first = self.launched()
        second = Session(self, "b", wall_ns=ADOPT_NS)
        self.ready(second)
        chain = self.chain(second)
        chain.at(0)
        self.assertIsNotNone(second.ownership.adopted_launch())
        self.crash(second)
        for seconds in (5, 10, 15):
            chain.at(seconds)
        chain.settle()
        chain.at(20)
        self.assertEqual(len(second.launcher.requests), 1)
        self.assertEqual(len(first.launcher.requests), 1)
        self.assertEqual(chain.flow.read().kind, "restarted")

    def cycle(self, session: Session, chain, start: float) -> None:
        """One crash and its restart: READY, a crash at start + 5 s, and the restart at start + 15 s."""
        self.ready(session)
        chain.at(start)
        self.crash(session)
        for seconds in (5, 10, 15):
            chain.at(start + seconds)
        chain.settle()

    def test_the_guard_leaves_the_fourth_crash_stopped(self) -> None:
        """Criterion 4: three restarts in 10 minutes are allowed; the fourth crash stays stopped."""
        session = self.launched()
        chain = self.chain(session)
        for start in (0, 40, 80):
            self.cycle(session, chain, start)
        self.assertEqual(len(session.launcher.requests), 4)
        self.ready(session)
        chain.at(120)
        self.crash(session)
        chain.at(125)
        state = chain.flow.read()
        self.assertEqual((state.kind, state.reason), ("gave_up", "guard_reached"))
        for seconds in (130, 135, 140):
            chain.at(seconds)
        chain.settle()
        self.assertEqual(len(session.launcher.requests), 4)
        self.assertEqual(session.readiness.status().state, ServerState.STOPPED)

    def test_a_crash_restart_leaves_the_saved_files_untouched(self) -> None:
        """Criterion 9: schedules, settings, preferences and profiles are not written; state.json keeps its keys."""
        session = self.launched()
        sentinels = {
            self.data / "schedules.json": "schedule sentinel",
            self.data / "settings.json": "settings sentinel",
            self.data / "ui-preferences.json": "preferences sentinel",
            self.manager / "profiles" / "livonia-main.json": "profile sentinel",
        }
        for path, text in sentinels.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sentinels}
        chain = self.chain(session)
        self.ready(session)
        chain.at(0)
        self.crash(session)
        for seconds in (5, 10, 15):
            chain.at(seconds)
        chain.settle()
        chain.at(20)
        self.assertEqual(chain.flow.read().kind, "restarted")
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sentinels}
        self.assertEqual(after, before)
        state = json.loads((self.data / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(set(state), {"schema_version", "revision", "session_id", "launch_evidence"})

    def test_an_operator_start_during_the_wait_takes_precedence(self) -> None:
        """Criterion 8: an operator start in the wait cancels the crash restart; the server starts once."""
        session = self.launched()
        chain = self.chain(session)
        self.ready(session)
        chain.at(0)
        self.crash(session)
        chain.at(5)
        self.assertEqual(chain.flow.read().kind, "pending")
        start = CrashRestartHandlers(chain.coordinator, chain.flow).handlers()["start_server"]
        start({"profile_id": PROFILE, "expected_profile_revision": 7, "expected_settings_revision": 4})
        chain.settle()
        self.assertEqual(len(session.launcher.requests), 2)
        self.assertIsNone(chain.flow.read())
        for seconds in (10, 15, 20):
            chain.at(seconds)
        chain.settle()
        self.assertEqual(len(session.launcher.requests), 2)

    def test_a_server_found_dead_when_a_manager_opens_is_not_restarted(self) -> None:
        """Criterion 6: a server that ended while no manager was open is not restarted by the next one."""
        first = self.launched()
        self.crash(first)
        second = Session(self, "b", wall_ns=ADOPT_NS)
        self.ready(second)
        chain = self.chain(second)
        for seconds in (0, 5, 10, 15, 20):
            chain.at(seconds)
        chain.settle()
        self.assertEqual(second.launcher.requests, [])
        self.assertIsNone(chain.flow.read())
        self.assertIsNone(second.ownership.current())

    def test_a_manager_of_another_root_never_arms(self) -> None:
        """Criterion 7: a server of another manager root is outside this manager; its end starts nothing."""
        self.launched()
        second = Session(self, "b", root=self.manager.parent / "Other", wall_ns=ADOPT_NS)
        self.ready(second)
        chain = self.chain(second)
        self.assertEqual(second.readiness.status().state, ServerState.RUNNING_EXTERNAL)
        self.crash(second)
        for seconds in (0, 5, 10, 15, 20):
            chain.at(seconds)
        chain.settle()
        self.assertEqual(second.launcher.requests, [])
        self.assertIsNone(chain.flow.read())


if __name__ == "__main__":
    unittest.main()
