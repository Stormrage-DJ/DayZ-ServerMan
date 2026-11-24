"""Headless Edge checks of the Overview crash notice (plan 3.1): texts, visibility and the Stop button."""
from __future__ import annotations

import unittest

try:
    from tests.overview_harness import OVERVIEW_HEAD
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from overview_harness import OVERVIEW_HEAD
    from ui_harness_support import EDGE, run_shell_harness


# Decisions 2 and 3 of the brief: the texts, and the notice only in the states where it is true
TABLE = OVERVIEW_HEAD + r"""
await start(); await wait(60);
const text = (status) => window.ServerManOverviewCrashNotice.text(status);
const pending = (status) => window.ServerManOverviewCrashNotice.pending(status);
const status = (state, crash, extra = {}) => ({state, readiness: null, process_id: null, diagnostic_code: null,
  query_port: null, profile_id: null, started_at: null, players: null, max_players: null, auto_restart: crash, ...extra});
const AT = "2026-11-23T12:00:05.000+00:00";
const PENDING_TEXT = "The server stopped unexpectedly. DayZ-ServerMan will start it again in about 10 seconds. Press Save & Stop to cancel.";
const RESTARTED_TEXT = "The server stopped unexpectedly and DayZ-ServerMan started it again.";
const GUARD_TEXT = "The server stopped again after 3 automatic restarts in 10 minutes. DayZ-ServerMan leaves it stopped.";
const BEFORE_READY_TEXT = "The restarted server stopped before it was ready. DayZ-ServerMan does not try again.";
const START_FAILED_TEXT = "DayZ-ServerMan could not start the server again.";
const FALLBACK_TEXT = "DayZ-ServerMan did not start the server again after it stopped unexpectedly.";
const crash = (kind, reason = null) => ({kind, reason, at: AT});
// Pending shows only while the server is stopped.
same(text(status("STOPPED", crash("pending"))), PENDING_TEXT, "pending text");
same(text(status("RUNNING_MANAGED", crash("pending"))), null, "pending while running");
same(text(status("STARTING", crash("pending"))), null, "pending while starting");
// Give up: one text for each reason, and the fallback for a reason that the table does not hold.
same(text(status("STOPPED", crash("gave_up", "guard_reached"))), GUARD_TEXT, "guard_reached text");
same(text(status("STOPPED", crash("gave_up", "ended_before_ready"))), BEFORE_READY_TEXT, "ended_before_ready text");
same(text(status("STOPPED", crash("gave_up", "start_failed"))), START_FAILED_TEXT, "start_failed text");
same(text(status("STOPPED", crash("gave_up", "later_reason"))), FALLBACK_TEXT, "unknown reason text");
same(text(status("STOPPED", crash("gave_up", "constructor"))), FALLBACK_TEXT, "inherited name as a reason");
same(text(status("RUNNING_MANAGED", crash("gave_up", "guard_reached"))), null, "gave_up while running");
same(text(status("STARTING", crash("gave_up", "guard_reached"))), null, "gave_up while starting");
// Restarted shows while the server runs and its start time is not later than the time of the notice.
const live = (startedAt) => status("RUNNING_MANAGED", crash("restarted"), {started_at: startedAt});
same(text(live("2026-11-23T12:00:01.000+00:00")), RESTARTED_TEXT, "restarted text");
same(text(live(AT)), RESTARTED_TEXT, "restarted with the same time");
same(text(live("2026-11-23T12:10:00.000+00:00")), null, "restarted after a later launch");
same(text(live(null)), null, "restarted without a start time");
same(text(status("STOPPED", crash("restarted"))), null, "restarted while stopped");
// No notice without a state of the crash restart, or with a kind that is not known.
same(text(status("STOPPED", null)), null, "no auto_restart");
same(text(status("STOPPED", crash("later_kind"))), null, "unknown kind");
// Only the first row of the pending case is pending.
same(pending(status("STOPPED", crash("pending"))), true, "pending: pending row");
same(pending(status("RUNNING_MANAGED", crash("pending"))), false, "pending: running row");
same(pending(status("STOPPED", crash("gave_up", "guard_reached"))), false, "pending: gave_up row");
same(pending(status("STOPPED", null)), false, "pending: null row");
"""


# The status area with a pending restart: Stop is on and cancels the restart instead of asking to stop
PENDING = OVERVIEW_HEAD + r"""
const calls = [];
Object.assign(window.pywebview.api, {
  cancel_crash_restart: async () => { calls.push("cancel"); host.status = {...host.status, auto_restart: null};
    return ok({cancelled: true}); },
  stop_server: async () => { calls.push("stop"); return ok({operation_id: "x", state: "QUEUED"}); },
});
const status = (state, crash, extra = {}) => ({state, readiness: null, process_id: null, diagnostic_code: null,
  query_port: null, profile_id: null, started_at: null, players: null, max_players: null, auto_restart: crash, ...extra});
const stopped = (extra) => status("STOPPED", null, extra);
host.status = stopped({auto_restart: {kind: "pending", reason: null, at: "2026-11-23T12:00:05.000+00:00"}});
await start(); await wait(60);
same(region().querySelectorAll(".overview-state").length, 1, "state labels");
same(region().querySelector(".overview-state").textContent, "Stopped", "state while pending");
same(byId("overview-crash-notice").textContent, "The server stopped unexpectedly. DayZ-ServerMan will start it again in about 10 seconds. Press Save & Stop to cancel.", "pending notice");
same(reasons(), "on|on|The server is not running.", "reasons while pending");
control("Save & Stop").click(); await wait(60);
same(calls.join(","), "cancel", "calls after Stop");
check(!byId("lifecycle-confirmation"), "a confirmation dialog opened");
check(byId("overview-crash-notice") === null, "the notice stayed after the cancel");
"""

# An old notice hides by its rule: gave_up and pending with a stopped server, restarted with a running one
HIDDEN = OVERVIEW_HEAD + r"""
const status = (state, crash, extra = {}) => ({state, readiness: null, process_id: null, diagnostic_code: null,
  query_port: null, profile_id: null, started_at: null, players: null, max_players: null, auto_restart: crash, ...extra});
const stopped = (extra) => status("STOPPED", null, extra);
const GUARD = {kind: "gave_up", reason: "guard_reached", at: "2026-11-23T12:00:05.000+00:00"};
await start(); await wait(60);
host.status = stopped({auto_restart: GUARD});
await tick();
same(byId("overview-crash-notice")?.textContent, "The server stopped again after 3 automatic restarts in 10 minutes. DayZ-ServerMan leaves it stopped.", "gave_up while stopped");
host.status = running({auto_restart: GUARD});
await tick();
check(byId("overview-crash-notice") === null, "gave_up stayed after an operator start");
host.status = running({auto_restart: {kind: "restarted", reason: null, at: new Date().toISOString()}});
await tick();
same(byId("overview-crash-notice")?.textContent, "The server stopped unexpectedly and DayZ-ServerMan started it again.", "restarted while running");
host.status = stopped({auto_restart: {kind: "restarted", reason: null, at: new Date().toISOString()}});
await tick();
check(byId("overview-crash-notice") === null, "restarted stayed after a stop");
"""

# An unchanged auto_restart in a new object redraws nothing and tells no page
QUIET = OVERVIEW_HEAD + r"""
const status = (state, crash, extra = {}) => ({state, readiness: null, process_id: null, diagnostic_code: null,
  query_port: null, profile_id: null, started_at: null, players: null, max_players: null, auto_restart: crash, ...extra});
const stopped = (extra) => status("STOPPED", null, extra);
let events = 0;
document.addEventListener("serverman:server-status", () => { events += 1; });
await start(); await wait(60);
host.status = stopped({auto_restart: {kind: "gave_up", reason: "start_failed", at: "2026-11-23T12:00:05.000+00:00"}});
await tick();
const afterFirst = events;
check(afterFirst > 0, "the first change was not announced");
host.status = stopped({auto_restart: {kind: "gave_up", reason: "start_failed", at: "2026-11-23T12:00:05.000+00:00"}});
await tick();
same(events, afterFirst, "events after an equal value");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class CrashNoticeDynamicTests(unittest.TestCase):
    """The notice texts and the visibility table, typed by hand from the brief."""

    def test_the_text_and_visibility_table(self) -> None:
        """Every row of the table holds."""
        self.assertEqual(run_shell_harness(TABLE, window_size="1500,900", budget=8000), "PASS")

    def test_stop_during_the_wait_cancels_the_restart(self) -> None:
        """The pending notice shows, Stop is on and calls the cancel method without a dialog."""
        self.assertEqual(run_shell_harness(PENDING, window_size="1500,900", budget=15000), "PASS")

    def test_an_old_notice_hides_by_its_rule(self) -> None:
        """A start hides a gave_up notice and a stop hides a restarted one."""
        self.assertEqual(run_shell_harness(HIDDEN, window_size="1500,900", budget=15000), "PASS")

    def test_an_unchanged_value_redraws_nothing(self) -> None:
        """An equal auto_restart object in a new status does not announce a change."""
        self.assertEqual(run_shell_harness(QUIET, window_size="1500,900", budget=15000), "PASS")


if __name__ == "__main__":
    unittest.main()
