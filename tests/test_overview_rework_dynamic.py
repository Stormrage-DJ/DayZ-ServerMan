"""Headless Edge checks of the reworked Overview: notices, server panel, and the three cards."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# Fake answers of the Overview page: snapshot parts, update state, backup history, and the schedule
HEAD = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const byId = (id) => document.getElementById(id);
const region = () => byId("content-region");
const button = (label, root = region()) => [...root.querySelectorAll("button")]
  .find((item) => item.textContent === label);
const tick = async () => { await pollEvents(); window.clearTimeout(shellState.pollTimer); await wait(10); };
const page = {block: null, root: "D:\\Server", backups: [], runtime: "serverman\\alpha\\profile", backupReads: 0,
  backupAnswer: null, schedule: {action: "restart", hour: 4, minute: 0, last_status: "QUEUED"}, saved: [],
  automatic: true, requests: []};
const updates = (extra = {}, state = "OK", checking = false) => ({mods: {check_state: state, checked_at: null,
  last_success_at: state === "NEVER" ? null : "2026-10-04T08:41:07.120Z", error_code: null, update_count: 0,
  pending_apply_count: 0, ...extra}, server_build: null, checking, revision: 1});
const scheduleView = (profileId) => ({profile_id: profileId, enabled: Boolean(page.schedule.action), ...page.schedule,
  next_run_local: page.schedule.action ? "2026-10-05T04:00" : null});
Object.assign(window.pywebview.api, {
  get_application_snapshot: async () => { const value = snapshot(host.snapshotOperations);
    value.mutation_block = page.block; value.settings.dayz_root = page.root; return ok(value); },
  get_ui_preferences: async () => ok({selected_profile_id: "alpha", backup_after_stop_profiles: [],
    automatic_update_checks: page.automatic}),
  request_update_check: async (scope, force) => { page.requests.push(force);
    return ok({accepted: force, checking: false}); },
  list_backups: async (profileId) => { page.backupReads += 1;
    if (page.backupAnswer) return page.backupAnswer;
    return ok({profile_id: profileId, profile_revision: 3, settings_revision: 2, backups: page.backups,
      diagnostics: [], legacy_backups: [], destination_kind: "portable", runtime_profile: page.runtime}); },
  get_lifecycle_schedule: async (profileId) => ok(scheduleView(profileId)),
  save_lifecycle_schedule: async (profileId, hour, minute, action) => { page.saved.push([profileId, hour, minute, action]);
    page.schedule = {...page.schedule, hour, minute, action}; return ok(scheduleView(profileId)); },
});
const running = (extra = {}) => ({state: "RUNNING_MANAGED", readiness: "READY", process_id: 18244,
  diagnostic_code: null, query_port: 27016, profile_id: "alpha",
  started_at: new Date(Date.now() - (3 * 60 + 12) * 60000).toISOString(), ...extra});
const control = (label) => button(label, region().querySelector(".overview-controls"));
const reasons = () => ["Start server", "Save & Stop", "Save & Restart"].map((label) =>
  control(label).disabled ? control(label).title : "on").join("|");
const reopen = async () => { commitSection("logs"); await wait(10); commitSection("overview"); await wait(80); };
"""

# Specification 5.1 section 4: notices, the state once, uptime, process details, controls with reasons
SERVER = HEAD + r"""
await start(); await wait(60);
// Stopped: one panel "Server" with the state once; the removed parts are gone.
const panel = () => byId("overview-server");
same(panel().querySelector("h2").textContent, "Server", "panel title");
same(region().querySelectorAll(".overview-state").length, 1, "state labels");
check(panel().querySelector(".overview-state").textContent === "Stopped"
  && panel().querySelector(".overview-state").classList.contains("status-neutral"), "stopped state");
same(region().textContent.split("Stopped").length - 1, 1, "times the state word is shown on the page");
for (const gone of ["Recent operations", "Active locations", "Managed process", "Live state", "Server at a glance",
  "Server control", "D:\\Server"]) check(!region().textContent.includes(gone), `still shown: ${gone}`);
same(byId("page-description").textContent, "Server state, updates, backups and the daily schedule.", "description");
same(byId("overview-uptime").textContent, "Not running", "uptime while stopped");
// Process details: a closed disclosure with the three values.
const details = () => byId("overview-process");
check(details().tagName === "DETAILS" && !details().open
  && details().querySelector("summary").textContent === "Process details", "process disclosure");
const values = () => [...details().querySelectorAll("dt, dd")].map((node) => node.textContent).join("|");
same(values(), "Process ID|None|Steam query port|Not known|Note|No problem reported.", "process values");
// The four controls stay in today's order inside the class that the host probe reads.
same([...region().querySelectorAll(".overview-controls .action-row > *")].map((node) => node.textContent).join("|"),
  "Backup after stop|Start server|Save & Stop|Save & Restart", "control order");
check(control("Start server").classList.contains("button-primary"), "Start is not the primary action");
same(reasons(), "on|The server is not running.|The server is not running.", "reasons while stopped");
// Ready: uptime from the start time; Start is off and no longer primary.
host.status = running();
await tick();
same(byId("overview-uptime").textContent, "Running for 3 h 12 min", "uptime");
check(byId("overview-uptime").title.startsWith("Started "), "uptime tooltip");
check(!control("Start server").classList.contains("button-primary"), "a locked Start is primary");
same(reasons(), "The server is already running.|on|on", "reasons while ready");
details().open = true;
same(values(), "Process ID|18244|Steam query port|27016|Note|No problem reported.", "process values while running");
// A status change redraws the notice and the panel only: the cards and an open disclosure stay.
const card = byId("overview-updates"); const editor = region().querySelector(".schedule-editor");
editor.open = true; editor.querySelector("input").value = "17";
host.status = running({readiness: "UNRESPONSIVE", diagnostic_code: "INVENTORY_INCOMPLETE"});
await tick();
check(panel().querySelector(".overview-state").textContent === "Not responding" && details().open,
  "the state or the open process details were lost");
check(values().endsWith("Note|The running programs could not be read completely."), "diagnostic note");
check(byId("overview-updates") === card && editor.isConnected && editor.open
  && editor.querySelector("input").value === "17", "a status change rebuilt the cards");
// The uptime follows the poll tick and is written only when it changed.
byId("overview-uptime").textContent = "old";
await tick();
same(byId("overview-uptime").textContent, "Running for 3 h 12 min", "uptime after a poll tick");
// Duration wording and the cases without a known uptime.
const uptime = (status, minutes) => window.ServerManOverviewServer.uptime(
  {state: "RUNNING_MANAGED", started_at: new Date(1000000000000 - minutes * 60000).toISOString(), ...status},
  1000000000000).text;
same(uptime({}, 0.5), "Running for less than a minute", "seconds");
same(uptime({}, 5), "Running for 5 min", "minutes");
same(uptime({}, 47 * 60 + 59), "Running for 47 h 59 min", "hours");
same(uptime({}, 49 * 60), "Running for 2 d 1 h", "days");
same(uptime({started_at: null}, 5), "Uptime not known", "no start time");
same(uptime({state: "RUNNING_EXTERNAL"}, 5), "Uptime not known", "external");
same(uptime({state: "STARTING"}, 5), "Not running", "starting");
// Every other state turns all three controls off with its own reason.
for (const [state, reason] of [["STARTING", "The server is starting."], ["STOPPING", "The server is stopping."],
  ["RUNNING_EXTERNAL", "The server runs outside DayZ-ServerMan. Stop it there."],
  ["AMBIGUOUS", "The server state could not be confirmed."], ["UNKNOWN", "The server state could not be confirmed."]]) {
  host.status = running({state, readiness: null, profile_id: null, started_at: null});
  await tick();
  same(reasons(), [reason, reason, reason].join("|"), state);
}
// Notices, one at most: recovery with its reason, then setup with a way to Settings.
host.status = {...running(), state: "STOPPED", readiness: null, process_id: null, profile_id: null, started_at: null};
page.block = "Mutations are blocked by unresolved mod publication recovery.";
await reopen();
same(region().querySelectorAll(".notice").length, 1, "notices");
check(region().querySelector(".notice h2").textContent === "Recovery required" && region().querySelector(".notice")
  .textContent.includes("Applying mods to the server folder was interrupted"), "recovery notice");
check(region().firstElementChild.firstElementChild === region().querySelector(".notice"), "the notice is not first");
const blocked = "Changes are blocked until recovery is resolved.";
same(reasons(), [blocked, blocked, blocked].join("|"), "reasons under a recovery block");
page.block = null; page.root = null;
await reopen();
same(region().querySelector(".notice h2").textContent, "Complete application setup", "setup notice");
same(control("Start server").title, "Complete the setup in Settings first.", "setup reason");
button("Open Settings", region().querySelector(".notice")).click(); await wait(60);
same(shellState.section, "settings", "Open Settings");
// No profile: the reason names the first step.
page.root = "D:\\Server"; profiles.length = 0;
await window.ServerManProfileContext.refreshAndSelect(); commitSection("overview"); await wait(80);
same(region().querySelector(".notice h2").textContent, "Create a server profile", "profile notice");
same(control("Start server").title, "Create a server profile first.", "profile reason");
check(document.querySelector(".overview-controls"), "the host probe class is gone");
"""

# Updates, last backup and schedule cards; the updates card agrees with the badge and the Mods header
CARDS = HEAD + r"""
host.updates = updates({update_count: 2, pending_apply_count: 1});
page.backups = [
  {backup_id: "old", created_at: "2026-10-01T04:00:00Z", entry_count: 1, total_size: 10, restore_compatibility: "COMPATIBLE"},
  {backup_id: "new", created_at: "2026-10-03T04:00:00Z", entry_count: 412, total_size: 1288490188,
    restore_compatibility: "COMPATIBLE"}];
window.pywebview.api.list_mod_inventory = async () => ok([1, 2, 3, 4].map((order) => ({order, name: `Mod ${order}`,
  directory: `@m${order}`, launch_scope: "client", source_kind: "workshop", workshop_id: String(100 + order),
  version: "1", state: order < 3 ? "UPDATE_AVAILABLE" : order === 3 ? "PENDING_APPLY" : "CURRENT", time_updated: 1,
  remote_time_updated: 2, remote_check: "OK", pending_reason: order === 3 ? "TARGET_MISSING" : null})));
await start(); await wait(80);
const cards = [...region().querySelectorAll(".overview-cards > .overview-card")];
same(cards.map((card) => card.querySelector("h2").textContent).join("|"),
  "Updates|Last backup|Next scheduled action", "cards");
// Updates: the same sentence as the badge name and the Mods header, and a primary way to Mods.
const summary = () => byId("overview-updates").querySelector(".overview-updates-summary");
const expected = "2 updates available · 1 downloaded - not applied";
same(summary().textContent, expected, "updates summary");
check(summary().classList.contains("status-warning"), "summary tone");
same(document.querySelector('.nav-item[data-section="mods"] .nav-badge').textContent, "3", "badge");
same(document.querySelector('.nav-item[data-section="mods"] .nav-badge-name').textContent,
  ", 2 updates available, 1 downloaded - not applied", "badge name");
check(byId("overview-updates").textContent.includes("Last checked ")
  && byId("overview-open-mods").classList.contains("button-primary"), "last check or primary link");
commitSection("mods"); await wait(100);
same(byId("mods-update-summary").textContent, expected, "Mods header summary");
same(document.querySelectorAll('.mods-table tbody tr[data-state="UPDATE_AVAILABLE"]').length
  + document.querySelectorAll('.mods-table tbody tr[data-state="PENDING_APPLY"]').length, 3, "Mods table rows");
commitSection("overview"); await wait(80);
// The card follows the update state in place: nothing pending, a running check, the switch, "Check now".
const card = byId("overview-updates");
const land = async (next) => { host.updates = next; updateStatusState.readAt -= 31000;
  await window.ServerManUpdateStatus.poll(false); await wait(10); };
await land(updates());
check(byId("overview-updates") === card && summary().textContent === "No updates available"
  && !byId("overview-open-mods").classList.contains("button-primary"), "card after the updates were applied");
check(card.querySelector(".overview-updates-switch").hidden && card.querySelector(".mods-update-busy").hidden,
  "switch line or busy mark without a reason");
await land(updates({}, "OK", true));
check(!card.querySelector(".mods-update-busy").hidden && byId("overview-check-now").disabled, "running check");
await land(updates());
byId("overview-check-now").click(); await wait(30);
same(page.requests.at(-1), true, "Check now sends a forced request");
window.ServerManUpdateStatus.setAutomaticChecks(false);
await land(updates({}, "STALE"));
check(!card.querySelector(".overview-updates-switch").hidden
  && card.querySelector(".overview-updates-switch").textContent === "Automatic checks are off."
  && summary().textContent === "Could not check: automatic checks are off", "switch off");
window.ServerManUpdateStatus.setAutomaticChecks(true);
byId("overview-open-mods").click(); await wait(40);
same(shellState.section, "mods", "Open Mods");
// Last backup: the newest entry; read after the page is drawn; every other answer has its sentence.
const backup = () => [...byId("overview-backup").querySelector(".overview-card-body").children]
  .map((node) => node.textContent).join("|");
let release;
page.backupAnswer = new Promise((resolve) => { release = resolve; });
commitSection("overview"); await wait(80);
check(byId("overview-server") && region().getAttribute("aria-busy") === "false" && backup() === "Checking…",
  "the page waited for the backup history");
release(ok({profile_id: "alpha", backups: page.backups, runtime_profile: page.runtime})); await wait(20);
check(backup().endsWith("|Verified · 412 files · 1.2 GB|Restore ready") && !backup().includes("1 file"), backup());
page.backupAnswer = null;
const reads = page.backupReads;
await push(record("made", "CREATE_BACKUP", "SUCCEEDED", {progress_phase: "complete", result: {}})); await wait(30);
same(page.backupReads, reads + 1, "backup reads after a finished backup");
page.backups = [{...page.backups[1], restore_compatibility: "LEGACY_PROFILE_SCHEMA"}]; page.runtime = null;
await reopen();
check(backup().endsWith("|Cannot be restored|Set a runtime profile directory in Profiles to make backups."), backup());
page.backups = []; page.runtime = "x";
await reopen();
same(backup(), "No backup yet", "empty history");
page.backupAnswer = fail("STORAGE_FAILURE", "x");
await reopen();
same(backup(), "Backup history could not be read.", "failed read");
page.backupAnswer = null;
button("Open Backups").click(); await wait(40);
same(shellState.section, "backups", "Open Backups");
// Schedule: summary, status, the open-manager note; every control works inside the disclosure.
commitSection("overview"); await wait(80);
const schedule = byId("overview-schedule");
same(schedule.querySelector(".schedule-summary").textContent, "Save & Restart daily at 04:00", "schedule summary");
same(schedule.querySelector(".schedule-status").textContent,
  "Next: 2026-10-05 04:00 local time. Last run was queued.", "schedule status");
check(schedule.querySelector(".schedule-status").getAttribute("role") === "status"
  && schedule.textContent.includes("Runs at local time, only while DayZ-ServerMan is open."), "status role or note");
const editor = schedule.querySelector("details.schedule-editor");
check(!editor.open && editor.querySelector("summary").textContent === "Change schedule", "closed editor");
editor.open = true;
const [hour, minute] = editor.querySelectorAll("input[type=number]");
const [stop, restart] = editor.querySelectorAll("input[type=checkbox]");
check(hour.value === "4" && minute.value === "00" && restart.checked && !stop.checked
  && editor.textContent.includes("Hour") && editor.textContent.includes("Minute"), "editor values");
stop.click();
check(stop.checked && !restart.checked, "the two actions are not exclusive");
hour.value = "99"; button("Save schedule", editor).click(); await wait(20);
check(schedule.querySelector(".schedule-status").textContent.startsWith("Enter an hour") && editor.open
  && page.saved.length === 0, "an invalid time was saved");
hour.value = "6"; minute.value = "30"; button("Save schedule", editor).click(); await wait(30);
same(JSON.stringify(page.saved), '[["alpha",6,30,"stop"]]', "saved schedule");
same(schedule.querySelector(".schedule-summary").textContent, "Save & Stop daily at 06:30", "summary after the save");
stop.click(); button("Save schedule", editor).click(); await wait(30);
check(page.saved.at(-1)[3] === null && schedule.querySelector(".schedule-summary").textContent === "No scheduled action"
  && editor.querySelector("summary").textContent === "Set schedule"
  && schedule.querySelector(".schedule-status").textContent.startsWith("No timed action is enabled."), "disabled schedule");
page.schedule = {action: "stop", hour: 4, minute: 0, last_status: "SKIPPED_NOT_RUNNING"};
await reopen();
check(byId("overview-schedule").querySelector(".schedule-status").textContent.endsWith(
  " Last run was skipped because this server was not running under the manager."), "skipped wording");
"""

# The card grid: three, two (the schedule spans both) or one column, by the width of the workspace
GRID = HEAD + r"""
await start(); await wait(80);
const boxes = [...region().querySelectorAll(".overview-card")].map((card) => card.getBoundingClientRect());
const columns = new Set(boxes.map((box) => Math.round(box.left))).size;
const workspace = document.querySelector(".workspace").getBoundingClientRect().width;
const wanted = workspace >= 980 ? 3 : workspace >= 660 ? 2 : 1;
same(columns, wanted, `columns at a workspace of ${workspace} px`);
if (wanted === 2) check(boxes[2].width > boxes[0].width * 1.5, "the schedule card does not span both columns");
const panel = byId("overview-server").getBoundingClientRect();
const controls = region().querySelector(".overview-controls").getBoundingClientRect();
const state = region().querySelector(".overview-server-state").getBoundingClientRect();
same(controls.top >= state.bottom, workspace < 860, "the controls wrap under the state");
check(document.documentElement.scrollWidth <= window.innerWidth && panel.right <= window.innerWidth,
  "the page scrolls sideways");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OverviewReworkDynamicTests(unittest.TestCase):
    """Criterion 12 and the Overview half of criterion 6."""

    def test_notices_state_uptime_process_details_and_control_reasons(self) -> None:
        """The state once, the uptime, the process disclosure, the reasons, and the notices."""
        self.assertEqual(run_shell_harness(SERVER, window_size="1500,900", budget=15000), "PASS")

    def test_cards_agree_with_mods_and_keep_every_schedule_control(self) -> None:
        """Updates agree with badge and Mods; the backup card loads late; the schedule editor works."""
        self.assertEqual(run_shell_harness(CARDS, window_size="1500,900", budget=25000), "PASS")

    def test_card_grid_follows_the_workspace_width(self) -> None:
        """Three, two and one column; the controls wrap under the state in a narrow workspace."""
        for size in ("1500,900", "1166,900", "900,900", "700,900", "620,900"):
            with self.subTest(size=size):
                self.assertEqual(run_shell_harness(GRID, window_size=size, budget=6000), "PASS")


if __name__ == "__main__":
    unittest.main()
