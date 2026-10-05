"""Pure rules of the server build check: pending reasons, ownership class, status table and timing."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

try:
    from tests import server_build_fixtures  # noqa: F401
except ModuleNotFoundError:
    import server_build_fixtures  # noqa: F401

from dayz_serverman.application.steamcmd_guard import LANE_WAIT_SECONDS
from dayz_serverman.domain.server_build import (
    CHECK_HOLD_SECONDS,
    BranchFact,
    BuildState,
    InstalledBuild,
    InstalledRead,
    Ownership,
    OwnershipSignals,
    build_status,
    due_trigger,
    ownership_class,
    pending_reason,
    seconds_until_due,
)
from dayz_serverman.domain.update_check import AttemptOutcome, CheckAttempt
from dayz_serverman.domain.update_check_rules import CheckState, utc_text

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PUBLIC = {"public": BranchFact(24570360, 1786528820)}


def build(**values) -> InstalledBuild:
    """Return the installed build of evidence 6.1 with overrides."""
    fields = {"build_id": 24570360, "target_build_id": None, "state_flags": 4,
              "branch": "public", "branch_change": False, **values}
    return InstalledBuild(**fields)


def attempt(age_seconds: float, outcome: AttemptOutcome = AttemptOutcome.OK) -> CheckAttempt:
    """Return an attempt that finished the given seconds before NOW."""
    return CheckAttempt(utc_text(NOW - timedelta(seconds=age_seconds)), outcome,
                        None if outcome is AttemptOutcome.OK else "TIMEOUT")


class PendingReasonTests(unittest.TestCase):
    """Detailed design 14.2: pending reason, first match."""

    def test_each_flag_row(self) -> None:
        """Every row of the flag table and the order of the reasons."""
        cases = [
            (build(), None), (build(state_flags=4 | 64 | 8 | 16 | 1), None),
            (build(target_build_id=24570360), None), (build(target_build_id=24600000), "TARGET_BUILD"),
            (build(state_flags=6), "UPDATE_REQUIRED"), (build(state_flags=4 | 1024), "UPDATE_RUNNING"),
            (build(state_flags=4 | 256), "UPDATE_RUNNING"), (build(state_flags=36), "FILES_DAMAGED"),
            (build(state_flags=132), "FILES_DAMAGED"), (build(branch_change=True), "BRANCH_CHANGE"),
            # Order: target build, update running, update required, files damaged, branch change
            (build(target_build_id=1, state_flags=1026 | 32, branch_change=True), "TARGET_BUILD"),
            (build(state_flags=1026 | 32, branch_change=True), "UPDATE_RUNNING"),
            (build(state_flags=2 | 32, branch_change=True), "UPDATE_REQUIRED"),
            (build(state_flags=4 | 32, branch_change=True), "FILES_DAMAGED"),
        ]
        for installed, expected in cases:
            with self.subTest(installed=installed):
                self.assertEqual(pending_reason(installed), expected)


class OwnershipTests(unittest.TestCase):
    """Detailed design 14.3: rows 1 to 8; signals that disagree give UNKNOWN."""

    def classify(self, **signals) -> Ownership:
        """Classify a found build with the given signals."""
        return ownership_class(InstalledRead(build(), None, OwnershipSignals(**signals)))

    def test_rows(self) -> None:
        """Each row of the table, first match wins."""
        self.assertIs(ownership_class(InstalledRead(None, "NO_MANIFEST")), Ownership.UNKNOWN)
        self.assertIs(self.classify(layout="A", launcher_steam=True), Ownership.UNKNOWN)
        self.assertIs(self.classify(layout="A"), Ownership.STEAMCMD)
        self.assertIs(self.classify(layout="A", launcher_steamcmd=True), Ownership.STEAMCMD)
        for client in ({"steam_exe": True}, {"client_library": True}, {"launcher_steam": True}):
            with self.subTest(client=client):
                self.assertIs(self.classify(layout="B", steamcmd_root=True, **client), Ownership.UNKNOWN)
                self.assertIs(self.classify(layout="B", launcher_steamcmd=True, **client), Ownership.UNKNOWN)
                self.assertIs(self.classify(layout="B", **client), Ownership.STEAM_CLIENT)
        self.assertIs(self.classify(layout="B", steamcmd_root=True), Ownership.STEAMCMD)
        self.assertIs(self.classify(layout="B", steamcmd_root=True, launcher_steamcmd=True), Ownership.STEAMCMD)
        self.assertIs(self.classify(layout="B"), Ownership.UNKNOWN)
        # A SteamCMD that is not the configured one is not proven
        self.assertIs(self.classify(layout="B", launcher_steamcmd=True), Ownership.UNKNOWN)

    def test_evidence_layout_is_a_steam_client_library(self) -> None:
        """Evidence 6.1: library layout, steam.exe at the root, LauncherPath steam.exe."""
        self.assertIs(self.classify(layout="B", steam_exe=True, launcher_steam=True), Ownership.STEAM_CLIENT)


class StatusTableTests(unittest.TestCase):
    """Detailed design 14.7, first match wins."""

    def status(self, read=None, state=CheckState.OK, branches=PUBLIC, success=True, configured=True):
        """Apply the rule with defaults of a current installation."""
        read = read or InstalledRead(build(), None)
        return build_status(read, state, branches, has_success=success, steamcmd_configured=configured)

    def test_each_row(self) -> None:
        """Rows 1 to 7 with their reasons and values."""
        unknown = self.status(InstalledRead(None, "NO_MANIFEST"))
        self.assertEqual((unknown.state, unknown.reason, unknown.available_build),
                         (BuildState.UNKNOWN_INSTALLATION, "NO_MANIFEST", 24570360))
        pending = self.status(InstalledRead(build(target_build_id=24600000), None))
        self.assertEqual((pending.state, pending.reason, pending.target_build),
                         (BuildState.UPDATE_PENDING, "TARGET_BUILD", 24600000))
        newer = {"public": BranchFact(24600000, 1790000000)}
        available = self.status(branches=newer)
        self.assertEqual((available.state, available.available_build, available.available_time),
                         (BuildState.UPDATE_AVAILABLE, 24600000, 1790000000))
        self.assertIs(self.status().state, BuildState.CURRENT)
        beta = self.status(InstalledRead(build(branch="beta"), None))
        self.assertEqual((beta.state, beta.reason), (BuildState.COULD_NOT_CHECK, "BRANCH_NOT_LISTED"))
        never = self.status(state=CheckState.NEVER, branches={}, success=False, configured=False)
        self.assertEqual(never.reason, "STEAMCMD_NOT_CONFIGURED")
        for state in (CheckState.NEVER, CheckState.STALE, CheckState.FAILED):
            with self.subTest(state=state):
                self.assertEqual(self.status(state=state, branches={} if state is CheckState.NEVER else PUBLIC,
                                             success=state is not CheckState.NEVER).reason, state.value)

    def test_never_current_without_a_fresh_success(self) -> None:
        """A stale or failed check never reads current, but still shows an available update."""
        for state in (CheckState.STALE, CheckState.FAILED, CheckState.NEVER):
            with self.subTest(state=state):
                self.assertIsNot(self.status(state=state).state, BuildState.CURRENT)
                newer = {"public": BranchFact(24600000, None)}
                self.assertIs(self.status(state=state, branches=newer).state, BuildState.UPDATE_AVAILABLE)

    def test_pending_wins_over_available(self) -> None:
        """Steam already knows and acts: pending before available."""
        read = InstalledRead(build(state_flags=6), None)
        newer = {"public": BranchFact(24600000, None)}
        self.assertIs(self.status(read, branches=newer).state, BuildState.UPDATE_PENDING)


class TimingTests(unittest.TestCase):
    """Detailed design 14.6: start, debounce, interval, waiting and the guard bounds."""

    def test_start_trigger_and_debounce(self) -> None:
        """The start check runs once; a check shortly before the start counts as it."""
        self.assertEqual(due_trigger(None, False, NOW, True), ("START", False))
        self.assertEqual(due_trigger(attempt(601), False, NOW, True), ("START", False))
        self.assertEqual(due_trigger(attempt(599), False, NOW, True), (None, True))
        self.assertEqual(due_trigger(None, False, NOW, False), (None, False))

    def test_interval_also_after_a_failure(self) -> None:
        """An interval run is due 6 h after the last attempt; a failure does not shorten it."""
        for outcome in (AttemptOutcome.OK, AttemptOutcome.FAILED):
            with self.subTest(outcome=outcome):
                self.assertEqual(due_trigger(attempt(6 * 3600, outcome), True, NOW, True), ("INTERVAL", True))
                self.assertEqual(due_trigger(attempt(6 * 3600 - 1, outcome), True, NOW, True), (None, True))
        self.assertEqual(due_trigger(attempt(-120), True, NOW, True), ("INTERVAL", True))

    def test_seconds_until_due(self) -> None:
        """Waiting evaluates every 5 s; otherwise the next interval or at once."""
        self.assertEqual(seconds_until_due(attempt(10), True, True, NOW), 5.0)
        self.assertEqual(seconds_until_due(attempt(10), False, False, NOW), 0.0)
        self.assertEqual(seconds_until_due(attempt(3600), True, False, NOW), 5 * 3600)

    def test_lane_wait_outlasts_the_longest_check_hold(self) -> None:
        """Architect decision 2: lane wait (120 s) > longest check hold (60 s deadline + 30 s)."""
        self.assertEqual(CHECK_HOLD_SECONDS, 90.0)
        self.assertGreater(LANE_WAIT_SECONDS, CHECK_HOLD_SECONDS)


if __name__ == "__main__":
    unittest.main()
