"""Headless Edge checks of the phase 5 rework: QF-029, QF-030, QF-031, QF-032, QF-033, QF-034 and QF-037."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# Shared helpers: exact comparison, a poll tick, a sidebar switch, a running status, a stored switch value
HEAD = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const byId = (id) => document.getElementById(id);
const region = () => byId("content-region");
const button = (label, root = region()) => [...root.querySelectorAll("button")].find((item) => item.textContent === label);
const tick = async () => { await pollEvents(); window.clearTimeout(shellState.pollTimer); await wait(10); };
const choose = async (profileId) => { const select = byId("global-profile"); select.value = profileId;
  select.dispatchEvent(new Event("change", {bubbles: true})); await wait(150); };
const running = (profileId, extra = {}) => ({state: "RUNNING_MANAGED", readiness: "READY", process_id: 7,
  diagnostic_code: null, query_port: 27016, profile_id: profileId, started_at: "2026-10-04T05:28:00.000+00:00", ...extra});
const page = {automatic: true, saveDelay: 0, saveFails: false, stops: 0};
Object.assign(window.pywebview.api, {
  get_ui_preferences: async () => ok({selected_profile_id: "alpha", backup_after_stop_profiles: [],
    automatic_update_checks: page.automatic}),
  save_automatic_update_checks: async (enabled) => { await wait(page.saveDelay);
    if (page.saveFails) return fail("STORAGE_FAILURE", "x");
    page.automatic = enabled; return ok({automatic_update_checks: enabled}); },
  stop_server: async () => { page.stops += 1; return ok({operation_id: "stop", state: "QUEUED"}); },
  restart_server: async () => { page.stops += 1; return ok({operation_id: "stop", state: "QUEUED"}); },
  list_mod_inventory: async () => ok([{order: 1, name: "Example Mod", directory: "@Example", launch_scope: "client",
    source_kind: "workshop", workshop_id: "111", version: "1.0", state: "INSTALLED", time_updated: 1,
    remote_time_updated: null, remote_check: "NEVER", pending_reason: null}]),
});
"""

# QF-029: after a sidebar switch every consumer shows the new profile's status, also when it equals the old one
SWITCH = HEAD + r"""
const updates = (state, count, checking = false) => ({mods: {check_state: state, checked_at: "2026-10-04T08:00:00.000Z",
  last_success_at: state === "NEVER" ? null : "2026-10-04T08:00:00.000Z", error_code: state === "FAILED" ? "TIMEOUT" : null,
  update_count: count, pending_apply_count: 0}, server_build: null, checking, revision: 7});
const cases = [
  [updates("OK", 2), true, "is-count", "2", "Mods, 2 updates available", "2 updates available"],
  [updates("STALE", 0), true, "is-unchecked", "", "Mods, updates not checked", "Could not check: the last check is too old"],
  [updates("FAILED", 0), true, "is-failed", "!", "Mods, could not check for updates",
    "Could not check: Steam did not answer in time"],
  [updates("NEVER", 0), true, "is-unchecked", "", "Mods, updates not checked", "Could not check: not checked yet"],
  [updates("NEVER", 0, true), true, "is-checking", "", "Mods, checking for updates", "Checking for updates…"],
  [updates("STALE", 0), false, "is-unchecked", "", "Mods, updates not checked", "Could not check: automatic checks are off"],
];
await start(); await wait(60);
const item = () => document.querySelector('.nav-item[data-section="mods"]');
const shown = () => { const badge = item().querySelector(".nav-badge");
  return `${badge.hidden ? "none" : `${badge.className.replace("nav-badge ", "")}:${badge.textContent}`}|`
    + `${item().querySelector(".nav-label").textContent}${item().querySelector(".nav-badge-name").textContent}`; };
let profile = "alpha";
for (const [status, automatic, form, text, name, summary] of cases) {
  // The selected profile shows the status first; the other profile then answers with exactly the same status.
  host.updates = status; page.automatic = automatic; window.ServerManUpdateStatus.setAutomaticChecks(automatic);
  updateStatusState.readAt -= 31000; await window.ServerManUpdateStatus.poll(false); await wait(20);
  profile = profile === "alpha" ? "bravo" : "alpha";
  commitSection("overview"); await wait(60);
  await choose(profile);
  const label = `${status.mods.check_state}${status.checking ? " checking" : ""}${automatic ? "" : " automatic off"}`;
  same(shown(), `${form}:${text}|${name}`, `${label}: sidebar badge`);
  same(byId("overview-updates").querySelector(".overview-updates-summary").textContent, summary, `${label}: Overview card`);
  check(!byId("page-update-badge").hidden && byId("page-update-badge").getAttribute("aria-label")
    === `${name.slice(6, 7).toUpperCase()}${name.slice(7)}. Open Mods.`, `${label}: heading badge`);
  // The page shows the same three answers on later polls, and the Mods header agrees.
  for (let index = 0; index < 3; index += 1) { updateStatusState.readAt -= 31000; await tick(); }
  same(shown(), `${form}:${text}|${name}`, `${label}: sidebar badge after polls`);
  commitSection("mods"); await wait(120);
  same(byId("mods-update-summary").textContent, summary, `${label}: Mods header`);
}
"""

# QF-030: an unchanged state writes nothing into the live application status over a simulated minute
QUIET = HEAD + r"""
await start(); await wait(60);
const writes = [];
const watch = new MutationObserver((records) => writes.push(...records.map((record) => record.type)));
for (const node of [byId("application-status"), byId("menu-button")]) {
  watch.observe(node, {subtree: true, childList: true, characterData: true, attributes: true});
}
for (const section of ["overview", "logs", "mods"]) {
  commitSection(section); await wait(60); writes.length = 0;
  for (let index = 0; index < 40; index += 1) await tick();
  same(writes.length, 0, `writes into the application status over a minute on ${section}`);
}
// A real change is still written, once, and so is the way back.
window.pywebview.api.get_server_status = async () => fail("PROCESS_STATE_UNKNOWN", "x");
await tick(); await tick();
same(byId("application-status-text").textContent, "Server status could not be refreshed", "failed read");
const afterFailure = writes.length;
check(afterFailure > 0, "a changed status was not written");
await tick();
same(writes.length, afterFailure, "a second equal failure was written again");
window.pywebview.api.get_server_status = async () => ok(host.status);
await tick();
same(byId("application-status-text").textContent, "Ready", "status after a good read");
"""

# QF-031, QF-032 and QF-037 on Overview
OVERVIEW = HEAD + r"""
window.pywebview.api.get_lifecycle_schedule = async (profileId) => ok({profile_id: profileId, enabled: false,
  action: null, hour: 4, minute: 0, next_run_local: null, last_status: null});
host.status = running("alpha");
await start(); await wait(80);
// QF-031: before a schedule exists the card says that the time is local time.
const schedule = byId("overview-schedule");
check(schedule.querySelector(".schedule-summary").textContent === "No scheduled action"
  && schedule.textContent.includes("Runs at local time, only while DayZ-ServerMan is open."), "local time note");
// QF-032: the focus stays on the same control across a state redraw, and an open disclosure stays open.
const redraw = async (extra) => { host.status = running("alpha", extra); await tick(); await wait(10); };
for (const [id, extra] of [["overview-stop", {readiness: "UNRESPONSIVE"}], ["overview-restart", {readiness: "READY"}],
  ["backup-after-stop", {readiness: "STARTING"}], ["overview-process-summary", {readiness: "READY"}]]) {
  byId(id).focus();
  const before = byId("overview-server");
  await redraw(extra);
  check(byId("overview-server") !== before, `${id}: the panel was not redrawn`);
  same(document.activeElement?.id, id, `focus after a redraw from ${id}`);
}
byId("overview-process").open = true; byId("overview-process-summary").focus();
await redraw({readiness: "UNRESPONSIVE"});
check(byId("overview-process").open && document.activeElement === byId("overview-process-summary"), "open disclosure");
// A control that is off after the redraw leaves the focus on the panel heading, not on the page body.
byId("overview-stop").focus();
await redraw({state: "STOPPING", readiness: null});
check(byId("overview-stop").disabled && document.activeElement === byId("overview-server-title"), "focus on an off control");
// QF-037: the lock is checked again at the press, also when the button is reached without its disabled state.
host.status = running("bravo"); await tick(); await wait(20);
check(byId("overview-stop").disabled && byId("overview-restart").disabled, "the lock is missing");
for (const action of ["stop", "restart"]) {
  await confirmLifecycleAction(action); await wait(20);
  check(!byId("lifecycle-confirmation"), `${action}: a locked action opened its confirmation`);
}
same(page.stops, 0, "lifecycle requests while another profile runs");
"""

# QF-033: on Mods the lock reason is visible text tied to the restart action; QF-034: the switch after a redraw
MODS_AND_SETTINGS = HEAD + r"""
host.status = running("bravo");
await start("mods"); await wait(120);
const reason = "Bravo is running. Select it to stop or restart it.";
const line = () => byId("mods-restart-lock");
check(line() && line().textContent === reason && line().offsetHeight > 0
  && line().previousElementSibling === byId("mods-update-header"), "visible lock line under the header");
same(byId("update-start").getAttribute("aria-describedby"), "mods-restart-lock", "the restart is not tied to its reason");
check(byId("update-start").disabled, "the restart is offered");
await choose("bravo"); await wait(60);
check(!line() && !byId("update-start").hasAttribute("aria-describedby") && !byId("update-start").disabled,
  "the line stayed for the running profile");
host.status = {...running(null), state: "STOPPED", readiness: null, process_id: null, profile_id: null};
await choose("alpha"); await tick(); await wait(40);
check(!line(), "a stopped server shows the lock line");
// QF-034: a redraw of the Settings form while the switch saves shows the stored value afterwards.
commitSection("settings"); await wait(120);
const box = () => byId("automatic-update-checks");
page.saveDelay = 80;
box().click();
byId("backup-mode-custom").click(); await wait(20);
check(box().disabled && box().checked === false, `the redrawn switch during the save: ${box().checked} ${box().disabled}`);
await wait(150);
check(!box().disabled && box().checked === false && page.automatic === false
  && byId("automatic-update-checks-feedback").textContent === "Saved. Automatic checks are off.",
  "after the save, a redrawn switch shows the stored value");
// A failed save during a redraw puts the stored value back on the switch that is in the page.
page.saveFails = true;
box().click();
byId("backup-mode-portable").click(); await wait(150);
check(!box().disabled && box().checked === false && window.ServerManUpdateStatus.automaticChecks() === false,
  `after a failed save: ${box().checked}`);
page.saveFails = false; page.saveDelay = 0;
box().click(); await wait(60);
check(box().checked && byId("automatic-update-checks-feedback").textContent === "Saved. Automatic checks are on.",
  "a save without a redraw");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class Phase5ReworkDynamicTests(unittest.TestCase):
    """Findings QF-029 to QF-034 and QF-037 of the independent verification of phase 5."""

    def test_every_consumer_shows_the_new_profile_after_a_switch(self) -> None:
        """QF-029: equal counts, stale, failed, never, checking and automatic off."""
        self.assertEqual(run_shell_harness(SWITCH, budget=30000), "PASS")

    def test_unchanged_state_writes_nothing_into_the_application_status(self) -> None:
        """QF-030: forty quiet ticks per section write nothing; a change is written once."""
        self.assertEqual(run_shell_harness(QUIET, budget=30000), "PASS")

    def test_local_time_note_focus_after_a_redraw_and_press_time_lock(self) -> None:
        """QF-031, QF-032 and QF-037 on Overview."""
        self.assertEqual(run_shell_harness(OVERVIEW, window_size="1500,900", budget=12000), "PASS")

    def test_mods_lock_line_and_switch_after_a_redraw(self) -> None:
        """QF-033 on Mods and QF-034 on Settings."""
        self.assertEqual(run_shell_harness(MODS_AND_SETTINGS, window_size="1500,900", budget=12000), "PASS")


if __name__ == "__main__":
    unittest.main()
