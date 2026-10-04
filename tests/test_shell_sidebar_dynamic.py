"""Headless Edge checks of the sidebar: server state, running profile, update badge, and the D11 lock."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

# Helpers shared by every scenario: exact comparison, a running status, one shell poll
COMMON = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const byId = (id) => document.getElementById(id);
const running = (profileId, extra = {}) => ({state: "RUNNING_MANAGED", readiness: "READY", process_id: 7,
  diagnostic_code: null, query_port: 27016, profile_id: profileId,
  started_at: "2026-10-04T05:28:00.000+00:00", ...extra});
const tick = async () => { await pollEvents(); window.clearTimeout(shellState.pollTimer); await wait(10); };
const button = (label) => [...byId("content-region").querySelectorAll("button")]
  .find((item) => item.textContent === label);
"""

# Specification 5.1 section 1: state line, whose state it is, freshness, and the D11 lock on Overview
STATE = COMMON + r"""
const SCOPE = " One DayZ server runs in this installation, whichever profile is selected.";
same(byId("sidebar-server-state").textContent, "Checking…", "before the first read");
check(byId("sidebar-server-state").classList.contains("status-neutral"), "first line is neutral");
await start();
const line = byId("sidebar-server-state");
same(line.textContent, "Stopped", "stopped label");
check(line.classList.contains("status-neutral") && !line.classList.contains("status-normal"), "stopped is grey");
same(line.title, `The configured server process is not running.${SCOPE}`, "tooltip");
check(!line.hasAttribute("aria-live") && line.tabIndex === -1, "the line is neither live nor focusable");
check(byId("sidebar-running").hidden, "no running line while stopped");
same(byId("global-profile").title, "Alpha", "selector tooltip");
// One change event per change, none for an equal status, in every section.
const seen = [];
document.addEventListener("serverman:server-status", (event) => seen.push(event.detail));
await tick();
same(seen.length, 0, "an unchanged status was announced");
commitSection("logs"); await wait(20);
host.status = running("alpha");
await tick();
same(seen.length, 1, "events after a change");
same(line.textContent, "Ready", "label follows the poll outside Overview");
check(line.classList.contains("status-normal"), "ready is green");
check(byId("sidebar-running").hidden, "the selected profile runs: one line");
same(byId("operation-announcer").textContent, "Server state: Ready.", "state announcement");
same(window.ServerManServerState.current().started_at, "2026-10-04T05:28:00.000+00:00", "start time kept");
host.status = running("alpha", {started_at: "2026-10-04T06:00:00.000+00:00"});
await tick();
same(seen.length, 2, "a new start time is a change");
// Another profile runs: the sidebar names it, and the state module reports the lock reason.
host.status = running("bravo");
await tick();
same(byId("sidebar-running").textContent, "Running: Bravo", "running line");
check(!byId("sidebar-running").hidden, "running line is shown");
same(window.ServerManServerState.lockReason(), "Bravo is running. Select it to stop or restart it.", "reason");
same(window.ServerManServerState.otherRunningProfile().profile_id, "bravo", "other profile");
// Overview: stop and restart are locked with the reason; the request is not sent.
let sent = 0;
window.pywebview.api.stop_server = async () => { sent += 1; return ok({operation_id: "x", state: "QUEUED"}); };
window.pywebview.api.restart_server = window.pywebview.api.stop_server;
commitSection("overview"); await wait(60);
for (const label of ["Save & Stop", "Save & Restart"]) {
  const control = button(label);
  check(control.disabled, `${label}: not locked`);
  same(control.title, "Bravo is running. Select it to stop or restart it.", `${label} tooltip`);
  same(control.getAttribute("aria-describedby"), "overview-running-notice", `${label} description`);
  control.click();
}
same(byId("overview-running-notice").textContent, "Select Bravo in the sidebar before you stop or restart it.",
  "visible reason");
same(byId("overview-running-notice").previousElementSibling.textContent,
  "The running server was started with Bravo", "notice title");
check(!byId("lifecycle-confirmation") && sent === 0, "a locked action ran");
check(document.querySelector(".overview-controls"), "the host probe class is gone");
// A busy operation keeps the own reason of the natively disabled buttons.
await push(record("busy", "CREATE_BACKUP", "RUNNING", {progress_phase: "STAGE"}));
same(button("Save & Stop").title, "Bravo is running. Select it to stop or restart it.", "reason under the busy lock");
host.status = running("bravo", {readiness: "UNRESPONSIVE"});
await tick();
check(!byId("operation-announcer").textContent.startsWith("Server state"), "state spoken during an operation");
await push(record("busy", "CREATE_BACKUP", "SUCCEEDED", {progress_phase: "complete", result: {}}));
// Selecting the running profile removes the line and unlocks both actions.
window.ServerManProfileContext.select("bravo"); await wait(80);
check(byId("sidebar-running").hidden, "running line after the selection");
same(window.ServerManServerState.lockReason(), "", "reason after the selection");
check(!button("Save & Stop").disabled && !button("Save & Restart").disabled && !byId("overview-running-notice"),
  "actions stayed locked for the running profile");
// A failed read keeps the last line and sets the application status; the next good read clears it.
window.pywebview.api.get_server_status = async () => fail("PROCESS_STATE_UNKNOWN", "x");
const before = seen.length;
await tick();
same(line.textContent, "Not responding", "line after a failed read");
same(byId("application-status-text").textContent, "Server status could not be refreshed", "status text");
check(window.ServerManServerState.confirmed() === false && seen.length === before + 1
  && seen.at(-1).confirmed === false, "pages were not told about the unconfirmed state");
await tick();
same(seen.length, before + 1, "a second failed read was announced again");
window.pywebview.api.get_server_status = async () => ok(host.status);
await tick();
same(byId("application-status-text").textContent, "Ready", "status cleared");
check(window.ServerManServerState.confirmed() && seen.at(-1).confirmed === true, "state confirmed again");
// The reworded labels of the specification.
for (const [state, label] of [["RUNNING_EXTERNAL", "Running outside the manager"],
  ["AMBIGUOUS", "Several servers found"], ["UNKNOWN", "State unknown"], ["STOPPING", "Stopping"]]) {
  host.status = {...running(null), state, readiness: null, profile_id: null};
  await tick();
  same(line.textContent, label, state);
}
"""

# Specification 5.1 section 3: every badge form with its accessible name, cadence, triggers, announcement
BADGE = COMMON + r"""
const requests = [];
let reads = 0;
const updates = (extra = {}, state = "OK", checking = false) => ({mods: {check_state: state, checked_at: null,
  last_success_at: null, error_code: null, update_count: 0, pending_apply_count: 0, ...extra},
  server_build: null, checking, revision: 1});
window.pywebview.api.get_update_status = async () => { reads += 1; return ok(host.updates); };
window.pywebview.api.request_update_check = async (scope, force) => { requests.push([scope, force]);
  return ok({accepted: false, checking: false}); };
await start(); await wait(30);
const item = document.querySelector('.nav-item[data-section="mods"]');
const badge = item.querySelector(".nav-badge");
const name = () => `${item.querySelector(".nav-label").textContent}${item.querySelector(".nav-badge-name").textContent}`;
same(JSON.stringify(requests), '[["mods",false]]', "one non-forced request after the first snapshot");
check(reads === 1 && badge.hidden && name() === "Mods", "no badge for a current set");
same(badge.getAttribute("aria-hidden"), "true", "badge is hidden from assistive technology");
// Land one status through the poll of a section that is not Mods.
const land = async (next) => { host.updates = next; updateStatusState.readAt -= 31000;
  await window.ServerManUpdateStatus.poll(false); await wait(10); };
for (const [next, shown, text, form, words] of [
  [updates({update_count: 2}), true, "2", "is-count", "Mods, 2 updates available"],
  [updates({update_count: 1, pending_apply_count: 1}), true, "2", "is-count",
    "Mods, 1 update available, 1 downloaded - not applied"],
  [updates({pending_apply_count: 3}), true, "3", "is-count", "Mods, 3 downloaded - not applied"],
  [updates({update_count: 12}), true, "12", "is-wide", "Mods, 12 updates available"],
  [updates({update_count: 150}), true, "99+", "is-wide", "Mods, 150 updates available"],
  [updates({}, "NEVER", true), true, "", "is-checking", "Mods, checking for updates"],
  [updates({}, "STALE", true), true, "", "is-unchecked", "Mods, updates not checked"],
  [updates({}, "OK", true), false, "", "is-none", "Mods"],
  [updates({}, "FAILED"), true, "!", "is-failed", "Mods, could not check for updates"],
  [updates({}, "NEVER"), true, "", "is-unchecked", "Mods, updates not checked"],
  [updates({}, "STALE"), true, "", "is-unchecked", "Mods, updates not checked"],
  [updates({update_count: 2}, "FAILED"), true, "2", "is-count", "Mods, 2 updates available, last check failed"],
  [updates({update_count: 2}, "STALE"), true, "2", "is-count", "Mods, 2 updates available, not checked recently"],
  [updates(), false, "", "is-none", "Mods"]]) {
  await land(next);
  check(badge.hidden === !shown && badge.textContent === text && badge.classList.contains(form) && name() === words,
    `${words}: hidden=${badge.hidden} text="${badge.textContent}" class="${badge.className}" name="${name()}"`);
}
check(!badge.classList.contains("is-wide"), "the wide form stayed");
// Cadence: 30 s outside Mods, 5 s on Mods, every tick while a check runs.
let count = reads;
updateStatusState.readAt = Date.now() - 6000;
await window.ServerManUpdateStatus.poll(false);
same(reads, count, "a read inside 30 s outside Mods");
await window.ServerManUpdateStatus.poll(true);
same(reads, count + 1, "reads after 5 s on Mods");
host.updates = updates({}, "OK", true);
updateStatusState.readAt -= 31000; await window.ServerManUpdateStatus.poll(false);
count = reads;
await window.ServerManUpdateStatus.poll(false);
same(reads, count + 1, "reads on the next tick while a check runs");
// The shell poll reads the update state in a section that is not Mods.
host.updates = updates({update_count: 1});
commitSection("logs"); await wait(20);
updateStatusState.readAt -= 31000;
await tick();
check(!badge.hidden && badge.textContent === "1", "the shell poll did not feed the badge outside Mods");
same(byId("operation-announcer").textContent, "1 mod update needs attention for Alpha.", "rise announcement");
// A rise is spoken once; a fall and an equal count are not.
byId("operation-announcer").textContent = "";
await land(updates({update_count: 3}));
same(byId("operation-announcer").textContent, "3 mod updates need attention for Alpha.", "second rise");
byId("operation-announcer").textContent = "";
await land(updates({update_count: 2})); await land(updates({update_count: 2, pending_apply_count: 0}, "STALE"));
same(byId("operation-announcer").textContent, "", "a fall was spoken");
// One request-and-read on a profile change, and after each operation that can change mod content.
let sent = requests.length;
window.ServerManProfileContext.select("bravo"); await wait(60);
same(requests.length, sent + 1, "requests on a profile change");
for (const kind of ["UPDATE_WORKSHOP_ITEMS", "PUBLISH_MODS_AND_KEYS", "APPLY_MODS_AND_RESTART", "VERIFY_WORKSHOP_FILES",
  "SAVE_PROFILE", "DELETE_PROFILE", "PROVISION_PROFILE", "RESTORE_PROFILE_FROM_BACKUP"]) {
  sent = requests.length;
  await push(record(`run-${kind}`, kind, "RUNNING", {progress_phase: "running"})); await wait(20);
  same(requests.length, sent, `${kind}: a request while it runs`);
  await push(record(`run-${kind}`, kind, "SUCCEEDED", {progress_phase: "complete", result: {}})); await wait(20);
  same(requests.length, sent + 1, `${kind}: requests after its end`);
}
sent = requests.length;
await push(record("other", "CREATE_BACKUP", "SUCCEEDED", {progress_phase: "complete", result: {}})); await wait(20);
await loadSnapshot(); window.clearTimeout(shellState.pollTimer); await wait(20);
same(requests.length, sent, "a request after a backup or a second snapshot");
check(requests.every(([scope, force]) => scope === "mods" && force === false), "a forced request was sent");
"""

# Specification 5.1 sections 1, 2, 7 and 8: zero profiles, keyboard order, context line, narrow badge button
LAYOUT = COMMON + r"""
host.updates = {...host.updates, mods: {...host.updates.mods, update_count: 2}};
host.status = running("alpha");
await start(); await wait(40);
const context = byId("page-server");
same(context.textContent, "Server: Alpha Ready", "context line");
same(context.querySelector("strong").textContent, "Alpha", "name is emphasised");
check(context.nextElementSibling === byId("page-description")
  && context.previousElementSibling === byId("page-title"), "context line place");
same(document.body.dataset.section, "overview", "body section mark");
const stateShown = () => getComputedStyle(context.querySelector(".page-server-state")).display !== "none";
const pageBadge = byId("page-update-badge");
const narrow = window.innerWidth < 860;
check(!stateShown(), "the state part is shown on Overview");
commitSection("backups"); await wait(20);
check(stateShown() === narrow, `state part at ${window.innerWidth} px`);
check(!pageBadge.hidden && (getComputedStyle(pageBadge).display !== "none") === narrow, "heading badge button");
same(pageBadge.getAttribute("aria-label"), "2 updates available. Open Mods.", "heading badge name");
same(pageBadge.querySelector(".nav-badge").textContent, "2", "heading badge text");
if (narrow) {
  check(pageBadge.getBoundingClientRect().width === 32 && pageBadge.getBoundingClientRect().height === 32, "32 px target");
  check(byId("sidebar").getBoundingClientRect().right <= 0, "drawer is closed");
  byId("menu-button").click();
  check(document.body.classList.contains("drawer-open") && byId("sidebar-server-state").textContent === "Ready"
    && document.querySelector('.nav-item[data-section="mods"] .nav-badge').textContent === "2", "drawer content");
  document.querySelector('.nav-item[data-section="logs"]').click(); await wait(20);
  check(!document.body.classList.contains("drawer-open") && shellState.section === "logs", "drawer navigation");
}
for (const [id, shown] of [["logs", false], ["settings", false], ["tweaks", true], ["profiles", true],
  ["configuration", true], ["mods", true]]) {
  shellState.section = "overview"; commitSection(id); await wait(10);
  same(context.hidden, !shown, `${id}: context line hidden`);
  same(document.body.dataset.section, id, "body section mark");
}
check(pageBadge.hidden, "the heading badge is shown on Mods");
commitSection("backups"); await wait(20);
pageBadge.click(); await wait(20);
same(shellState.section, "mods", "the heading badge opens Mods");
// A page text replaces the name until the next section open.
window.ServerManPageContext.setText("New profile");
same(context.textContent, "New profile", "page text");
commitSection("backups"); await wait(20);
same(context.querySelector("strong").textContent, "Alpha", "page text after the next open");
// Keyboard: every item is a tab stop in visual order, and the current page is marked once.
const items = [...document.querySelectorAll(".nav-item")];
check(items.every((item) => item.tabIndex === 0 && item.type === "button"), "tab stops");
same(items.map((item) => item.dataset.section).join(","),
  "overview,mods,backups,logs,profiles,configuration,tweaks,settings", "visual order");
same(document.querySelectorAll('.nav-item[aria-current="page"]').length, 1, "current page marks");
check(items.every((item) => item.querySelector("svg.nav-icon[aria-hidden='true'][focusable='false']")
  && !item.querySelector("span[aria-hidden]:not(.nav-badge)")), "icons replaced the letter tiles");
check(byId("page-title").tagName === "H1" && !document.querySelector(".sidebar h1, .sidebar h2, .sidebar h3"),
  "a sidebar heading precedes the page title");
// No profile: the selector says so, the sidebar offers the first profile, the state is still shown.
profiles.length = 0;
await window.ServerManProfileContext.refreshAndSelect(); await wait(40);
const select = byId("global-profile");
check(select.disabled && select.options[0].textContent === "No profiles" && !select.title, "empty selector");
check(!byId("sidebar-create-profile").hidden, "Create profile is not offered");
same(byId("sidebar-server-state").textContent, "Ready", "state without a profile");
same(context.textContent, "No server profile yet", "context line without a profile");
check(document.querySelector('.nav-item[data-section="mods"] .nav-badge').hidden && pageBadge.hidden,
  "a badge without a profile");
byId("sidebar-create-profile").click(); await wait(20);
same(shellState.section, "profiles", "Create profile opens Profiles");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ShellSidebarDynamicTests(unittest.TestCase):
    """The sidebar, the badge and the heading context in the running shell."""

    def test_state_line_running_profile_change_event_and_lock(self) -> None:
        """State line, "Running: <name>", one event per change, failed read, and the D11 lock."""
        self.assertEqual(run_shell_harness(STATE, budget=8000), "PASS")

    def test_badge_forms_names_cadence_triggers_and_announcement(self) -> None:
        """Every badge form with its name; the cadence; the immediate reads; the rise announcement."""
        self.assertEqual(run_shell_harness(BADGE, budget=12000), "PASS")

    def test_context_line_keyboard_order_and_zero_profiles(self) -> None:
        """Context line, page mark, tab order, and the sidebar without a profile."""
        self.assertEqual(run_shell_harness(LAYOUT, budget=8000), "PASS")

    def test_narrow_window_shows_state_and_badge_button_in_the_heading(self) -> None:
        """Below 860 px the heading names the state and repeats the badge as a button."""
        self.assertEqual(run_shell_harness(LAYOUT, window_size="700,900", budget=8000), "PASS")


if __name__ == "__main__":
    unittest.main()
