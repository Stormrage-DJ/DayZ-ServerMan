"""Headless Edge checks of the phase 3 rework: host sentences, conflict causes, block reasons, and bar rules."""
from __future__ import annotations

import json
import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

from dayz_serverman.application import activity_wording as wording


# QF-009: a host sentence is shown; an identifier inside it becomes the on-screen label
HOST_SENTENCES = r"""
const messages = window.ServerManOperationMessages;
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const request = (message) => messages.error({code: "INVALID_REQUEST", message}, "Page fallback.");
const FALLBACK = "The request was not accepted. Check the values you entered, then try again. "
  + "Details are in Logs, Manager diagnostics.";
// The examples of the independent review.
same(request("Workshop mods must not repeat an identifier"), "Workshop mods must not repeat an identifier.", "profile save");
same(request("maxPlayers is above its supported maximum"),
  "“Maximum players” is above its supported maximum.", "configuration key");
same(request("The selected archive is corrupt, unsafe or unsupported. Select a valid full backup ZIP."),
  "The selected archive is corrupt, unsafe or unsupported. Select a valid full backup ZIP.", "archive sentence");
same(request("This older backup lacks complete profile metadata. Create a new full backup first."),
  "This older backup lacks complete profile metadata. Create a new full backup first.", "older backup sentence");
// The validation of the Configuration form names the field by its label.
const input = document.createElement("input"); input.type = "number"; input.value = "1.5";
input.dataset.configurationField = "steamQueryPort"; document.body.append(input);
const values = new Map([["steamQueryPort", {key: "steamQueryPort", kind: "integer", present: true, value: 27016}]]);
const thrown = () => { try { window.ServerManConfigurationEdit.changedValues(values); return ""; }
  catch (error) { return error.message; } };
same(thrown(), "“Steam query port” is not a valid integer.", "form validation");
input.value = ""; same(thrown(), "“Steam query port” requires a numeric value.", "empty numeric field");
input.remove();
// Other keys: tweak keys with and without a path, profile fields, path roles, and enum values.
same(request("LootDamageMin must not exceed LootDamageMax"),
  "“Minimum loot damage” must not exceed “Maximum loot damage”.", "tweak keys");
same(request("PlayerData.StaminaData.staminaMax must be a number"),
  "“Maximum stamina” must be a number.", "tweak key with a path");
same(request("game_port must be from 1 through 65535"), "“Game port” must be from 1 through 65535.", "profile field");
same(request("dayz_executable must be inside dayz_root"),
  "The DayZ server program must be inside the DayZ server folder.", "path roles");
same(request("ACCOUNT mode requires account_name"),
  "“Steam account” mode requires “Steam account name”.", "enum value");
same(request("server config steamQueryPort exceeds 65535"),
  "Server config “Steam query port” exceeds 65535.", "key inside the sentence");
same(messages.error({code: "STORAGE_FAILURE", message: "rain_time_min could not be written"}),
  "“Minimum interval” could not be written.", "the rule holds for every code with a sentence");
// Fallback: an empty message, a bare code, and an identifier that no catalogue knows.
for (const message of ["", "INVALID", "parameters contain unknown fields: ['x']", "expected_revision is invalid",
  "unknownCamelKey is bad"]) same(request(message), FALLBACK, `fallback for "${message}"`);
check(!FALLBACK.includes("Reload"), "the fallback names a reload control");
same(window.ServerManHostSentences.fieldLabel("constructor"), null, "prototype names are not field keys");
"""

# QF-007 and QF-008: the cause of a refused submission and the reason of a recovery block
CONFLICTS_AND_BLOCKS = r"""
const messages = window.ServerManOperationMessages;
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const conflict = (message, reason, owner) => messages.error({code: "MUTATION_CONFLICT", message,
  retryable: true, ...(reason ? {details: {reason, ...(owner ? {owner} : {})}} : {})});
const QUEUE = "Too many operations are waiting. Wait for one to finish and try again.";
const CLOSING = "DayZ-ServerMan is closing and starts no new operation.";
const RESTORE = "A backup restore did not finish cleanly. Open Backups; DayZ-ServerMan checks the unfinished "
  + "restore again there.";
same(conflict("operation queue is full", "QUEUE_FULL"), QUEUE, "queue full");
same(conflict("operation lane is draining", "SHUTTING_DOWN"), CLOSING, "closing");
same(conflict("Mutations are blocked by unresolved restore recovery.", "RECOVERY_BLOCK"),
  `Changes are blocked until recovery is resolved. ${RESTORE}`, "recovery block");
// A host that names no cause is read by its message; the cause wins over the message.
same(conflict("operation queue is full"), QUEUE, "queue full by message");
same(conflict("operation lane is draining"), CLOSING, "closing by message");
same(conflict("Mutations are blocked until restore recovery is inspected."),
  `Changes are blocked until recovery is resolved. ${RESTORE}`, "recovery block by message");
same(conflict("anything", "QUEUE_FULL"), QUEUE, "cause wins");
same(conflict("A later cause in a plain sentence.", "LATER_CAUSE"), "A later cause in a plain sentence.", "later cause");
// One sentence per known block reason, the kept host sentence, and the fallback.
const TAIL = " Changes are blocked until this is resolved. Details are in Logs, Manager diagnostics.";
for (const [fragment, sentence] of BLOCK_REASONS) {
  same(messages.recoveryNotice(`Mutations are blocked: ${fragment}.`), sentence + TAIL, `block reason ${fragment}`);
}
// QF-045: a reason without a catalogue sentence names its way out: a restart, or Backups for a restore.
const RESTART = " Restart DayZ-ServerMan to check again.";
same(messages.recoveryNotice("The configured DayZ root is unsafe."),
  "The configured DayZ server folder is unsafe." + RESTART + TAIL, "host sentence kept");
same(messages.recoveryNotice("profile record is unavailable: INTERRUPTED_WRITE"),
  "An earlier operation did not finish cleanly." + RESTART + TAIL, "fallback");
same(messages.recoveryNotice("Committed restore targets could not be proven.", "RESTORE_BACKUP"),
  "Committed restore targets could not be proven. Open Backups to finish the restore." + TAIL, "restore owner");
same(conflict("Committed restore targets could not be proven.", "RECOVERY_BLOCK", "RESTORE_BACKUP"),
  "Changes are blocked until recovery is resolved. Committed restore targets could not be proven. "
  + "Open Backups to finish the restore.", "restore owner in a refusal");
// QF-048: the way out follows the owner, never the words of the reason.
same(window.ServerManHostSentences.blockReason("Committed restore targets could not be proven."),
  "Committed restore targets could not be proven." + RESTART, "restore words without the owner");
same(window.ServerManHostSentences.blockReason("The launched server ownership could not be verified.", "RESTORE_BACKUP"),
  "The launched server ownership could not be verified. Open Backups to finish the restore.", "owner without restore words");
// The Overview notice shows the reason of the snapshot.
let block = "Direct profile restore recovery requires attention.";
let owner = null;
window.pywebview.api.get_application_snapshot = async () => { const value = snapshot([]);
  value.mutation_block = block; value.mutation_block_owner = owner; return ok(value); };
await start();
const notice = () => document.querySelector("#content-region .notice-recovery")?.textContent || "";
// QF-075: changed or unreadable restore data is not resolved by a stop and restart, so none is offered.
same(notice(), "Recovery required" + "A profile restore from a backup archive did not finish, and DayZ-ServerMan "
  + "cannot finish or undo it safely because the DayZ server folder or the restored files changed or cannot be "
  + "opened. If a drive or folder was unavailable, make it available again, then restart DayZ-ServerMan." + TAIL,
  "Overview notice");
block = "journal_state is RECOVERY_REQUIRED";
await loadSnapshot(); window.clearTimeout(shellState.pollTimer); await wait(40);
same(notice(), "Recovery required" + "An earlier operation did not finish cleanly." + RESTART + TAIL,
  "Overview fallback");
block = "Committed restore targets could not be proven."; owner = "RESTORE_BACKUP";
await loadSnapshot(); window.clearTimeout(shellState.pollTimer); await wait(40);
same(notice(), "Recovery required" + block + " Open Backups to finish the restore." + TAIL, "Overview restore owner");
// The texts shared with the Manager activity wording are the same in both catalogues.
for (const [code, text] of Object.entries(ERROR_TEXTS)) same(messages.error({code, message: ""}), text, `code ${code}`);
for (const [kind, texts] of Object.entries(KIND_ERROR_TEXTS)) for (const [code, text] of Object.entries(texts)) {
  same(messages.error({code, message: ""}, "", kind), text, `code ${code} of ${kind}`);
}
// D19: the bar result of an unproven SteamCMD exit speaks of the operation that ran and states the block once.
const unproven = (kind, message, code = "UPDATE_RESULT_UNKNOWN") => messages.result(record("u", kind,
  "RECOVERY_REQUIRED", {progress_phase: "failed", terminal_error: {code, message}})).text;
const once = (text, expected, name) => { same(text, expected, name);
  same(text.split("Changes are blocked").length, 2, `${name} states the block once`); };
once(unproven("AUTHENTICATE_STEAMCMD", "SteamCMD process-tree exit after the sign-in could not be proven."),
  "Steam sign-in did not complete. Changes are blocked. SteamCMD did not close cleanly after the Steam sign-in, "
  + "so the sign-in cannot be confirmed.", "sign-in result");
once(unproven("UPDATE_WORKSHOP_ITEMS", "A prior SteamCMD update ended without a proven result."),
  "The mods could not be updated. Changes are blocked. SteamCMD did not close cleanly, so the update cannot be "
  + "confirmed.", "update result");
once(unproven("RESTORE_BACKUP", "x", "RECOVERY_REQUIRED"), "The backup could not be restored. Changes are blocked. "
  + "An earlier operation did not finish cleanly. Details are in Logs, Manager diagnostics.", "recovery result");
for (const [role, label] of Object.entries(ROLE_LABELS)) same(window.ServerManDiagnosticLabels.role(role), label, role);
for (const [fragment, text] of REQUEST_TEXTS) {
  same(messages.error({code: "INVALID_REQUEST", message: `value ${fragment}`}), text, `request ${fragment}`);
}
for (const [state, text] of Object.entries(SERVER_STATES)) {
  same(messages.error({code: "X", message: `state is ${state}`}), `This cannot be done while the server is ${text}.`, state);
}
for (const [message, text] of SAMPLES) same(window.ServerManHostSentences.sentence(message), text, `sample ${message}`);
"""

# QF-011, QF-013, QF-014, QF-016: verification look, backup card reasons, bar replacement, small texts
BAR_AND_CARDS = r"""
const labels = window.ServerManOperationLabels;
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const rows = () => [...bar().querySelectorAll(".operation-row")].map((row) =>
  `${row.className.replace("operation-row ", "")}|${row.querySelector(".operation-name").textContent}`);
const status = () => document.getElementById("application-status-text").textContent;
const dismiss = async () => { barButton("Dismiss").click(); await wait(10); };
const item = (id, source, target) => ({workshop_id: String(id), source_state: source, target_state: target});
await start();
// A verification that found problems: warning look and the count of mods; a clean one: success.
await push(record("v1", "VERIFY_WORKSHOP_FILES", "SUCCEEDED", {progress_phase: "complete", result: {items: [
  item(1, "VERIFIED", "MATCHES_SOURCE"), item(2, "MISSING", "NOT_APPLIED"), item(3, "VERIFIED", "DIFFERS")]}}));
same(JSON.stringify(rows()), '["operation-result is-warning|2 mods have problems."]', "verification with problems");
check(getComputedStyle(bar().querySelector(".operation-dot")).backgroundColor === "rgb(242, 184, 75)", "warning colour");
check(!status().includes("failed"), `a verification with problems is no failed operation: ${status()}`);
await dismiss();
await push(record("v2", "VERIFY_WORKSHOP_FILES", "SUCCEEDED", {progress_phase: "complete", result: {items: [
  item(1, "VERIFIED", "MATCHES_SOURCE"), item(2, "VERIFIED", "FAILED")]}}));
same(JSON.stringify(rows()), '["operation-result is-warning|1 mod has a problem."]', "singular");
await dismiss();
await push(record("v3", "VERIFY_WORKSHOP_FILES", "SUCCEEDED", {progress_phase: "complete", result: {items: [
  item(1, "VERIFIED", "MATCHES_SOURCE")]}}));
same(JSON.stringify(rows()), '["operation-result is-success|Mod files verified."]', "clean verification");
await dismiss();
// A cancelled or warning result never replaces a failed or recovery row that was not dismissed.
const failure = {progress_phase: "failed", terminal_error: {code: "CONNECTION_FAILED", message: "x"}};
await push(record("f1", "UPDATE_WORKSHOP_ITEMS", "FAILED", failure));
await push(record("c1", "SAVE_PROFILE", "CANCELLED", {progress_phase: "cancelled"}));
same(JSON.stringify(rows()), '["operation-result is-failed|The mods could not be updated."]', "failed row kept");
same(status(), "Last operation failed", "status with a kept failure");
await push(record("v4", "VERIFY_WORKSHOP_FILES", "SUCCEEDED", {result: {items: [item(1, "CHANGED", "DIFFERS")]}}));
same(JSON.stringify(rows()), '["operation-result is-failed|The mods could not be updated."]', "warning keeps the failure");
await push(record("r1", "RESTORE_BACKUP", "RECOVERY_REQUIRED", {progress_phase: "failed",
  terminal_error: {code: "RECOVERY_REQUIRED", message: "x"}}));
same(rows()[0].split("|")[0], "operation-result is-recovery", "a newer problem replaces the failure");
await push(record("c2", "CREATE_BACKUP", "CANCELLED", {progress_phase: "cancelled"}));
same(rows()[0].split("|")[0], "operation-result is-recovery", "recovery row kept");
same(status(), "Recovery required", "status with a kept recovery row");
await push(record("s1", "SAVE_PROFILE", "SUCCEEDED", {progress_phase: "complete", result: {}}));
same(rows().length, 2, "a success sits beside the kept problem");
// The same rule holds when a snapshot rebuilds the bar.
host.snapshotOperations = [record("f1", "UPDATE_WORKSHOP_ITEMS", "FAILED", {...failure, finished_at: "2026-10-03T10:01:00Z"}),
  record("c9", "SAVE_PROFILE", "CANCELLED", {finished_at: "2026-10-03T10:02:00Z"})];
await loadSnapshot(); window.clearTimeout(shellState.pollTimer); await wait(40);
same(JSON.stringify(rows()), '["operation-result is-failed|The mods could not be updated."]', "rebuild keeps the failure");
// After the dismissal a cancelled result is shown, and a later cancelled one replaces it.
await dismiss();
await push(record("c3", "CREATE_BACKUP", "CANCELLED", {progress_phase: "cancelled"}));
await push(record("c4", "SAVE_PROFILE", "CANCELLED", {progress_phase: "cancelled"}));
same(JSON.stringify(rows()), '["operation-result is-cancelled|Profile save cancelled."]', "cancelled replaces cancelled");
// Small texts: the cancelling line per kind, the restore phases, and the apply result without a start.
same(labels.cancelling("AUTHENTICATE_STEAMCMD"), "Cancelling. The sign-in stops at the next safe moment.", "sign-in");
same(labels.cancelling("VERIFY_WORKSHOP_FILES"), "Cancelling. The verification stops at the next safe moment.", "verify");
same(labels.cancelling("UPDATE_WORKSHOP_ITEMS"), "Cancelling. The update stops at the next safe moment.", "update");
same(labels.cancelling("PUBLISH_MODS_AND_KEYS"), "Cancelling. Applying the mods stops at the next safe moment.", "apply");
same(labels.cancelling("LATER_KIND"), "Cancelling. The operation stops at the next safe moment.", "other kind");
same(labels.phase("RESTORE_BACKUP", "VERIFY_SOURCE").text, "Saving a recovery copy and preparing files to restore", "phase");
same(labels.phase("RESTORE_BACKUP", "PREPARE_RECOVERY").text, "Recording the restore plan", "phase before the journal");
same(window.ServerManOperationMessages.result(record("p", "PUBLISH_MODS_AND_KEYS", "SUCCEEDED",
  {result: {start_state: "NOT_REQUESTED"}})).text, "Mods and keys applied. Server start was not requested.", "apply result");
// Backup cards: one reason per compatibility value and a fallback, on the card and as the tooltip.
const backup = (id, day, compatibility) => ({backup_id: id, created_at: `2026-10-0${day}T06:00:00Z`, entry_count: 4,
  total_size: 2048, restore_compatibility: compatibility, restore_compatibility_reason: "host text with profile-v2"});
Object.assign(window.pywebview.api, {
  list_backups: async (id) => ok({profile_id: id, profile_revision: 3, settings_revision: 2, diagnostics: [],
    backups: [backup("b3", 3, "LEGACY_PROFILE_SCHEMA"), backup("b2", 2, "PENDING_RUNTIME_PROFILE_SUPPORT"),
      backup("b1", 1, "LATER_VALUE")], legacy_backups: [], destination_kind: "portable", runtime_profile: "x"}),
  inspect_restore_recovery: async () => ok({blocked: false, diagnostics: []}),
});
commitSection("backups"); await wait(120);
const cards = [...document.querySelectorAll(".backup-item")];
const REASONS = ["This backup was made by an older version, before backups held the full profile data. "
  + "Create a new backup to have one that can be restored.",
  "This backup has content that this version cannot restore.", "This backup cannot be restored by this version."];
same(cards.length, 3, "cards");
cards.forEach((card, index) => {
  same(card.lastElementChild.textContent, REASONS[index], `card reason ${index}`);
  same(card.querySelector(".backup-restore-action").title, REASONS[index], `tooltip ${index}`);
  check(card.textContent.includes("Cannot be restored") && !card.textContent.includes("profile-v2"), `card ${index}`);
});
// QF-012: the label of a locked primary button stays readable (at least 4.5:1 on the panel behind it).
await push(record("busy", "UPDATE_WORKSHOP_ITEMS", "RUNNING", {progress_phase: "check_remote"}));
const locked = document.getElementById("backup-create");
check(locked.getAttribute("aria-disabled") === "true" && locked.classList.contains("button-primary"), "locked primary");
const rgb = (text) => { const parts = text.match(/[\d.]+/g).slice(0, 3).map(Number);
  return text.startsWith("color(") ? parts.map((part) => part * 255) : parts; };
const light = (colour) => { const [r, g, b] = colour.map((value) => { const part = value / 255;
  return part <= 0.03928 ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const style = getComputedStyle(locked);
const behind = rgb(getComputedStyle(locked.closest(".panel")).backgroundColor);
const seen = (colour) => rgb(colour).map((value, index) => value * Number(style.opacity) + behind[index] * (1 - Number(style.opacity)));
const pair = [light(seen(style.color)), light(seen(style.backgroundColor))].sort((a, b) => b - a);
const ratio = (pair[0] + 0.05) / (pair[1] + 0.05);
check(ratio >= 4.5 && Number(style.opacity) < 1, `locked primary label contrast ${ratio.toFixed(2)}`);
"""


def shared_tables() -> str:
    """Return the Python wording tables as script constants for the comparison with the frontend."""
    samples = ["Profile was not found.", "extra arguments contain a credential option",
               "dayz_executable must be inside dayz_root", "The configured DayZ root is unsafe.",
               "write failed for applied_mod_state", "RECOVERY_REQUIRED", ""]
    tables = {
        "ERROR_TEXTS": wording.ERROR_TEXTS, "KIND_ERROR_TEXTS": wording.KIND_ERROR_TEXTS,
        "ROLE_LABELS": wording.ROLE_LABELS,
        "REQUEST_TEXTS": wording.REQUEST_TEXTS, "SERVER_STATES": wording.SERVER_STATES,
        "BLOCK_REASONS": wording.BLOCK_REASONS,
        "SAMPLES": [[message, wording.plain_sentence(message)] for message in samples],
    }
    return "".join(f"const {name} = {json.dumps(value)};\n" for name, value in tables.items())


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class HostSentenceDynamicTests(unittest.TestCase):
    """Plain wording never costs the operator information: host sentences, causes, reasons, and results."""

    def test_host_sentences_translate_identifiers_in_place(self) -> None:
        """QF-009: each example of the review, each identifier class, and the fallback."""
        self.assertEqual(run_shell_harness(HOST_SENTENCES), "PASS")

    def test_conflict_causes_block_reasons_and_shared_texts(self) -> None:
        """QF-007, QF-008, QF-010: causes, reasons on Overview, and equality with the Python wording."""
        self.assertEqual(run_shell_harness(shared_tables() + CONFLICTS_AND_BLOCKS), "PASS")

    def test_verification_look_bar_replacement_small_texts_and_backup_cards(self) -> None:
        """QF-011, QF-013, QF-014, QF-016 in the running page."""
        self.assertEqual(run_shell_harness(BAR_AND_CARDS, budget=6000), "PASS")


if __name__ == "__main__":
    unittest.main()
