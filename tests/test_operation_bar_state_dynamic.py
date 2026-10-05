"""Headless Edge checks of the operation bar state: snapshot rebuild, announcements, shutdown, host error."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# Snapshot rebuild, dismissal memory, announcements, page suppression, shutdown, and host error
REBUILD_AND_ANNOUNCE = r"""
const polite = () => document.getElementById("operation-announcer").textContent;
const alertText = () => document.getElementById("operation-alert").textContent;
host.snapshotOperations = [
  record("old-fail", "CREATE_BACKUP", "FAILED", {finished_at: "2026-10-03T09:00:05.000+00:00",
    progress_phase: "failed", terminal_error: {code: "STORAGE_FAILURE", message: "Backup storage failed."}}),
  record("old-ok", "SAVE_SETTINGS", "SUCCEEDED", {finished_at: "2026-10-03T09:00:09.000+00:00",
    progress_phase: "complete", result: {}}),
  record("live", "VERIFY_WORKSHOP_FILES", "RUNNING", {cancellable: true, progress_phase: "verify_source",
    progress_percent: 10}),
];
host.operations.set("live", host.snapshotOperations[2]);
await start();
const rows = () => [...bar().querySelectorAll(".operation-row")];
check(rows().length === 2 && rows()[0].textContent.includes("The backup could not be created.")
  && rows()[1].textContent.includes("Verifying mod files"), `snapshot rows ${barText()}`);
check(document.querySelector(".overview-controls"), "the page was not opened below the bar");
check(polite() === "" && alertText() === "", "a snapshot load announced something");
check(document.body.dataset.shellReady === "true"
  && document.querySelector(".overview-controls, .operation-bar:not([hidden])"), "smoke probe selector");
// A dismissed result stays dismissed after a snapshot reload in the same window.
rows()[0].querySelector('[data-bar-control^="dismiss"]').click();
await loadSnapshot(); window.clearTimeout(shellState.pollTimer); await wait(20);
check(rows().length === 1 && !barText().includes("could not be created"), "dismissal was forgotten");
// With no active operation the newest finished operation decides the success row.
host.snapshotOperations = host.snapshotOperations.slice(0, 2);
await loadSnapshot(); window.clearTimeout(shellState.pollTimer); await wait(20);
check(rows().length === 1 && barText().includes("Application locations saved."), "rebuilt success row");
// Start announcement with the target, then a quarter step, then nothing within the pause.
await push(record("start", "RESTART_SERVER", "RUNNING", {target_profile_id: "bravo", cancellable: true,
  progress_phase: "BACKUP_DISCOVER", progress_percent: 26, last_working_phase: "BACKUP_DISCOVER"}));
check(polite() === "Restarting server started for Bravo.", `start announcement: ${polite()}`);
await push(record("start", "RESTART_SERVER", "RUNNING", {target_profile_id: "bravo", cancellable: true,
  progress_phase: "BACKUP_STAGE", progress_percent: 30, last_working_phase: "BACKUP_STAGE"}));
check(polite() === "Restarting server: 30 percent", `percent announcement: ${polite()}`);
await push(record("start", "RESTART_SERVER", "RUNNING", {target_profile_id: "bravo", cancellable: true,
  progress_phase: "BACKUP_HASH", progress_percent: 55, last_working_phase: "BACKUP_HASH"}));
check(polite() === "Restarting server: 30 percent", "a second step was announced within the pause");
await push(record("start", "RESTART_SERVER", "CANCELLING", {target_profile_id: "bravo", cancellable: true,
  progress_phase: "BACKUP_HASH", progress_percent: 55, last_working_phase: "BACKUP_HASH"}));
check(polite() === "Cancelling Restarting server.", `cancelling announcement: ${polite()}`);
// A failure is announced through the alert element with sentence and message.
await push(record("start", "RESTART_SERVER", "FAILED", {target_profile_id: "bravo", progress_phase: "failed",
  last_working_phase: "BACKUP_HASH", terminal_error: {code: "STORAGE_FAILURE", message: "Backup storage failed."}}));
check(alertText() === "Server stopped, but the backup failed. The server was not started again. Backup storage failed.",
  `failure announcement: ${alertText()}`);
// A page that shows its own live notice for the result silences the bar for that operation.
window.ServerManSections.register("notice", {title: "Notice", description: "Page with its own result.",
  open: () => {}, operationFinished: (operation) => {
    if (operation.state === "SUCCEEDED") window.ServerManOperationBar.pageResult(operation);
    return true; }});
commitSection("notice");
await push(record("quiet", "CREATE_BACKUP", "RUNNING", {progress_phase: "DISCOVER", progress_percent: 5}));
check(polite() === "Creating backup started.", `second start: ${polite()}`);
await push(record("quiet", "CREATE_BACKUP", "SUCCEEDED", {progress_phase: "complete", result: {}}));
check(polite() === "Creating backup started." && barText().includes("Backup created."),
  `a page-announced result was announced again: ${polite()}`);
// A handler that consumed the event without a notice does not silence the bar.
await push(record("loud", "CREATE_BACKUP", "CANCELLED", {progress_phase: "cancelled"}));
check(polite() === "Backup cancelled.", `result announcement: ${polite()}`);
// Shutdown: the panel replaces the page, the bar keeps the finishing operation without Cancel or queue.
commitSection("overview"); await wait(20);
await push(record("closing", "CREATE_BACKUP", "RUNNING", {cancellable: true, progress_phase: "STAGE",
  progress_percent: 40}));
await push(record("waits", "SAVE_SETTINGS", "QUEUED", {cancellable: true,
  accepted_at: "2026-10-03T10:05:00.000+00:00"}));
check(barButton("Cancel") && barButton("+1 waiting"), "controls before shutdown");
window.ServerManUi.renderShutdown({state: "DRAINING", blocking_reason: null});
check(document.getElementById("content-region").textContent.includes("Closing DayZ-ServerMan safely"),
  "shutdown panel");
check(!bar().hidden && barText().includes("Creating backup") && !barButton("Cancel")
  && !barButton("+1 waiting"), "bar during shutdown");
check(document.body.dataset.operationBusy === "true", "busy during shutdown");
"""

# Host error panel: the active row hides, the problem row stays, the next record brings the row back
HOST_ERROR = r"""
await start();
await push(record("bad", "SAVE_PROFILE", "FAILED", {progress_phase: "failed",
  terminal_error: {code: "NOT_FOUND", message: "The selected profile was not found."}}));
await push(record("work", "CREATE_BACKUP", "RUNNING", {cancellable: true, progress_phase: "STAGE",
  progress_percent: 40}));
check(bar().querySelectorAll(".operation-row").length === 2, "rows before the host error");
window.ServerManUi.renderHostError(fail("INTERNAL_FAILURE", "The operation could not be completed."));
check(document.querySelector('#content-region [role="alert"]'), "host error panel");
check(bar().querySelectorAll(".operation-row").length === 1
  && barText().includes("The profile could not be saved.")
  && barText().includes("The selected profile was not found.")
  && !barText().includes("Creating backup"), `bar during a host error: ${barText()}`);
check(document.body.dataset.operationBusy === "true", "the busy lock was released by a host error");
await push(record("work", "CREATE_BACKUP", "RUNNING", {cancellable: true, progress_phase: "HASH",
  progress_percent: 60}));
check(bar().querySelectorAll(".operation-row").length === 2 && barText().includes("Writing the file list"),
  "the next record did not bring the active row back");
// A submit acknowledgement shows the operation at once, and an older copy never replaces a newer record.
host.operations.set("adopted", record("adopted", "SAVE_SETTINGS", "QUEUED", {revision: 3, cancellable: true,
  accepted_at: "2026-10-03T10:09:00.000+00:00"}));
await window.ServerManOperationBar.adopt("adopted");
check(barButton("+1 waiting"), "adopt did not add the accepted operation");
window.ServerManOperationBar.sync(record("work", "CREATE_BACKUP", "RUNNING", {revision: 1,
  progress_phase: "DISCOVER", progress_percent: 5}));
check(barText().includes("Writing the file list"), "an older record replaced a newer one");
"""


# Refused Cancel (QF-071): the plain note in the row is also spoken through the alert element; focus stays on Cancel
REFUSED_CANCEL = r"""
await start();
const alertNode = document.getElementById("operation-alert");
const spoken = [];
new MutationObserver(() => spoken.push(alertNode.textContent))
  .observe(alertNode, {childList: true, characterData: true, subtree: true});
await push(record("run", "UPDATE_WORKSHOP_ITEMS", "RUNNING", {cancellable: true, progress_phase: "verify_items",
  progress_percent: 75}));
// An accepted cancellation speaks nothing through the alert element.
barButton("Cancel").click(); await wait(20);
check(spoken.length === 0 && barButton("Cancelling…"), `an accepted cancellation was alerted: ${spoken}`);
await push(record("run", "UPDATE_WORKSHOP_ITEMS", "RUNNING", {cancellable: true, progress_phase: "verify_items",
  progress_percent: 80}));
// A refused cancellation shows the note and speaks it assertively, without moving the focus.
host.cancelAnswer = () => fail("OPERATION_NOT_CANCELLABLE", "operation is not cancellable in its current state");
barButton("Cancel").focus(); barButton("Cancel").click(); await wait(20);
const note = "This operation can no longer be cancelled.";
check(bar().querySelector(".operation-note")?.textContent === note, "note missing");
check(alertNode.textContent === note && spoken.at(-1) === note, `refusal not alerted: ${JSON.stringify(spoken)}`);
check(document.activeElement === barButton("Cancel")
  && !barButton("Cancel").hasAttribute("aria-disabled"), "focus left Cancel or Cancel stayed locked");
// A second refusal is spoken again.
const before = spoken.length;
barButton("Cancel").click(); await wait(20);
check(spoken.length > before && alertNode.textContent === note, "a second refusal was not alerted");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OperationBarStateDynamicTests(unittest.TestCase):
    """Specification sections 1, 4 and 6: rebuild, announcements, and the two whole-workspace panels."""

    def test_snapshot_rebuild_announcements_and_shutdown(self) -> None:
        """Rebuild from a snapshot, dismissal memory, announcements, page suppression, shutdown."""
        self.assertEqual(run_shell_harness(REBUILD_AND_ANNOUNCE, budget=6000), "PASS")

    def test_host_error_and_submit_acknowledgement(self) -> None:
        """Host error hides only the active row; adopt and the revision rule."""
        self.assertEqual(run_shell_harness(HOST_ERROR), "PASS")

    def test_refused_cancel_is_announced_and_keeps_focus(self) -> None:
        """QF-071: a refused Cancel is spoken through the alert element; Cancel keeps the focus."""
        self.assertEqual(run_shell_harness(REFUSED_CANCEL), "PASS")


if __name__ == "__main__":
    unittest.main()
