"""Headless Edge checks of the operator wording: catalogue lookups, Settings diagnostics, and Mods lines."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# Catalogue lookups with their fallbacks, error wording, and result composition
CATALOGUE = r"""
const labels = window.ServerManOperationLabels;
const messages = window.ServerManOperationMessages;
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
// Kinds, phases, and states: known values and the fallback for a value that the backend adds later.
same(labels.kind("VERIFY_WORKSHOP_FILES").name, "Verifying mod files", "kind name");
same(JSON.stringify(labels.kind("BRAND_NEW_KIND")), JSON.stringify({name: "Working",
  success: "Operation finished.", failure: "The operation did not finish.", cancelled: "Operation cancelled."}),
  "kind fallback");
same(JSON.stringify(labels.phase("UPDATE_WORKSHOP_ITEMS", "verify_items")),
  '{"text":"Verifying downloaded mods","determinate":true}', "phase");
same(JSON.stringify(labels.phase("UPDATE_WORKSHOP_ITEMS", "check_remote")),
  '{"text":"Checking Steam for changes","determinate":false}', "indeterminate phase");
same(labels.phase("UPDATE_WORKSHOP_ITEMS", "download").text, "Downloading changed mods", "download phase");
same(labels.phase("RESTART_SERVER", "BACKUP_HASH").text, "Backup: writing the file list", "lifecycle backup phase");
same(labels.phase("CREATE_BACKUP", "BACKUP_HASH").text, "Working", "backup prefix outside a lifecycle kind");
same(labels.phase("SAVE_PROFILE", "queued").text, "Waiting to start", "generic phase");
same(JSON.stringify(labels.phase("SAVE_PROFILE", "later_phase")), '{"text":"Working","determinate":false}',
  "phase fallback");
same(labels.phase("constructor", "toString").text, "Working", "prototype names are not catalogue keys");
same(labels.state("RECOVERY_REQUIRED"), "Needs recovery", "state");
same(labels.state("PAUSED"), "Status unknown", "state fallback");
// Error codes: wrap, keep, keep with replacement, request sentences, state names, and the leak filter.
same(messages.error({code: "INTERNAL_FAILURE", message: "The operation could not be completed."}),
  "Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics.", "internal");
same(messages.error({code: "REVISION_CONFLICT", message: "expected revision 3, current revision is 4"}),
  "The profile or the settings changed in the meantime. Open another page, return to this one, and try again.", "wrap");
same(messages.error({code: "STORAGE_FAILURE", message: "Settings could not be stored."}),
  "Settings could not be stored.", "keep");
same(messages.error({code: "PATH_INVALID", message: "The configured DayZ root is unsafe."}),
  "The configured DayZ server folder is unsafe.", "keep with replacement");
same(messages.error({code: "INVALID_REQUEST", message: "steam_account_name must contain only letters, digits, or underscore"}),
  "The Steam account name may contain only letters, digits and underscores.", "known request sentence");
same(messages.error({code: "INVALID_REQUEST", message: "lifecycle parameters are invalid"}),
  "Lifecycle parameters are invalid.", "host sentence of a request");
same(messages.error({code: "EXTERNAL_PROCESS", message: "Cannot start while server state is RUNNING_EXTERNAL."}),
  "This cannot be done while the server is running outside DayZ-ServerMan.", "state name");
same(messages.error({code: "CONTROL_CONFLICT", message: "Restore requires STOPPED; current state is RUNNING_MANAGED"}),
  "This cannot be done while the server is running.", "last state name wins");
same(messages.error({code: "CONTROL_CONFLICT", message: "Profile restore requires STOPPED; current state is RUNNING_MANAGED."}),
  "This cannot be done while the server is running.", "profile restore while running (criterion 31)");
same(messages.error({code: "NEW_CODE", message: "A plain sentence from the host."}),
  "A plain sentence from the host.", "plain message of an unknown code");
same(messages.error({code: "NEW_CODE", message: "phase verify_items failed with WORKSHOP_STATE_BAD"}),
  "Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics.", "leak filter");
same(messages.error({code: "STORAGE_FAILURE", message: "write failed for applied_mod_state"}),
  "Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics.", "kept code with a leak");
same(messages.bridgeError(null, "Fallback sentence."), "Fallback sentence.", "missing error");
// Result composition.
const result = (kind, state, extra = {}) => messages.result(record("r", kind, state, extra));
same(result("STOP_SERVER", "SUCCEEDED", {result: {backup: {backup_id: "b"}}}).text,
  "Server stopped and backed up.", "stop with backup");
same(result("STOP_SERVER", "FAILED", {last_working_phase: "BACKUP_STAGE",
  terminal_error: {code: "STORAGE_FAILURE", message: "Backup storage failed."}}).text,
  "Server stopped, but the backup failed. Backup storage failed.", "stop failed in the backup");
same(result("RESTART_SERVER", "FAILED", {last_working_phase: "START_SERVER",
  terminal_error: {code: "LAUNCH_FAILED", message: "The DayZ server could not be started."}}).text,
  "Server stopped, but it could not be started again.", "restart failed at the start");
same(messages.result(record("r", "RESTART_SERVER", "FAILED", {terminal_error: {code: "STOP_TIMEOUT", message: "x"}}),
  "BACKUP_VERIFY").sentence, "Server stopped, but the backup failed. The server was not started again.",
  "phase seen by the bar when the record has none");
same(result("START_SERVER", "FAILED", {terminal_error: {code: "LAUNCH_FAILED",
  message: "The DayZ server could not be started."}}).text, "The server could not be started.", "launch failure once");
same(result("STOP_SERVER", "CANCELLED", {last_working_phase: "BACKUP_HASH"}).text,
  "Backup cancelled. The server stays stopped.", "stop cancelled in the backup");
same(result("STOP_SERVER", "CANCELLED").text, "Operation cancelled.", "stop cancelled while it waited");
same(result("RESTORE_BACKUP", "RECOVERY_REQUIRED", {terminal_error: {code: "RECOVERY_REQUIRED", message: "x"}}).look,
  "recovery", "recovery look");
for (const [state, text, look] of [["VERIFIED", "Mods are downloaded and checked.", "success"],
  ["EMPTY", "This profile has no Workshop mods.", "success"], ["FAILED", "Some mods could not be updated.", "failed"],
  ["UNKNOWN", "The mod update could not be confirmed.", "failed"], ["CANCELLED", "Mod update cancelled.", "cancelled"]]) {
  const value = result("UPDATE_WORKSHOP_ITEMS", "SUCCEEDED", {result: {download_state: state}});
  check(value.text === text && value.look === look, `download state ${state}: ${value.text} ${value.look}`);
}
same(result("PUBLISH_MODS_AND_KEYS", "SUCCEEDED", {result: {start_state: "FAILED", start_error: "PUBLICATION_REQUIRED"}}).text,
  "Mods and keys applied. The server could not be started: Review and apply the mods before the server starts.",
  "apply with a failed start");
same(result("PUBLISH_MODS_AND_KEYS", "SUCCEEDED", {result: {start_state: "NOT_REQUESTED"}}).text,
  "Mods and keys applied. Server start was not requested.", "apply without a start");
// Apply mods and restart: phases of the other kinds, and what each end leaves behind.
const restart = "APPLY_MODS_AND_RESTART";
same(labels.phase(restart, "preflight").text, "Checking the reviewed plan", "restart plan check");
same(labels.phase(restart, "STOP_SERVER").text, "Saving the world and stopping the server", "restart stop");
same(labels.phase(restart, "BACKUP_HASH").text, "Backup: writing the file list", "restart backup");
same(labels.phase(restart, "COPY_FILE").text, "Copying mod files", "restart apply");
same(JSON.stringify(labels.phase(restart, "VERIFY_BEFORE_START")),
  '{"text":"Checking the server folder before the start","determinate":false}', "restart check");
same(labels.phase(restart, "START_SERVER").text, "Starting the server", "restart start");
same(labels.phase("PUBLISH_MODS_AND_KEYS", "STOP_SERVER").text, "Working", "an apply borrows no phase");
same(labels.cancelling(restart), "Cancelling. The restart stops at the next safe moment.", "restart cancelling");
same(result(restart, "CANCELLED").text, "Cancelled. The server keeps running; nothing was applied.", "cancelled waiting");
same(result(restart, "CANCELLED", {last_working_phase: "BACKUP_STAGE"}).text,
  "Cancelled. The server stays stopped; the mods were not applied.", "cancelled after the stop");
same(result(restart, "FAILED", {last_working_phase: "preflight",
  terminal_error: {code: "PUBLICATION_PREVIEW_STALE", message: "Publication preview changed."}}).text,
  "The reviewed plan is out of date, or the server state changed. The server keeps running; nothing was applied. Update again.",
  "refused plan");
same(result(restart, "FAILED", {last_working_phase: "STOP_SERVER", terminal_error: {code: "STOP_TIMEOUT", message: "x"}}).sentence,
  "The server could not be stopped. Nothing was applied.", "stop failed");
same(result(restart, "FAILED", {last_working_phase: "BACKUP_DISCOVER"}).sentence,
  "Server stopped, but the backup failed. The mods were not applied and the server was not started again.", "backup failed");
same(result(restart, "FAILED", {last_working_phase: "STOP_SERVER", terminal_error: {code: "CONTROL_CONFLICT",
  message: "Cannot stop while server state is STOPPED."}}).text,
  "The server was already stopped, so it was not restarted. Nothing was applied. To apply the mods and start the server, "
  + "use Update & start.", "restart of a stopped server");
same(result(restart, "FAILED", {last_working_phase: "STOP_SERVER", terminal_error: {code: "EXTERNAL_PROCESS",
  message: "Cannot stop while server state is RUNNING_EXTERNAL."}}).text, "The server could not be stopped. Nothing was applied. "
  + "This cannot be done while the server is running outside DayZ-ServerMan.", "stop refused in another state");
for (const [code, state, words] of [["CONTROL_CONFLICT", "STARTING", "starting"],
  ["EXTERNAL_PROCESS", "RUNNING_EXTERNAL", "running outside DayZ-ServerMan"]]) {
  same(result(restart, "FAILED", {last_working_phase: "STAGE_TARGET", terminal_error: {code,
    message: `Applying mods requires STOPPED; current state is ${state}.`}}).text,
    "The server was stopped, but its state changed before the mods could be applied. Nothing was applied; "
    + `the server folder is as it was. This cannot be done while the server is ${words}.`, `apply refused by the guard: ${state}`);
}
same(result(restart, "FAILED", {last_working_phase: "STAGE_TARGET", terminal_error: {code: "CONTROL_CONFLICT",
  message: "Another manager controls this DayZ installation."}}).sentence,
  "Server stopped, but the mods could not be applied. The server folder is as it was. The server was not started again.",
  "apply refused by a busy installation");
const failedStart = {last_working_phase: "START_SERVER", terminal_error: {code: "INTERNAL_FAILURE",
  message: "Publication processing failed."}};
const startText = "Mods applied, but the server start did not succeed or could not be confirmed. Check the server state on Overview. "
  + "Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics.";
same(result(restart, "FAILED", failedStart).text, startText, "restart: start handoff failed");
same(result("PUBLISH_MODS_AND_KEYS", "FAILED", failedStart).text, startText, "apply: start handoff failed");
same(result(restart, "FAILED", {...failedStart, terminal_error: {code: "STORAGE_FAILURE",
  message: "Launch evidence could not be recorded safely."}}).message, "Launch evidence could not be recorded safely.",
  "start handoff failed with a host sentence");
same(result(restart, "RECOVERY_REQUIRED", {last_working_phase: "AFTER_LIVE_TARGET"}).sentence,
  "Server stopped, but the mods could not be applied. Changes are blocked.", "apply not proven");
const failedCheck = {last_working_phase: "VERIFY_BEFORE_START", terminal_error: {code: "PUBLICATION_FAILED",
  message: "Published mods or keys changed before start."}};
same(result(restart, "FAILED", failedCheck).text,
  "Mods applied, but the server folder changed before the start. The server stays stopped.", "check failed");
same(result("PUBLISH_MODS_AND_KEYS", "FAILED", failedCheck).text,
  "Mods applied, but the server folder changed before the start. The server was not started.", "apply: check failed");
same(result("PUBLISH_MODS_AND_KEYS", "FAILED", {...failedCheck, last_working_phase: "COPY_FILE"}).text,
  "The mods could not be applied. The mods could not be copied to the server folder.", "apply: copy failed");
same(result(restart, "FAILED").sentence, "Applying mods and restarting the server did not finish.", "no phase known");
for (const [value, text, look] of [
  [{start_state: "STARTED", backup: null}, "Mods applied and server restarted.", "success"],
  [{start_state: "STARTED", backup: {backup_id: "b"}}, "Server backed up, mods applied and server restarted.", "success"],
  [{start_state: "CANCELLED"}, "Mods and keys applied. The server start was cancelled. The server stays stopped.", "success"],
  [{start_state: "FAILED", start_error: "CONTROL_CONFLICT"}, "Server stopped and mods applied, but the server could not be started: "
    + "Another DayZ-ServerMan is using this DayZ installation. Close it and try again.", "failed"],
  [{start_state: "NOT_NEEDED"}, "All mods are current. The server was not restarted. To restart it anyway, use Save & Restart on Overview.", "success"]]) {
  const shown = result(restart, "SUCCEEDED", {result: value});
  check(shown.text === text && shown.look === look, `restart result ${value.start_state}: ${shown.text} ${shown.look}`);
}
// Diagnostics, outcomes, and small enumerations.
const diagnostics = window.ServerManDiagnosticLabels;
same(diagnostics.modOutcome({outcome: "UPDATED_VERIFIED", error_code: null}), "Updated", "outcome");
same(diagnostics.modOutcome({outcome: "CONNECTION_FAILED", error_code: "CONNECTION_FAILED"}),
  "Steam could not be reached", "outcome without its code");
same(diagnostics.modOutcome({outcome: "UNKNOWN_FAILED", error_code: "CACHE_MANIFEST_ID_MISSING"}),
  "Could not verify (Download record is incomplete)", "outcome with a useful detail");
same(diagnostics.modOutcome({outcome: "LATER_OUTCOME", error_code: "LATER_CODE"}), "Failed", "outcome fallback");
same(diagnostics.process("PROCESS_AMBIGUOUS"), "More than one matching DayZ process is running.", "process code");
same(diagnostics.process("LATER_CODE"), "The server state could not be confirmed.", "process fallback");
"""

# Settings diagnostic lines, Overview notices, and the shutdown texts in the page
SETTINGS_AND_OVERVIEW = r"""
const diagnostic = (role, status) => ({role, status, configured_path: "X:\\path",
  message: `${role.replaceAll("_", " ")} is ${status.toLowerCase()}`, action: "No action is required"});
let diagnostics = [diagnostic("dayz_root", "READY"), diagnostic("steamcmd_root", "MOVED"),
  diagnostic("backup_root", "NOT_WRITABLE")];
let block = null;
window.pywebview.api.get_application_snapshot = async () => { const value = snapshot([]);
  value.diagnostics = diagnostics; value.mutation_block = block;
  value.settings.steamcmd_root = "D:\\SteamCMD"; value.settings.custom_backup_root = "E:\\Backups"; return ok(value); };
await start("settings"); await wait(40);
const lines = () => [...document.querySelectorAll(".settings-panel .status-label")].map((node) => node.textContent);
check(JSON.stringify(lines()) === JSON.stringify([
  "Ready: The DayZ server folder is ready.",
  "Moved or missing: The SteamCMD folder is no longer where it was. Reconnect the drive or choose the new location.",
  "Read-only: DayZ-ServerMan cannot write to the backup folder. Choose another folder or change its permissions.",
]), `diagnostic lines: ${JSON.stringify(lines())}`);
const labels = [...document.querySelectorAll(".settings-panel .status-label")];
check(labels[0].classList.contains("status-normal") && labels[1].classList.contains("status-warning"),
  "status colour of the diagnostic lines");
const page = () => document.getElementById("content-region").textContent;
check(!/[A-Z]{2,}_[A-Z_]+/.test(page()) && !page().includes("READY") && !page().includes("No action is required")
  && !page().includes("dayz root"), "raw diagnostic text in Settings");
// No diagnostic and a status that the catalogue does not know.
diagnostics = [diagnostic("steamcmd_root", "LATER_STATUS")];
commitSection("overview"); await wait(20); commitSection("settings"); await wait(40);
check(JSON.stringify(lines()) === JSON.stringify(["Not checked: Choose a location to check it.",
  "Needs attention: Check the SteamCMD folder.", "Not checked: Choose a location to check it."]),
  `fallback lines: ${JSON.stringify(lines())}`);
// Overview: the process diagnostic and the recovery notice use operator wording.
host.status = {state: "UNKNOWN", process_id: null, diagnostic_code: "INVENTORY_UNAVAILABLE", readiness: null,
  query_port: null};
block = "Mutations are blocked by unresolved mod publication recovery.";
commitSection("overview"); await wait(40);
check(page().includes("The running programs could not be read.") && !page().includes("INVENTORY_UNAVAILABLE"),
  "process diagnostic wording");
check(page().includes("Applying mods to the server folder was interrupted and could not be undone safely.")
  && page().includes("Changes are blocked until this is resolved. Details are in Logs, Manager diagnostics.")
  && !page().includes("Mutations are blocked"), "recovery notice wording");
// Host error panel and shutdown panel.
window.ServerManUi.renderHostError(fail("CONTROL_CONFLICT", "The installation control mutex could not be acquired."));
check(page().includes("Another DayZ-ServerMan is using this DayZ installation. Close it and try again.")
  && !page().includes("mutex"), "host error wording");
window.ServerManUi.renderShutdown({state: "DRAINING", blocking_reason: null});
check(page().includes("Waiting work is cancelled. Work in progress finishes or stops at a safe moment."),
  "shutdown wording");
window.ServerManUi.renderShutdown({state: "CLOSED", blocking_reason: null});
check(page().includes("Everything is saved. The window can close."), "closed wording");
"""

# Mods: the progress line, the cancelling line, the result lines, and the per-mod list
MODS = r"""
Object.assign(window.pywebview.api, {
  get_update_status: async () => ok({mods: {check_state: "OK", checked_at: null,
    last_success_at: "2026-10-03T13:41:07.120Z", error_code: null, update_count: 0, pending_apply_count: 0},
    server_build: null, checking: false, revision: 1}),
  request_update_check: async () => ok({accepted: false, checking: false}),
  list_mod_inventory: async () => ok([{order: 1, name: "Example Mod", directory: "@Example",
    launch_scope: "client", source_kind: "workshop", workshop_id: "111", version: "1.0", state: "CURRENT",
    time_updated: 1, remote_time_updated: 1, remote_check: "OK", pending_reason: null}]),
  update_workshop_items: async () => ok({operation_id: "update", state: "QUEUED"}),
});
host.operations.set("update", record("update", "UPDATE_WORKSHOP_ITEMS", "QUEUED", {cancellable: true}));
await start("mods"); await wait(50);
const feedback = () => document.getElementById("mods-feedback").textContent;
const raw = /[A-Z]{2,}_[A-Z_]+|\b[a-z]+_[a-z_]+\b/;
document.getElementById("update-workshop").click(); await wait(20);
check(feedback() === "Downloading or updating mods", `start line: ${feedback()}`);
// Progress: the page prints no phase and no percent; the bar words the phase.
await push(record("update", "UPDATE_WORKSHOP_ITEMS", "RUNNING", {cancellable: true,
  progress_phase: "check_remote", progress_percent: 12}));
check(feedback() === "Downloading or updating mods" && !feedback().includes("%"), `progress line: ${feedback()}`);
check(barText().includes("Checking Steam for changes") && !raw.test(barText()),
  `bar phase: ${barText()}`);
await push(record("update", "UPDATE_WORKSHOP_ITEMS", "RUNNING", {cancellable: true,
  progress_phase: "verify_items", progress_percent: 70}));
check(barText().includes("Verifying downloaded mods") && barText().includes("70%") && !feedback().includes("70"),
  "determinate phase in the bar only");
await push(record("update", "UPDATE_WORKSHOP_ITEMS", "CANCELLING", {cancellable: true,
  progress_phase: "verify_items", progress_percent: 70}));
check(feedback() === "Cancelling. The update stops at the next safe moment.", `cancelling line: ${feedback()}`);
// Result: failed download state with the per-mod list, without a state name or an error code.
await push(record("update", "UPDATE_WORKSHOP_ITEMS", "SUCCEEDED", {progress_phase: "complete",
  result: {profile_id: "alpha", download_state: "FAILED", items: [
    {item: {workshop_id: "111"}, outcome: "UPDATED_VERIFIED", error_code: null},
    {item: {workshop_id: "222"}, outcome: "CONNECTION_FAILED", error_code: "CONNECTION_FAILED"},
    {item: {workshop_id: "333"}, outcome: "UNKNOWN_FAILED", error_code: "CACHE_VERIFICATION_FAILED"}]}}));
check(feedback().startsWith("Some mods could not be updated."), `result line: ${feedback()}`);
const items = [...document.querySelectorAll("#mods-feedback .operation-items li")].map((node) => node.textContent);
check(JSON.stringify(items) === JSON.stringify(["Workshop 111: Updated", "Workshop 222: Steam could not be reached",
  "Workshop 333: Could not verify (Downloaded files could not be verified)"]), `per-mod list: ${JSON.stringify(items)}`);
check(!raw.test(feedback()) && !raw.test(barText()), `raw identifier: ${feedback()} | ${barText()}`);
check(document.getElementById("operation-alert").textContent === ""
  && document.getElementById("operation-announcer").textContent !== "Some mods could not be updated.",
  "the bar announced a result that the page showed");
// A failed operation of the page uses the failure sentence and the operator wording of the code.
host.operations.set("again", record("again", "UPDATE_WORKSHOP_ITEMS", "QUEUED", {cancellable: true}));
window.pywebview.api.update_workshop_items = async () => ok({operation_id: "again", state: "QUEUED"});
document.getElementById("update-workshop").click(); await wait(20);
await push(record("again", "UPDATE_WORKSHOP_ITEMS", "FAILED", {progress_phase: "failed",
  terminal_error: {code: "STEAMCMD_PATH_CHANGED",
    message: "SteamCMD or Workshop path identity changed. Reload settings and retry."}}));
check(feedback() === "The mods could not be updated. The SteamCMD or Workshop folder is missing or changed. "
  + "Check the folders in Settings and try again.", `failure line: ${feedback()}`);
check(document.querySelector("#mods-feedback [role=alert]"), "the failure is not an alert");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OperatorWordingDynamicTests(unittest.TestCase):
    """Criterion 15 in the running page: catalogue text, never a raw identifier."""

    def test_catalogue_lookups_fallbacks_and_result_sentences(self) -> None:
        """Kinds, phases, states, error codes, results, outcomes, and diagnostics with their fallbacks."""
        self.assertEqual(run_shell_harness(CATALOGUE), "PASS")

    def test_settings_diagnostics_overview_notices_and_panels(self) -> None:
        """Settings lines use label and sentence; Overview and the two panels use operator wording."""
        self.assertEqual(run_shell_harness(SETTINGS_AND_OVERVIEW), "PASS")

    def test_mods_progress_cancelling_and_result_lines(self) -> None:
        """The Mods line shows no phase, percent, state, or code; the bar and the catalogue word them."""
        self.assertEqual(run_shell_harness(MODS, budget=6000), "PASS")


if __name__ == "__main__":
    unittest.main()
