"""Headless Edge checks of the persistent operation bar: states, results, queue, Cancel, keys, and width."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# Every bar state on Overview, result persistence, and the rule for success and failure rows
STATES = r"""
const statusText = () => document.getElementById("application-status-text").textContent;
const rows = () => [...bar().querySelectorAll(".operation-row")];
const progress = () => bar().querySelector('[role="progressbar"]');
await start();
check(bar().hidden, "the bar is visible without an operation");
check(document.querySelector(".overview-controls"), "Overview did not render");
// Queued: waiting text, indeterminate progress, Cancel, target chip for another profile.
await push(record("backup", "CREATE_BACKUP", "QUEUED", {cancellable: true, target_profile_id: "bravo"}));
check(!bar().hidden, "the bar stayed hidden for a queued operation");
check(document.querySelector(".overview-controls"), "the page was replaced by the operation");
check(!document.querySelector(".operation-panel"), "the old operation panel is still drawn");
check(rows().length === 1 && rows()[0].classList.contains("is-queued"), "queued look");
check(barText().includes("Creating backup") && barText().includes("Waiting to start"), "queued text");
check(bar().querySelector(".operation-target").textContent === "Bravo", "target chip of another profile");
check(!progress().hasAttribute("aria-valuenow") && progress().getAttribute("aria-valuetext") === "Waiting to start"
  && progress().getAttribute("aria-label") === "Creating backup progress", "queued progress semantics");
check(progress().classList.contains("is-indeterminate") && !bar().querySelector(".operation-percent"),
  "queued progress is not indeterminate");
check(barButton("Cancel") && !barButton("Cancel").hasAttribute("aria-disabled"), "queued Cancel");
check(document.body.dataset.operationBusy === "true", "busy marker");
check(document.getElementById("busy-reason").textContent
  === "Not available while “Creating backup” is in progress.", "busy reason");
check(statusText() === "Working: Creating backup", `status ${statusText()}`);
// Running, determinate: phase text, percent, no chip for the selected profile, no raw phase.
await push(record("backup", "CREATE_BACKUP", "RUNNING", {cancellable: true, target_profile_id: "alpha",
  progress_phase: "STAGE", progress_percent: 42, last_working_phase: "STAGE"}));
check(rows()[0].classList.contains("is-running"), "running look");
check(barText().includes("Checking copied files") && !barText().includes("STAGE"), "running phase text");
check(bar().querySelector(".operation-percent").textContent === "42%"
  && progress().getAttribute("aria-valuenow") === "42" && !progress().hasAttribute("aria-valuetext"),
  "determinate progress");
check(bar().querySelector(".operation-progress .progress-bar").style.width === "42%", "fill width");
check(!bar().querySelector(".operation-target"), "chip shown for the selected profile");
// Running with a phase that the catalogue does not know: fallback text, indeterminate, nothing raw.
await push(record("backup", "CREATE_BACKUP", "RUNNING", {cancellable: true, progress_phase: "brand_new_phase",
  progress_percent: 50}));
check(bar().querySelector(".operation-phase").textContent === "Working"
  && !barText().includes("brand_new_phase") && !bar().querySelector(".operation-percent"), "unknown phase");
// Cancelling: warning look, fixed text, locked Cancel with its own label.
await push(record("backup", "CREATE_BACKUP", "CANCELLING", {cancellable: true, progress_phase: "STAGE",
  progress_percent: 42}));
check(rows()[0].classList.contains("is-cancelling")
  && barText().includes("Stopping at the next safe moment"), "cancelling look");
check(barButton("Cancelling…")?.getAttribute("aria-disabled") === "true" && !barButton("Cancel"),
  "cancelling button");
// Cancelled: result row that stays; busy ends.
await push(record("backup", "CREATE_BACKUP", "CANCELLED", {progress_phase: "cancelled",
  finished_at: "2026-10-03T10:01:00.000+00:00"}));
check(rows().length === 1 && rows()[0].classList.contains("is-cancelled")
  && rows()[0].classList.contains("operation-result"), "cancelled look");
check(barText().includes("Backup cancelled.") && barButton("View in Logs") && barButton("Dismiss"),
  "cancelled row content");
check(!("operationBusy" in document.body.dataset) && statusText() === "Ready", "busy after the end");
await wait(1600);
check(!bar().hidden && barText().includes("Backup cancelled."), "the result was dismissed by itself");
// Dismiss: the row goes, the bar hides, focus lands on the page.
barButton("Dismiss").focus(); barButton("Dismiss").click();
check(bar().hidden && document.activeElement.id === "main-content", "dismiss");
// Success: stays until another operation starts.
await push(record("save", "SAVE_SETTINGS", "RUNNING", {progress_phase: "running"}));
check(barText().includes("Saving application locations") && barText().includes("Starting")
  && !barButton("Cancel"), "running without Cancel");
await push(record("save", "SAVE_SETTINGS", "SUCCEEDED", {progress_phase: "complete", result: {}}));
check(rows().length === 1 && rows()[0].classList.contains("is-success")
  && barText().includes("Application locations saved."), "success row");
await push(record("steam", "SAVE_STEAM_SETTINGS", "QUEUED", {cancellable: true}));
check(rows().length === 1 && !barText().includes("Application locations saved.")
  && barText().includes("Saving Steam sign-in settings"), "success was not replaced by the next start");
// Failure: sentence and wrapped host message; it stays above the next operation.
await push(record("steam", "SAVE_STEAM_SETTINGS", "FAILED", {progress_phase: "failed",
  terminal_error: {code: "INVALID_REQUEST", message: "steam_account_name is required in ACCOUNT mode"}}));
check(rows()[0].classList.contains("is-failed") && barText().includes(
  "The Steam sign-in settings could not be saved.") && barText().includes(
  "Enter a Steam account name for account sign-in.") && !barText().includes("steam_account_name"),
  `failed row: ${barText()}`);
check(statusText() === "Last operation failed", `failed status ${statusText()}`);
await push(record("profile", "SAVE_PROFILE", "RUNNING", {progress_phase: "running"}));
check(rows().length === 2 && rows()[0].classList.contains("is-failed")
  && rows()[1].classList.contains("is-running"), "the failure did not stay above the active row");
await push(record("profile", "SAVE_PROFILE", "SUCCEEDED", {progress_phase: "complete", result: {}}));
check(rows().length === 2 && rows()[0].classList.contains("is-failed")
  && rows()[1].classList.contains("is-success"), "failure and success rows");
// Recovery: replaces the older problem; one problem row at most.
await push(record("restore", "RESTORE_BACKUP", "RUNNING", {progress_phase: "WRITE_JOURNAL", progress_percent: 80}));
check(progress().classList.contains("is-indeterminate") && barText().includes("Replacing server files"),
  "indeterminate working phase");
await push(record("restore", "RESTORE_BACKUP", "RECOVERY_REQUIRED", {progress_phase: "failed",
  terminal_error: {code: "RECOVERY_REQUIRED", message: "Mutations are blocked until restore recovery is inspected."}}));
check(rows().length === 1 && rows()[0].classList.contains("is-recovery"), "recovery row");
check(barText().includes("The backup could not be restored. Changes are blocked.")
  && barText().includes("An earlier operation did not finish cleanly."), `recovery text ${barText()}`);
check(statusText() === "Recovery required", `recovery status ${statusText()}`);
// Results of a mod update that ran to its end with problems use the failed look.
await push(record("update", "UPDATE_WORKSHOP_ITEMS", "SUCCEEDED", {progress_phase: "complete",
  result: {download_state: "UNKNOWN", items: []}}));
check(rows().length === 1 && rows()[0].classList.contains("is-failed")
  && barText().includes("The mod update could not be confirmed."), "update result look");
check(!/[A-Z]{2,}_[A-Z_]+/.test(barText()), `raw identifier in the bar: ${barText()}`);
"""

# Queue list, Cancel wiring, keyboard handling, focus rules, and the link to Logs
QUEUE_AND_KEYS = r"""
await start();
const queue = () => document.getElementById("operation-queue");
await push(record("run", "UPDATE_WORKSHOP_ITEMS", "RUNNING", {cancellable: true,
  progress_phase: "verify_items", progress_percent: 75, accepted_at: "2026-10-03T10:00:00.000+00:00"}));
check(!barButton("+1 waiting"), "queue button without a queue");
await push(record("first", "CREATE_BACKUP", "QUEUED", {cancellable: true,
  accepted_at: "2026-10-03T10:00:01.000+00:00"}));
await push(record("second", "SAVE_PROFILE", "QUEUED", {cancellable: true, target_profile_id: "bravo",
  accepted_at: "2026-10-03T10:00:02.000+00:00"}));
const waiting = barButton("+2 waiting");
check(waiting?.getAttribute("aria-expanded") === "false"
  && waiting.getAttribute("aria-controls") === "operation-queue" && !queue(), "queue button");
check(barText().includes("Updating mods") && barText().includes("Verifying downloaded mods"),
  "the running operation is not in the active row");
waiting.click();
check(barButton("+2 waiting").getAttribute("aria-expanded") === "true", "expanded state");
const items = [...queue().querySelectorAll("li")];
check(items.length === 2 && items[0].textContent.includes("Creating backup")
  && items[0].textContent.includes("Waiting") && items[1].textContent.includes("Saving profile")
  && items[1].textContent.includes("Bravo"), `queue list ${queue().textContent}`);
// A queued operation is cancelled from its own line.
host.cancelAnswer = (id) => ok({...host.operations.get(id), state: "CANCELLED", progress_phase: "cancelled",
  revision: 50});
items[1].querySelector("button").click(); await wait(10);
check(JSON.stringify(host.cancelRequests) === '["second"]', `queued cancel ${host.cancelRequests}`);
check(barButton("+1 waiting") && barText().includes("Profile save cancelled."), "queue count after cancel");
// Escape inside the bar closes the queue list first.
barButton("+1 waiting").focus();
document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
check(!queue() && barText().includes("Profile save cancelled."), "Escape did not close the queue list");
// F6 moves the focus into the bar and back to where it was.
const pageControl = document.getElementById("backup-after-stop");
pageControl.focus();
document.dispatchEvent(new KeyboardEvent("keydown", {key: "F6", bubbles: true}));
check(bar().contains(document.activeElement) && document.activeElement === bar().querySelector("button"),
  "F6 did not focus the first bar button");
document.dispatchEvent(new KeyboardEvent("keydown", {key: "F6", bubbles: true}));
check(document.activeElement === pageControl, "F6 did not return the focus");
// Cancel on the running operation: one request, then a locked "Cancelling…" button.
host.cancelAnswer = null;
barButton("Cancel").click(); await wait(10);
check(host.cancelRequests.at(-1) === "run" && host.cancelRequests.length === 2, "running cancel request");
check(barButton("Cancelling…").getAttribute("aria-disabled") === "true", "Cancel stayed enabled");
barButton("Cancelling…").click(); await wait(10);
check(host.cancelRequests.length === 2, "a locked Cancel sent a second request");
// When the operation ends while Cancel holds the focus, the focus moves to Dismiss of its result.
barButton("Cancelling…").focus();
await push(record("run", "UPDATE_WORKSHOP_ITEMS", "CANCELLED", {progress_phase: "cancelled"}));
check(document.activeElement.textContent === "Dismiss"
  && document.activeElement.closest(".operation-row").textContent.includes("Mod update cancelled."),
  "focus did not move from Cancel to Dismiss");
// A refused cancellation is explained in the row and Cancel is offered again.
check(barText().includes("Creating backup") && barText().includes("Waiting to start"), "next queued row");
host.cancelAnswer = () => fail("OPERATION_NOT_CANCELLABLE", "operation is not cancellable in its current state");
barButton("Cancel").click(); await wait(10);
check(bar().querySelector(".operation-note")?.textContent === "This operation can no longer be cancelled."
  && barButton("Cancel") && !barButton("Cancel").hasAttribute("aria-disabled"), "refused cancel");
// Escape on a result row dismisses that row only; it never cancels.
const requests = host.cancelRequests.length;
barButton("View in Logs").focus();
document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
check(!barText().includes("Mod update cancelled.") && barText().includes("Creating backup")
  && host.cancelRequests.length === requests, "Escape on a result row");
// View in Logs opens Logs on the activity source through the normal section switch.
await push(record("first", "CREATE_BACKUP", "FAILED", {progress_phase: "failed",
  last_working_phase: "HASH", terminal_error: {code: "STORAGE_FAILURE", message: "Backup storage failed."}}));
barButton("View in Logs").click(); await wait(30);
check(shellState.section === "logs" && logState.source === "manager"
  && document.getElementById("page-title").textContent === "Logs", "View in Logs");
check(!bar().hidden && barText().includes("The backup could not be created.")
  && barText().includes("Backup storage failed."),
  "the result did not survive the page change");
"""

# Narrow window: two-line active row, three-line result row, no sideways overflow, status dot on the menu button
NARROW = r"""
await start();
await push(record("run", "UPDATE_WORKSHOP_ITEMS", "RUNNING", {cancellable: true, target_profile_id: "bravo",
  progress_phase: "verify_items", progress_percent: 75}));
await push(record("next", "CREATE_BACKUP", "QUEUED", {cancellable: true,
  accepted_at: "2026-10-03T10:00:01.000+00:00"}));
const box = (selector) => bar().querySelector(selector).getBoundingClientRect();
check(window.innerWidth < 860, `the window is not narrow: ${window.innerWidth}`);
check(getComputedStyle(document.getElementById("menu-button")).display !== "none", "menu button hidden");
check(box(".operation-detail").top >= box(".operation-heading").bottom - 1, "phase and progress share the first line");
check(Math.abs(box(".operation-actions").top - box(".operation-heading").top) < 16
  && box(".operation-actions").right > window.innerWidth - 40, "buttons are not right on the first line");
check(box(".operation-progress").width > 300, `the track did not use the line: ${box(".operation-progress").width}`);
check(document.documentElement.scrollWidth <= window.innerWidth, "sideways overflow");
check(bar().getBoundingClientRect().top === 0 && getComputedStyle(bar()).position === "sticky", "bar position");
const dot = document.getElementById("menu-status-dot");
check(!dot.hidden && dot.classList.contains("is-busy"), "menu status dot");
check(document.getElementById("menu-button").getAttribute("aria-label")
  === "Open navigation. Application status: Working: Updating mods", "menu button label");
// Result row: sentence and message first, buttons below them on the left.
await push(record("next", "CREATE_BACKUP", "CANCELLED", {progress_phase: "cancelled"}));
await push(record("run", "UPDATE_WORKSHOP_ITEMS", "FAILED", {progress_phase: "failed",
  terminal_error: {code: "STEAMCMD_UNAVAILABLE", message: "SteamCMD or Workshop path identity changed. Reload settings and retry."}}));
const result = bar().querySelector(".operation-result");
const copy = result.querySelector(".operation-copy").getBoundingClientRect();
const actions = result.querySelector(".operation-actions").getBoundingClientRect();
check(actions.top >= copy.bottom - 1 && actions.left < 60, "result buttons are not below the text on the left");
check(result.textContent.includes("Check the folders in Settings"), "wrapped message");
check(document.documentElement.scrollWidth <= window.innerWidth, "sideways overflow with a result row");
check(dot.classList.contains("is-error"), "menu dot after a failure");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OperationBarDynamicTests(unittest.TestCase):
    """Criterion 10: the page stays, the bar shows the operation, and the result stays until dismissed."""

    def test_every_state_keeps_the_page_and_results_persist(self) -> None:
        """Queued, running, cancelling, and each result look; dismiss; success replaced, failure kept."""
        self.assertEqual(run_shell_harness(STATES), "PASS")

    def test_queue_cancel_keys_focus_and_logs_link(self) -> None:
        """Queue count and list, Cancel wiring and lock, refused cancel, F6, Escape, focus, View in Logs."""
        self.assertEqual(run_shell_harness(QUEUE_AND_KEYS), "PASS")

    def test_narrow_window_layout(self) -> None:
        """At 700 px the rows wrap as specified and the menu button carries the status."""
        self.assertEqual(run_shell_harness(NARROW, window_size="700,900"), "PASS")


if __name__ == "__main__":
    unittest.main()
