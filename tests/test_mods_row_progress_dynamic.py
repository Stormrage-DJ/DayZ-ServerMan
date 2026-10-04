"""Headless Edge checks of the Mods row marks: item phases of an update, outcomes, and apply targets."""
from __future__ import annotations

import unittest

try:
    from tests.mods_update_harness import MODS_HOST
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from mods_update_harness import MODS_HOST
    from ui_harness_support import EDGE, run_shell_harness

# Design 13.7: each row state during and after the page's own operations
ROWS = MODS_HOST + r"""
await start("mods"); await wait(50);
const megabyte = 1024 * 1024;
// An update that ends before any progress record was seen still marks its rows.
await runUpdate(false, currentResult);
check(statusCells()[0].textContent.endsWith("Already current"), "result without a progress record");
commitSection("overview"); await wait(20); commitSection("mods"); await wait(60);
const cell = (index) => statusCells()[index].textContent;
const label = (index) => statusCells()[index].querySelector(".mods-status-label").textContent;
const before = [cell(0), cell(1), cell(2)];
const running = async (id, items) => { await push(record(id, "UPDATE_WORKSHOP_ITEMS", "RUNNING",
  {target_profile_id: "alpha", cancellable: true, progress_phase: "download", progress_percent: 15,
   progress_detail: {items}})); await wait(20); };
const entry = (id, phase, done = null, total = null) => ({workshop_id: id, phase, done_bytes: done, total_bytes: total});
const update = await pressUpdate(false);
// A sent item waits; a row without an entry is being checked; a local row has no mark.
await running(update, [entry("111", "queued", null, 200 * megabyte)]);
same(cell(0), `${before[0]}Waiting for download`, "queued");
same(cell(1), `${before[1]}Checking…`, "no entry");
same(cell(2), before[2], "local row");
// The bar shows the operation as a whole; the rows show their mod.
check(barText().includes("Updating mods") && barText().includes("Downloading changed mods")
  && barButton("Cancel"), `bar: ${barText()}`);
check(!barText().includes("Waiting for download") && !feedback().includes("%"), "row detail leaked into bar or page");
// Downloading with both byte counts shows percent and size; without them only the label.
await running(update, [entry("111", "downloading", 50 * megabyte, 200 * megabyte)]);
same(cell(0), "Downloading25 % of 200 MB", "downloading with bytes");
check(statusCells()[0].classList.contains("mods-status-downloading"), "downloading tone");
await running(update, [entry("111", "downloading", null, 3 * 1024 * megabyte)]);
same(cell(0), "Downloading", "downloading without a count");
await running(update, [entry("111", "downloading", 5, null)]);
same(cell(0), "Downloading", "downloading without a size");
await running(update, [entry("111", "downloading", 1536 * megabyte, 2048 * megabyte)]);
same(cell(0), "Downloading75 % of 2.0 GB", "gigabytes");
// Verifying keeps the row state; a failed item is named as failed.
await running(update, [entry("111", "verifying"), entry("222", "verifying")]);
same(cell(0), `${before[0]}Verifying download`, "verifying");
await running(update, [entry("111", "failed"), entry("222", "done")]);
same(label(0), "Failed", "failed item");
same(cell(1), before[1], "done item shows the row state until the result is known");
check(!/[A-Z]{2,}_[A-Z_]+|\b[a-z]+_[a-z_]+\b/.test(statusCells().map((node) => node.textContent).join(" ")),
  "a raw phase reached a row");
// An operation of another profile marks nothing.
window.ServerManModsProgress.changed(record("other", "UPDATE_WORKSHOP_ITEMS", "RUNNING",
  {target_profile_id: "bravo", progress_detail: {items: [entry("111", "downloading", 1, 2)]}}));
same(label(0), "Failed", "another profile changed the rows");
// The finished update leaves the outcome of each mod in its row, and the list in the feedback region.
await finish(update, "UPDATE_WORKSHOP_ITEMS", updateResult({download_state: "FAILED", items: [
  item("111", "CONTENT_FAILED", null, "WORKSHOP_CONTENT_FAILED"), item("222", "VERIFIED_CURRENT", "APPLIED_STATE")]}));
same(cell(0), "FailedDownload failed", "failed row after the update");
same(cell(1), `${before[1]}Already current`, "current row after the update");
check(document.querySelector("#mods-feedback .operation-items")?.textContent.includes("Workshop 111: Download failed"),
  "the per-mod list left the feedback region");
// A cancelled update without a result marks the rows that were still at work.
const cancelled = await pressUpdate(false);
check(cell(0) === before[0] || cell(0) === `${before[0]}Checking…`, `next operation kept the old outcome: ${cell(0)}`);
await running(cancelled, [entry("111", "downloading", 1, 2)]);
await finish(cancelled, "UPDATE_WORKSHOP_ITEMS", null, "CANCELLED", {progress_phase: "cancelled"});
same(label(0), "Failed", "cancelled row");
same(cell(1), before[1], "untouched row after a cancellation");
// Everything current: each row says so, and no list is drawn.
await runUpdate(false, currentResult);
same(cell(0), `${before[0]}Already current`, "case E row");
check(!document.querySelector("#mods-feedback .operation-items"), "case E drew the list");
// A successful update with a review: outcome lines, then the reviewed targets are marked during the apply.
await runUpdate(false, updateResult);
same(cell(0), `${before[0]}Updated`, "updated row");
dialogButton("Apply mods and keys").click(); await wait(30);
same(cell(0), `${before[0]}Applying to the server folder…`, "apply target");
same(cell(1), before[1], "row outside the reviewed targets");
await push(record("publish-1", "PUBLISH_MODS_AND_KEYS", "RUNNING", {target_profile_id: "alpha",
  progress_phase: "COPY_FILE", progress_percent: 30})); await wait(20);
same(cell(0), `${before[0]}Applying to the server folder…`, "apply target while running");
check(barText().includes("Copying mod files"), "apply phase in the bar");
await finish("publish-1", "PUBLISH_MODS_AND_KEYS", {profile_id: "alpha", start_state: "NOT_REQUESTED"});
same(cell(0), before[0], "no mark after the apply");
// The restart marks its targets in the same way while the bar names the step.
await serverState("RUNNING_MANAGED");
await runUpdate(true, updateResult);
dialogButton("Stop server, apply and restart").click(); await wait(30);
await push(record("restart-1", "APPLY_MODS_AND_RESTART", "RUNNING", {target_profile_id: "alpha",
  progress_phase: "STOP_SERVER", progress_percent: 8})); await wait(20);
same(cell(0), `${before[0]}Applying to the server folder…`, "restart target");
check(barText().includes("Applying mods and restarting the server")
  && barText().includes("Saving the world and stopping the server"), `restart bar: ${barText()}`);
await finish("restart-1", "APPLY_MODS_AND_RESTART", {profile_id: "alpha", start_state: "STARTED", backup: null});
same(cell(0), before[0], "no mark after the restart");
// Opening the page again forgets the outcome lines.
await runUpdate(false, currentResult);
commitSection("overview"); await wait(20); commitSection("mods"); await wait(60);
same(cell(0), before[0], "outcome kept across a page open");
"""

# QF-023: only a target that the apply copies is marked; a current target and the short review mark nothing
APPLY_TARGETS = MODS_HOST + r"""
await start("mods"); await wait(50);
const cell = (index) => statusCells()[index].textContent;
const answer = (first, second) => () => ok({profile_id: "alpha", publication_fingerprint: "b".repeat(64),
  key_count: 2, missing_key_count: 0, plain_apply_guarded: false,
  targets: [{workshop_id: "111", target_relative: "mods\\alpha", current: first},
    {workshop_id: "222", target_relative: "mods\\beta", current: second}]});
// One target is copied and one is current: the mark is on the copied one only.
previewAnswer = answer(false, true);
await runUpdate(false, updateResult);
dialogButton("Apply mods and keys").click(); await wait(30);
check(cell(0).endsWith("Applying to the server folder…"), `copied target: ${cell(0)}`);
check(!cell(1).includes("Applying to the server folder"), `current target is marked: ${cell(1)}`);
await push(record("publish-1", "PUBLISH_MODS_AND_KEYS", "RUNNING", {target_profile_id: "alpha",
  progress_phase: "COPY_FILE", progress_percent: 30})); await wait(20);
check(cell(0).endsWith("Applying to the server folder…") && !cell(1).includes("Applying"), "marks while running");
await finish("publish-1", "PUBLISH_MODS_AND_KEYS", {profile_id: "alpha", start_state: "NOT_REQUESTED"});
// A preview without the flag counts the target as copied.
previewAnswer = () => ok({profile_id: "alpha", publication_fingerprint: "b".repeat(64), key_count: 2,
  missing_key_count: 0, plain_apply_guarded: false, targets: [{workshop_id: "222", target_relative: "mods\\beta"}]});
await runUpdate(false, updateResult);
dialogButton("Apply mods and keys").click(); await wait(30);
check(cell(1).endsWith("Applying to the server folder…") && !cell(0).includes("Applying"), "target without the flag");
await finish("publish-2", "PUBLISH_MODS_AND_KEYS", {profile_id: "alpha", start_state: "NOT_REQUESTED"});
// The short review says that no mod folder is copied: no row is marked while that apply runs.
previewAnswer = answer(true, true);
await runUpdate(true, currentResult);
check(dialog().textContent.includes("No mod folder is copied."), "short review");
dialogButton("Apply mods and keys").click(); await wait(30);
await push(record("publish-3", "PUBLISH_MODS_AND_KEYS", "RUNNING", {target_profile_id: "alpha",
  progress_phase: "VERIFY_BEFORE_START", progress_percent: 90})); await wait(20);
check(!statusCells().some((node) => node.textContent.includes("Applying to the server folder")),
  `a current target is marked: ${cell(0)} | ${cell(1)}`);
await finish("publish-3", "PUBLISH_MODS_AND_KEYS", {profile_id: "alpha", start_state: "STARTED"});
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ModsRowProgressDynamicTests(unittest.TestCase):
    """Criterion 7 in the rows: progress and result of each mod, beside the operation bar."""

    def test_rows_follow_item_phases_outcomes_and_apply_targets(self) -> None:
        """Each row of the design table, the bar beside it, and no mark for another profile."""
        self.assertEqual(run_shell_harness(ROWS, budget=20000), "PASS")

    def test_only_targets_that_are_copied_are_marked_during_an_apply(self) -> None:
        """A current target, and every target of the short review, keeps its row without a mark."""
        self.assertEqual(run_shell_harness(APPLY_TARGETS, budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
