"""Headless Edge checks of the Mods update actions: buttons per server state, result rule, apply review."""
from __future__ import annotations

import unittest

try:
    from tests.mods_update_harness import MODS_HOST
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from mods_update_harness import MODS_HOST
    from ui_harness_support import EDGE, run_shell_harness

# Design 13.1: header order, label and lock per server state, and the start flag per button
BUTTONS = MODS_HOST + r"""
await start("mods"); await wait(50);
same([...document.querySelectorAll(".mods-header-actions button")].map((button) => button.textContent).join("|"),
  "Check now|Verify files|Update & start|Update all", "header order");
check(byId("update-workshop").classList.contains("button-primary")
  && !byId("update-start").classList.contains("button-primary"), "one primary action");
check(!byId("content-region").textContent.includes("Download / update"), "the old label is gone");
check(!document.querySelector(".mods-panel #update-workshop"), "the update action left the Steam access panel");
const startButton = () => byId("update-start");
for (const [state, label, enabled, reason] of [
  ["STOPPED", "Update & start", true, ""], ["RUNNING_MANAGED", "Update & restart", true, ""],
  ["STARTING", "Update & restart", false, "The server is starting."],
  ["STOPPING", "Update & start", false, "The server is stopping."],
  ["RUNNING_EXTERNAL", "Update & restart", false,
    "The server runs outside DayZ-ServerMan. Stop it there, then use Update & start."],
  ["UNKNOWN", "Update & start", false, "The server state could not be confirmed. See Overview."],
  ["AMBIGUOUS", "Update & start", false, "The server state could not be confirmed. See Overview."]]) {
  await serverState(state);
  check(startButton().textContent === label && startButton().disabled === !enabled
    && startButton().title === reason, `${state}: ${startButton().textContent} ${startButton().disabled} ${startButton().title}`);
  check(!byId("update-workshop").disabled, `${state}: Update all is offered in every state`);
}
// A failed read of the server state offers no start.
window.pywebview.api.get_server_status = async () => fail("PROCESS_STATE_UNKNOWN", "x");
await serverState("STOPPED");
check(startButton().disabled && startButton().textContent === "Update & start", "failed read");
window.pywebview.api.get_server_status = async () => ok(host.status);
await serverState("STOPPED");
// Each button sends its own start flag, as a real boolean.
const first = await pressUpdate(false);
check(calls.updates.at(-1) === false, "Update all sends no start");
check(startButton().disabled && byId("update-workshop").disabled, "own operation locks both buttons");
same(feedback(), "Downloading or updating mods", "start line");
await finish(first, "UPDATE_WORKSHOP_ITEMS", updateResult({download_state: "FAILED"}));
check(!startButton().disabled, "unlocked after the operation");
const second = await pressUpdate(true);
check(calls.updates.at(-1) === true, "Update & start sends the start flag");
await finish(second, "UPDATE_WORKSHOP_ITEMS", null, "CANCELLED");
// The label follows the state that was read last, also when a state is entered from the other side.
await serverState("STARTING");
same(startButton().textContent, "Update & restart", "starting after stopped");
await serverState("RUNNING_MANAGED"); await serverState("STOPPING");
same(startButton().textContent, "Update & start", "stopping after running");
// While the page's own restart runs, the locked label keeps naming the restart in every server state.
await serverState("RUNNING_MANAGED");
const third = await pressUpdate(true);
await finish(third, "UPDATE_WORKSHOP_ITEMS", updateResult());
dialogButton("Stop server, apply and restart").click(); await wait(30);
for (const state of ["STOPPING", "STOPPED", "STARTING", "RUNNING_MANAGED"]) {
  await serverState(state);
  check(startButton().textContent === "Update & restart" && startButton().disabled,
    `restart in progress, ${state}: ${startButton().textContent} ${startButton().disabled}`);
}
await finish("restart-1", "APPLY_MODS_AND_RESTART", {profile_id: "alpha", start_state: "STARTED", backup: null});
check(startButton().textContent === "Update & restart" && !startButton().disabled, "after the restart");
// The same for an apply with a start: the label names the start until the operation ends.
await serverState("STOPPED");
const fourth = await pressUpdate(true);
await finish(fourth, "UPDATE_WORKSHOP_ITEMS", updateResult());
dialogButton("Apply mods and keys").click(); await wait(30);
for (const state of ["STARTING", "RUNNING_MANAGED"]) {
  await serverState(state);
  check(startButton().textContent === "Update & start" && startButton().disabled, `start in progress, ${state}`);
}
await finish("publish-1", "PUBLISH_MODS_AND_KEYS", {profile_id: "alpha", start_state: "STARTED"});
same(startButton().textContent, "Update & restart", "after the start");
"""

# Design 13.2: what follows an update, per result and per button
RESULTS = MODS_HOST + r"""
await start("mods"); await wait(50);
const failed = () => updateResult({download_state: "FAILED",
  items: [item("111", "CONTENT_FAILED", null, "WORKSHOP_CONTENT_FAILED"), item("222", "VERIFIED_CURRENT", "APPLIED_STATE")]});
// An update without verified downloads: page result and per-mod list, never a review.
await runUpdate(false, failed);
check(feedback().startsWith("Some mods could not be updated.") && feedback().includes("Workshop 111: Download failed")
  && !feedback().includes("The server"), `failed: ${feedback()}`);
await runUpdate(true, failed);
check(feedback().includes("Some mods could not be updated. The server was not started."), `failed start: ${feedback()}`);
await serverState("RUNNING_MANAGED");
await runUpdate(true, failed);
check(feedback().includes("Some mods could not be updated. The server keeps running; nothing was applied."),
  `failed restart: ${feedback()}`);
check(calls.previews === 0 && !dialog(), "a failed update opened a review");
// Everything is current: one sentence, no review, no per-mod list.
await serverState("STOPPED");
await runUpdate(false, currentResult);
same(feedback(), "All mods are current. Nothing to download or apply.", "case E, Update all");
check(!dialog() && !document.querySelector("#mods-feedback .operation-items"), "case E drew a review or a list");
// A missing key file keeps the review, so the apply still repairs it.
previewAnswer = () => ok({profile_id: "alpha", publication_fingerprint: "b".repeat(64), key_count: 2,
  missing_key_count: 1, plain_apply_guarded: false,
  targets: [{workshop_id: "111", target_relative: "mods\\alpha", current: true}]});
await runUpdate(false, currentResult);
check(dialog() && dialog().textContent.includes("mods\\alpha"), "a missing key did not open the review");
dialogButton("Cancel").click();
previewAnswer = () => ok({profile_id: "alpha", publication_fingerprint: "b".repeat(64), key_count: 2,
  missing_key_count: 0, plain_apply_guarded: false,
  targets: [{workshop_id: "111", target_relative: "mods\\alpha", current: true}]});
// Update & start keeps the review in its short form, because it confirms the start.
await runUpdate(true, currentResult);
check(dialog().textContent.includes("No mod folder is copied. Missing key files are added.")
  && dialog().textContent.includes("will start this server") && !dialog().textContent.includes("mods\\alpha"),
  `case E start: ${dialog().textContent}`);
dialogButton("Cancel").click();
// Update & restart with nothing to apply neither reviews nor stops.
await serverState("RUNNING_MANAGED");
await runUpdate(true, currentResult);
same(feedback(), "All mods are current. The server was not restarted. To restart it anyway, use Save & Restart on Overview.",
  "case E restart");
check(!dialog() && calls.restarted.length === 0, "case E restart submitted something");
// A profile without Workshop mods.
const empty = () => updateResult({download_state: "EMPTY", items: [], process_id: null});
await runUpdate(true, empty);
same(feedback(), "This profile has no Workshop mods. The server was not restarted.", "case N restart");
await serverState("STOPPED");
await runUpdate(false, empty);
same(feedback(), "This profile has no Workshop mods.", "case N, Update all");
await runUpdate(true, empty);
check(dialog()?.textContent.includes("No mod folder is copied."), "case N start has no short review");
dialogButton("Cancel").click();
// Any other verified result opens the full review; a failed preview opens none.
await runUpdate(false, updateResult);
check(dialog().textContent.includes("Workshop 111: mods\\alpha") && dialog().textContent.includes("2 verified key file(s)"),
  "case A review");
check(document.querySelector("#mods-feedback .operation-items")?.textContent.includes("Workshop 111: Updated"),
  "case A keeps the per-mod list");
dialogButton("Cancel").click();
previewAnswer = () => fail("PUBLICATION_PREVIEW_STALE", "Publication preview changed.");
await runUpdate(false, currentResult);
check(!dialog() && feedback().includes("changed after the review"), `failed preview: ${feedback()}`);
"""

# Design 13.3: review sentence and confirm routing per start flag, server state and policy
REVIEW = MODS_HOST + r"""
await start("mods"); await wait(50);
const targets = (current) => [{workshop_id: "111", target_relative: "mods\\alpha", current}];
const answer = (guarded, current, missing = 0) => () => ok({profile_id: "alpha",
  publication_fingerprint: "b".repeat(64), key_count: 2, missing_key_count: missing,
  plain_apply_guarded: guarded, targets: targets(current)});
const sentence = () => dialog().querySelector(".mod-publication-variant").textContent;
const review = async (start, state) => { await serverState(state === "RUNNING_MANAGED" || state === "STOPPED" ? state : "STOPPED");
  const id = await pressUpdate(start); await serverState(state);
  await finish(id, "UPDATE_WORKSHOP_ITEMS", updateResult()); };
const confirmAndEnd = async (label, kind, id, result) => { dialogButton(label).click(); await wait(30);
  await finish(id, kind, result); };
// Plain apply, server stopped.
await review(false, "STOPPED");
same(sentence(), "No server start was requested.", "plain");
await confirmAndEnd("Apply mods and keys", "PUBLISH_MODS_AND_KEYS", "publish-1", {profile_id: "alpha", start_state: "NOT_REQUESTED"});
check(calls.published.length === 1 && calls.published[0].length === 6, "plain apply submitted");
same(feedback(), "Mods and keys applied. Server start was not requested.", "plain result");
// Plain apply while the server runs, policy "attempt" (not the host default): the warning, and the apply is still offered.
previewAnswer = answer(false, false);
await review(false, "RUNNING_MANAGED");
check(sentence().startsWith("The server is running. A mod folder that is in use cannot be replaced")
  && sentence().endsWith("Use Update & restart instead."), `attempt: ${sentence()}`);
check(dialogButton("Apply mods and keys"), "attempt has no confirm");
dialogButton("Cancel").click();
same(feedback(), "Apply review cancelled. Downloaded content was not copied to the server.", "cancel text");
// QF-022: the first sentence is true for every state that is not stopped, also for a failed read.
const inUse = " A mod folder that is in use cannot be replaced; the apply then stops and puts the folders back.";
const whenStopped = " To be safe, apply the mods when the server is stopped.";
for (const [state, text] of [["RUNNING_MANAGED", `The server is running.${inUse} Use Update & restart instead.`],
  ["RUNNING_EXTERNAL", `The server is running outside DayZ-ServerMan.${inUse}${whenStopped}`],
  ["STARTING", `The server is starting.${inUse}${whenStopped}`],
  ["STOPPING", `The server is stopping.${inUse}${whenStopped}`],
  ["UNKNOWN", `The server state could not be confirmed.${inUse}${whenStopped}`],
  ["AMBIGUOUS", `The server state could not be confirmed.${inUse}${whenStopped}`]]) {
  await review(false, state);
  same(sentence(), text, `plain apply, ${state}`);
  check(dialogButton("Apply mods and keys"), `${state}: the attempt is not offered`);
  dialogButton("Cancel").click();
}
window.pywebview.api.get_server_status = async () => fail("PROCESS_STATE_UNKNOWN", "x");
await review(false, "STOPPED");
same(sentence(), `The server state could not be confirmed.${inUse}${whenStopped}`, "plain apply, failed read");
dialogButton("Cancel").click();
window.pywebview.api.get_server_status = async () => ok(host.status);
// Policy "refuse": a plan that writes offers only Close; a plan that writes nothing is a plain apply.
previewAnswer = answer(true, false);
await review(false, "RUNNING_MANAGED");
same(sentence(), "The server is running. Mods cannot be applied to the server folder now. "
  + "Use Update & restart, or stop the server first.", "refused");
same([...dialog().querySelectorAll("button")].map((button) => button.textContent).join("|"), "Close", "refused buttons");
dialogButton("Close").click();
previewAnswer = answer(true, true, 1);
await review(false, "RUNNING_MANAGED");
check(sentence().startsWith("The server is running. Mods cannot be applied"), "a missing key counts as writing");
dialogButton("Close").click();
previewAnswer = answer(true, true);
await review(false, "RUNNING_MANAGED");
check(sentence() === "No server start was requested." && dialogButton("Apply mods and keys"), "no-write plain apply");
dialogButton("Cancel").click();
previewAnswer = answer(true, false);
// Apply, then start.
await review(true, "STOPPED");
check(sentence().includes("will start this server"), "start sentence");
await confirmAndEnd("Apply mods and keys", "PUBLISH_MODS_AND_KEYS", "publish-2", {profile_id: "alpha", start_state: "STARTED"});
same(feedback(), "Mods and keys applied. The server was started.", "start result");
// A server state that is neither stopped nor managed submits nothing.
await review(true, "STARTING");
same(sentence(), "The server state changed. The mods are downloaded; nothing was applied.", "changed");
check(!dialogButton("Apply mods and keys") && dialogButton("Close"), "changed has a confirm");
dialogButton("Close").click();
// Restart: the review says what happens to the running server; the backup flag is a real boolean.
await review(true, "RUNNING_MANAGED");
same(sentence(), "DayZ-ServerMan will save and stop the server, apply the mods, and start the server again. "
  + "The server is offline during these steps.", "restart");
dialogButton("Cancel").click();
same(feedback(), "Apply review cancelled. Downloaded content was not copied to the server. The server keeps running.",
  "restart cancel text");
await window.ServerManProfileContext.setBackupAfterStop("alpha", true);
await review(true, "RUNNING_MANAGED");
check(sentence().includes("save and stop the server, create a verified backup, apply the mods"), `backup: ${sentence()}`);
dialogButton("Stop server, apply and restart").click(); await wait(30);
check(calls.restarted.length === 1 && calls.restarted[0].length === 7 && calls.restarted[0][6] === true
  && calls.published.length === 2, "restart routing with the backup flag");
same(feedback(), "Stopping the server, applying mods and starting it again", "restart start line");
await finish("restart-1", "APPLY_MODS_AND_RESTART", null, "FAILED", {last_working_phase: "BACKUP_STAGE",
  progress_phase: "failed", terminal_error: {code: "STORAGE_FAILURE", message: "Backup storage failed."}});
check(feedback().startsWith("Server stopped, but the backup failed. The mods were not applied and the server was not started again."),
  `restart failure: ${feedback()}`);
await window.ServerManProfileContext.setBackupAfterStop("alpha", false);
await review(true, "RUNNING_MANAGED");
dialogButton("Stop server, apply and restart").click(); await wait(30);
check(calls.restarted.length === 2 && calls.restarted[1][6] === false, "restart without a backup");
await finish("restart-2", "APPLY_MODS_AND_RESTART", {profile_id: "alpha", start_state: "STARTED", backup: null});
same(feedback(), "Mods applied and server restarted.", "restart result");
// The state is read again at confirm: a change redraws the review and submits nothing.
await review(true, "STOPPED");
host.status = {...host.status, state: "RUNNING_MANAGED"};
dialogButton("Apply mods and keys").click(); await wait(30);
check(calls.published.length === 2 && calls.restarted.length === 2, "a changed state was submitted");
check(dialog() && sentence().startsWith("DayZ-ServerMan will save and stop the server"), "review not redrawn");
dialogButton("Cancel").click();
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ModsUpdateActionsDynamicTests(unittest.TestCase):
    """Criteria 7 and 8 on the Mods page: one update action, its start variant, and the reviewed apply."""

    def test_buttons_follow_the_server_state_and_send_their_start_flag(self) -> None:
        """Label, lock and reason per server state; "Update all" stays the one primary action."""
        self.assertEqual(run_shell_harness(BUTTONS, budget=9000), "PASS")

    def test_result_rule_after_an_update(self) -> None:
        """Each cell of the result table: sentence, review, short review, or the page result."""
        self.assertEqual(run_shell_harness(RESULTS, budget=20000), "PASS")

    def test_review_sentences_and_confirm_routing(self) -> None:
        """Each review row with its submitted method; a state change before confirm submits nothing."""
        self.assertEqual(run_shell_harness(REVIEW, budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
