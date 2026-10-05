"""Headless Edge checks of the Overview status board (variant C, D17): rows, the schedule editor row, and QF-056."""
from __future__ import annotations

import unittest

try:
    from tests.overview_harness import OVERVIEW_HEAD
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from overview_harness import OVERVIEW_HEAD
    from ui_harness_support import EDGE, run_shell_harness


# Updates and last backup rows; the Mods row agrees with the badge and the Mods header
BOARD = OVERVIEW_HEAD + r"""
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
// One board named by a visually hidden "Status" heading and no visible group heads (QF-059, T4); every visible row
// has an icon, a title, a body and an action cell.
const board = region().querySelector(".overview-board");
const boardTitle = board.firstElementChild;
check(boardTitle.tagName === "H2" && boardTitle.classList.contains("sr-only") && boardTitle.textContent === "Status"
  && board.getAttribute("aria-labelledby") === boardTitle.id && !board.hasAttribute("aria-label")
  && board.querySelectorAll("h2").length === 1 && board.querySelectorAll(".overview-list").length === 2, "board heading");
const rows = [...board.querySelectorAll(".overview-row")].filter((row) => !row.hidden);
same(rows.map((row) => row.querySelector("h3").textContent).join("|"), "Mods|Last backup|Next scheduled action", "rows");
check(rows.every((row) => row.tagName === "LI" && row.querySelector("svg[aria-hidden='true']")
  && row.querySelector(".overview-row-body") && row.querySelector(".overview-row-action button")), "row columns");
// T5: "Check now" is a link button right after the last check in the Mods row, with its name and title.
const checkNow = byId("overview-check-now");
check(checkNow.tagName === "BUTTON" && checkNow.type === "button" && checkNow.textContent === "Check now"
  && checkNow.classList.contains("link-button")
  && checkNow.previousElementSibling === byId("overview-updates").querySelector(".overview-updates-checked")
  && checkNow.title === "Check Steam now for mod updates and the DayZ server build", "Check now place or name");
// Keyboard order: process details, the lifecycle controls that are on, then the board from top to bottom.
same([...region().querySelectorAll("button, input")].filter((node) => !node.disabled && node.getClientRects().length)
  .map((node) => node.textContent || node.id).join("|"),
  "Process details|backup-after-stop|Start server|Check now|Open Mods|Open Backups|Change schedule", "focus order");
// Mods: the Mods header sentence with "mod" named (QF-049), and a primary way to Mods.
const summary = () => byId("overview-updates").querySelector(".overview-updates-summary");
const reason = () => byId("overview-updates").querySelector(".overview-updates-reason");
same(summary().textContent, "2 mod updates available · 1 downloaded - not applied", "updates summary");
check(summary().classList.contains("status-warning") && reason().hidden, "summary tone or a reason line");
same(document.querySelector('.nav-item[data-section="mods"] .nav-badge').textContent, "3", "badge");
check(byId("overview-updates").textContent.includes("Last checked ")
  && byId("overview-open-mods").classList.contains("button-primary"), "last check or primary link");
commitSection("mods"); await wait(100);
same(byId("mods-update-summary").textContent, "2 updates available · 1 downloaded - not applied", "Mods header");
commitSection("overview"); await wait(80);
// The row follows the update state in place: nothing pending, a running check, the switch, "Check now".
const card = byId("overview-updates");
const land = async (next) => { host.updates = next; updateStatusState.readAt -= 31000;
  await window.ServerManUpdateStatus.poll(false); await wait(10); };
await land(updates());
check(byId("overview-updates") === card && summary().textContent === "No mod updates available"
  && !byId("overview-open-mods").classList.contains("button-primary"), "row after the updates were applied");
check(card.querySelector(".overview-updates-switch").hidden && card.querySelector(".mods-update-busy").hidden,
  "switch line or busy mark without a reason");
// QF-054: a mod whose content folder is missing is counted in the row, the badge and its spoken name.
await land(updates({not_downloaded_count: 1}));
const nav = (selector) => document.querySelector(`.nav-item[data-section="mods"] ${selector}`).textContent;
check(summary().textContent === "1 not downloaded" && summary().classList.contains("status-warning")
  && nav(".nav-badge") === "1" && nav(".nav-badge-name") === ", 1 not downloaded"
  && byId("overview-open-mods").classList.contains("button-primary"), `not downloaded: ${nav(".nav-badge-name")}`);
await land(updates({}, "OK", true));
check(!card.querySelector(".mods-update-busy").hidden && byId("overview-check-now").disabled, "running check");
await land(updates());
byId("overview-check-now").click(); await wait(30);
same(page.requests.at(-1), true, "Check now sends a forced request");
// A failed check: a short bold head and the reason in normal weight on its own line (D17).
await land({...updates({}, "FAILED"), mods: {...updates({}, "FAILED").mods, error_code: "TIMEOUT"}});
check(summary().textContent === "Could not check" && summary().classList.contains("status-error")
  && !reason().hidden && reason().textContent === "Steam did not answer in time.", `failed: ${reason().textContent}`);
// Pending updates and a failed check: the head names the updates, the reason line the failed check.
await land({...updates({update_count: 1}, "FAILED"), mods: {...updates({update_count: 1}, "FAILED").mods,
  error_code: "TIMEOUT"}});
same(`${summary().textContent}|${reason().textContent}`, "1 mod update available|Could not check: Steam did not answer in time.",
  "pending and failed");
// Switch off: the reason says it, so the separate switch line is not repeated; with a fresh check it is shown.
window.ServerManUpdateStatus.setAutomaticChecks(false);
await land(updates({}, "STALE"));
check(summary().textContent === "Could not check" && reason().textContent === "Automatic checks are off."
  && card.querySelector(".overview-updates-switch").hidden, "switch off with an old check");
await land(updates());
check(!card.querySelector(".overview-updates-switch").hidden
  && card.querySelector(".overview-updates-switch").textContent === "Automatic checks are off.", "switch off");
window.ServerManUpdateStatus.setAutomaticChecks(true);
byId("overview-open-mods").click(); await wait(40);
same(shellState.section, "mods", "Open Mods");
// Last backup: the newest entry; read after the page is drawn; every other answer has its sentence and tone.
const backup = () => [...byId("overview-backup").querySelectorAll(".overview-row-body p")]
  .map((node) => node.textContent).join("|");
const tone = () => byId("overview-backup").querySelector(".overview-status").className.match(/status-(\w+)$/)[1];
let release;
page.backupAnswer = new Promise((resolve) => { release = resolve; });
commitSection("overview"); await wait(80);
check(byId("overview-server") && region().getAttribute("aria-busy") === "false" && backup() === "Checking…",
  "the page waited for the backup history");
release(ok({profile_id: "alpha", backups: page.backups, runtime_profile: page.runtime})); await wait(20);
check(backup().endsWith(" · Restore ready|Verified · 412 files · 1.2 GB") && tone() === "normal", backup());
page.backupAnswer = null;
const reads = page.backupReads;
await push(record("made", "CREATE_BACKUP", "SUCCEEDED", {progress_phase: "complete", result: {}})); await wait(30);
same(page.backupReads, reads + 1, "backup reads after a finished backup");
page.backups = [{...page.backups[1], restore_compatibility: "LEGACY_PROFILE_SCHEMA"}]; page.runtime = null;
await reopen();
check(backup().endsWith(" · Cannot be restored|Verified · 412 files · 1.2 GB|Set a runtime profile directory in "
  + "Profiles to make backups.") && tone() === "warning", backup());
page.backups = []; page.runtime = "x";
await reopen();
check(backup() === "No backup yet" && tone() === "neutral", backup());
page.backupAnswer = fail("STORAGE_FAILURE", "x");
await reopen();
check(backup() === "Backup history could not be read." && tone() === "error", backup());
page.backupAnswer = null;
button("Open Backups").click(); await wait(40);
same(shellState.section, "backups", "Open Backups");
"""

# The schedule row and its editor row: every control, Cancel, the tone dot, and the failures of QF-056
SCHEDULE = OVERVIEW_HEAD + r"""
await start(); await wait(80);
const schedule = byId("overview-schedule");
const summary = () => schedule.querySelector(".schedule-summary");
const status = () => schedule.querySelector(".schedule-status");
const editor = byId("overview-schedule-editor");
const opener = byId("overview-schedule-toggle");
same(summary().textContent, "Save & Restart daily at 04:00", "schedule summary");
check(summary().classList.contains("status-normal"), "tone of a set schedule");
same(status().textContent, "Next: 2026-10-05 04:00 local time. Last run was queued.", "schedule status");
check(status().getAttribute("role") === "status"
  && schedule.textContent.includes("Runs at local time, only while DayZ-ServerMan is open."), "status role or note");
// The editor is a row under the schedule row, opened by a button that says whether it is open.
check(editor.tagName === "LI" && editor.previousElementSibling === schedule && editor.hidden
  && opener.textContent === "Change schedule" && opener.getAttribute("aria-expanded") === "false"
  && opener.getAttribute("aria-controls") === "overview-schedule-editor", "closed editor");
opener.click();
check(!editor.hidden && opener.getAttribute("aria-expanded") === "true", "the editor did not open");
const [hour, minute] = editor.querySelectorAll("input[type=number]");
const [stop, restart] = editor.querySelectorAll("input[type=checkbox]");
check(document.activeElement === hour && hour.value === "4" && minute.value === "00" && restart.checked
  && !stop.checked && editor.textContent.includes("Hour") && editor.textContent.includes("Minute"), "editor values");
// Cancel closes the row, puts the stored values back, and returns the focus to the opener.
hour.value = "9"; stop.click();
button("Cancel", editor).click();
check(editor.hidden && document.activeElement === opener && hour.value === "4" && restart.checked && !stop.checked,
  "Cancel kept the edits or lost the focus");
opener.click(); stop.click();
check(stop.checked && !restart.checked, "the two actions are not exclusive");
hour.value = "99"; button("Save schedule", editor).click(); await wait(20);
check(status().textContent.startsWith("Enter an hour") && !editor.hidden && page.saved.length === 0,
  "an invalid time was saved");
hour.value = "6"; minute.value = "30"; button("Save schedule", editor).click(); await wait(30);
same(JSON.stringify(page.saved), '[["alpha",6,30,"stop"]]', "saved schedule");
check(summary().textContent === "Save & Stop daily at 06:30" && editor.hidden, "summary or editor after the save");
// QF-062: a successful save returns the focus to the opener.
same(document.activeElement?.id, "overview-schedule-toggle", "focus after a successful save");
opener.click(); stop.click(); button("Save schedule", editor).click(); await wait(30);
check(page.saved.at(-1)[3] === null && summary().textContent === "No scheduled action"
  && summary().classList.contains("status-neutral") && opener.textContent === "Set schedule"
  && status().textContent.startsWith("No timed action is enabled."), "disabled schedule");
page.schedule = {action: "stop", hour: 4, minute: 0, last_status: "QUEUE_FAILED"};
await reopen();
check(byId("overview-schedule").querySelector(".schedule-summary").classList.contains("status-warning")
  && byId("overview-schedule").querySelector(".schedule-status").textContent.endsWith(" Last run could not be queued."),
  "a run that could not be queued");
page.schedule = {action: "stop", hour: 4, minute: 0, last_status: "SKIPPED_NOT_RUNNING"};
await reopen();
check(byId("overview-schedule").querySelector(".schedule-status").textContent.endsWith(
  " Last run was skipped because this server was not running under the manager."), "skipped wording");
// QF-056: a failed or thrown load keeps the whole page; the row says it, the application status names it.
for (const fault of ["fail", "throw"]) {
  page.scheduleFault = fault;
  await reopen();
  const row = byId("overview-schedule");
  check(byId("overview-server") && byId("overview-updates") && row && !region().textContent.includes("Could not load the workspace"),
    `${fault}: the page was replaced`);
  check(row.querySelector(".schedule-status").textContent === "The schedule could not be loaded."
    && row.querySelector(".schedule-summary").textContent === "Schedule not known"
    && byId("overview-schedule-toggle").disabled, `${fault}: load failure row`);
  same(byId("application-status-text").textContent, "Schedule could not be loaded", `${fault}: application status`);
}
page.scheduleFault = null;
await reopen();
check(byId("application-status-text").textContent !== "Schedule could not be loaded", "the load status stayed");
// A failed or thrown save keeps the page and the editor open with the edits.
const storedLine = byId("overview-schedule").querySelector(".schedule-status").textContent;
for (const fault of ["fail", "throw"]) {
  page.saveFault = fault;
  byId("overview-schedule-toggle").click();
  const row = byId("overview-schedule-editor");
  row.querySelector("input").value = "7"; button("Save schedule", row).click(); await wait(30);
  check(byId("overview-server") && !row.hidden && row.querySelector("input").value === "7"
    && !button("Save schedule", row).disabled, `${fault}: save failure editor`);
  same(byId("overview-schedule").querySelector(".schedule-status").textContent, "The schedule could not be saved.",
    `${fault}: save failure text`);
  same(byId("application-status-text").textContent, "Schedule could not be saved", `${fault}: application status`);
  // QF-060: Cancel drops the failure: the stored line and values come back and the global warning goes.
  button("Cancel", row).click();
  same(byId("overview-schedule").querySelector(".schedule-status").textContent, storedLine, `${fault}: line after Cancel`);
  same(byId("application-status-text").textContent, "Ready", `${fault}: application status after Cancel`);
  check(row.hidden && row.querySelector("input").value === "4"
    && document.activeElement === byId("overview-schedule-toggle"), `${fault}: editor after Cancel`);
}
check(storedLine.startsWith("Next: ") && page.saved.every((entry) => entry[1] !== 7), "a failed save was stored");
page.saveFault = null;
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OverviewBoardDynamicTests(unittest.TestCase):
    """Criterion 12, the Overview half of criterion 6, D17 and QF-056."""

    def test_rows_agree_with_mods_and_show_head_and_reason(self) -> None:
        """Mods row agrees with badge and Mods; failed checks split; the backup row loads late with its tone."""
        self.assertEqual(run_shell_harness(BOARD, window_size="1500,900", budget=25000), "PASS")

    def test_schedule_editor_row_and_failures_keep_the_page(self) -> None:
        """Every schedule control works in the editor row; Cancel; a failed load or save keeps the page."""
        self.assertEqual(run_shell_harness(SCHEDULE, window_size="1500,900", budget=25000), "PASS")


if __name__ == "__main__":
    unittest.main()
