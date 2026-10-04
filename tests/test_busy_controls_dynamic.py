"""Headless Edge checks of the busy rule: locked mutating controls, their explanation, and the activation guard."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# Shared checks: a locked control explains itself and ignores activation; an unlocked one is clean again
CHECKS = r"""
const REASON = "Not available while “Creating backup” is in progress.";
const busyOperation = record("busy", "CREATE_BACKUP", "RUNNING", {cancellable: true, progress_phase: "STAGE",
  progress_percent: 30});
const idleOperation = record("busy", "CREATE_BACKUP", "SUCCEEDED", {progress_phase: "complete", result: {}});
const button = (label, root = document.getElementById("content-region")) =>
  [...root.querySelectorAll("button")].find((item) => item.textContent === label);
const locked = (control, name) => {
  check(control, `${name}: control not found`);
  check(control.getAttribute("aria-disabled") === "true", `${name}: not locked`);
  check((control.getAttribute("aria-describedby") || "").split(" ").includes("busy-reason"),
    `${name}: no description`);
  if (!control.disabled) check(control.title === REASON, `${name}: title "${control.title}"`);
};
const free = (control, name) => {
  check(control, `${name}: control not found`);
  check(!control.hasAttribute("aria-disabled"), `${name}: locked`);
  check(!(control.getAttribute("aria-describedby") || "").includes("busy-reason"), `${name}: description left`);
};
"""

# Overview: the three lifecycle buttons, the confirmation dialog that is open when an operation starts
OVERVIEW = CHECKS + r"""
let starts = 0;
window.pywebview.api.start_server = async () => { starts += 1; return ok({operation_id: "started", state: "QUEUED"}); };
host.operations.set("started", record("started", "START_SERVER", "QUEUED", {cancellable: true}));
await start();
free(button("Start server"), "Start before");
await push(busyOperation);
locked(button("Start server"), "Start server");
locked(button("Save & Stop"), "Save & Stop");
locked(button("Save & Restart"), "Save & Restart");
check(button("Save & Stop").disabled && !button("Save & Stop").title, "a natively disabled control lost its own state");
free(document.getElementById("overview-profile"), "profile selector");
free(button("Save schedule"), "Save schedule");
free(document.querySelector(".nav-item"), "navigation");
check(document.getElementById("busy-reason").textContent === REASON, "reason text");
// Activation guard: a press on the locked button opens nothing.
button("Start server").click(); await wait(10);
check(!document.getElementById("lifecycle-confirmation"), "a locked button opened its confirmation");
// The page stays usable for read-only work and for navigation.
commitSection("logs"); await wait(20);
check(document.querySelector(".logs-panel"), "navigation was blocked while busy");
commitSection("overview"); await wait(30);
locked(button("Start server"), "Start after a page change");
// Unlock when the operation ends.
await push(idleOperation); await wait(30);
free(button("Start server"), "Start after the end");
check(button("Start server").title === "", "title after unlock");
// A dialog that is open when an operation starts: confirm locks, a visible line explains, Cancel works.
button("Start server").click(); await wait(10);
const dialog = document.getElementById("lifecycle-confirmation");
check(dialog && document.querySelector(".app-shell").inert, "confirmation did not open");
check(!document.getElementById("operation-announcer").inert && !document.getElementById("operation-alert").inert,
  "the announcers were made inert by a dialog");
const confirm = button("Start server", dialog);
free(confirm, "dialog confirm before");
host.operations.set("late", record("late", "CREATE_BACKUP", "RUNNING", {progress_phase: "STAGE"}));
window.ServerManOperationBar.sync(host.operations.get("late"));
locked(confirm, "dialog confirm");
const line = dialog.querySelector(".busy-wait-line");
check(line?.textContent === "Wait: “Creating backup” is in progress."
  && line.nextElementSibling.classList.contains("action-row"), "wait line");
confirm.click(); await wait(10);
check(starts === 0 && document.getElementById("lifecycle-confirmation"), "a locked confirm submitted");
window.ServerManOperationBar.sync(record("late", "CREATE_BACKUP", "SUCCEEDED", {revision: 9,
  progress_phase: "complete", result: {}}));
free(confirm, "dialog confirm after");
check(!dialog.querySelector(".busy-wait-line"), "the wait line stayed");
window.ServerManOperationBar.sync(record("again", "CREATE_BACKUP", "RUNNING", {progress_phase: "STAGE"}));
button("Cancel", dialog).click(); await wait(10);
check(!document.getElementById("lifecycle-confirmation") && !document.querySelector(".app-shell").inert,
  "Cancel did not close the dialog while busy");
window.ServerManOperationBar.sync(record("again", "CREATE_BACKUP", "CANCELLED", {revision: 9,
  progress_phase: "cancelled"}));
// A confirmed start hands its operation to the bar at once and the page stays.
button("Start server").click(); await wait(10);
button("Start server", document.getElementById("lifecycle-confirmation")).click(); await wait(20);
check(starts === 1 && barText().includes("Starting server") && document.querySelector(".overview-controls"),
  "the confirmed start did not reach the bar or replaced the page");
check(button("Start server").getAttribute("aria-disabled") === "true"
  && button("Start server").title === "Not available while “Starting server” is in progress.",
  "Start was not locked by its own operation");
"""

# Mods: sign-in, login, download, and verification are locked; fields and Check now stay usable
MODS = CHECKS + r"""
let updates = 0;
Object.assign(window.pywebview.api, {
  get_update_status: async () => ok({mods: {check_state: "OK", checked_at: null,
    last_success_at: "2026-10-03T13:41:07.120Z", error_code: null, update_count: 0, pending_apply_count: 0},
    server_build: null, checking: false, revision: 1}),
  request_update_check: async () => ok({accepted: false, checking: false}),
  list_mod_inventory: async () => ok([{order: 1, name: "Example Mod", directory: "@Example",
    launch_scope: "client", source_kind: "workshop", workshop_id: "111", version: "1.0", state: "CURRENT",
    time_updated: 1, remote_time_updated: 1, remote_check: "OK", pending_reason: null}]),
  update_workshop_items: async () => { updates += 1; return ok({operation_id: "update", state: "QUEUED"}); },
});
await start("mods"); await wait(50);
const byId = (id) => document.getElementById(id);
check(!byId("cancel-workshop-operation") && !button("Cancel current operation"),
  "the page still has its own Cancel button");
const verifyDescription = byId("verify-mod-files").getAttribute("aria-describedby");
const verifyTitle = byId("verify-mod-files").title;
await push(busyOperation);
["save-steam-settings", "authenticate-steamcmd", "update-workshop", "update-start", "verify-mod-files"].forEach((id) =>
  locked(byId(id), id));
check(byId("verify-mod-files").getAttribute("aria-describedby") === `${verifyDescription} busy-reason`,
  "the own description of Verify files was lost");
["steam-auth-mode", "steam-account-name", "check-updates-now"].forEach((id) => {
  free(byId(id), id); check(!byId(id).disabled, `${id}: disabled`); });
byId("update-workshop").click(); await wait(10);
check(updates === 0, "a locked download was submitted");
check(barButton("Cancel"), "Cancel is not reachable in the bar");
await push(idleOperation); await wait(30);
["save-steam-settings", "authenticate-steamcmd", "update-workshop", "update-start", "verify-mod-files"].forEach((id) =>
  free(byId(id), id));
check(byId("verify-mod-files").getAttribute("aria-describedby") === verifyDescription
  && byId("verify-mod-files").title === verifyTitle, "Verify files did not get its own description back");
byId("update-workshop").click(); await wait(10);
check(updates === 1, "an unlocked download was not submitted");
"""

# Backups: creation and its confirmation are locked; history actions and the ZIP browse stay usable
BACKUPS = CHECKS + r"""
let created = 0;
Object.assign(window.pywebview.api, {
  list_backups: async (profileId) => ok({profile_id: profileId, profile_revision: 3, settings_revision: 2,
    backups: [{backup_id: "b1", created_at: "2026-10-02T08:00:00Z", entry_count: 4, total_size: 4096,
      restore_compatibility: "COMPATIBLE", restore_compatibility_reason: null}],
    diagnostics: [], legacy_backups: [], destination_kind: "portable", runtime_profile: "serverman\\alpha\\profile"}),
  inspect_restore_recovery: async () => ok({blocked: false, diagnostics: []}),
  create_backup: async () => { created += 1; return ok({operation_id: "made", state: "QUEUED"}); },
});
await start("backups"); await wait(50);
const create = () => document.getElementById("backup-create");
free(create(), "Create backup before");
create().click(); await wait(10);
const dialog = document.getElementById("backup-confirmation");
check(dialog, "backup confirmation did not open");
await push(busyOperation);
locked(create(), "Create backup");
locked(button("Create backup", dialog), "confirmation button");
check(dialog.querySelector(".busy-wait-line"), "no wait line in the open confirmation");
button("Create backup", dialog).click(); await wait(10);
check(created === 0, "a locked confirmation created a backup");
dialog.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
check(!document.getElementById("backup-confirmation"), "Escape did not close the confirmation while busy");
free(document.querySelector(".backup-restore-action"), "restore review icon");
free(button("Restore profile from backup…"), "ZIP browse");
free(document.getElementById("backup-profile"), "profile selector");
create().click(); await wait(10);
check(!document.getElementById("backup-confirmation"), "a locked Create backup opened its confirmation");
await push(idleOperation); await wait(30);
free(create(), "Create backup after");
// The page shows the result of its own backup with the wording of the bar and draws no progress of its own.
create().click(); await wait(10);
button("Create backup", document.getElementById("backup-confirmation")).click(); await wait(10);
check(created === 1, "the confirmed backup was not submitted");
await push(record("made", "CREATE_BACKUP", "RUNNING", {progress_phase: "HASH", progress_percent: 50}));
const feedback = document.getElementById("backup-feedback");
check(!feedback.querySelector(".progress-track") && !feedback.textContent.includes("%")
  && !feedback.textContent.includes("HASH"), `page progress is still drawn: ${feedback.textContent}`);
check(barText().includes("Writing the file list") && barText().includes("50%"), "bar progress");
await push(record("made", "CREATE_BACKUP", "FAILED", {progress_phase: "failed", last_working_phase: "HASH",
  terminal_error: {code: "STORAGE_FAILURE", message: "Backup storage failed."}}));
check(feedback.textContent === "The backup could not be created. Backup storage failed."
  && feedback.querySelector('[role="alert"]'), `page result: ${feedback.textContent}`);
check(document.getElementById("operation-alert").textContent === "", "the bar announced a result that the page showed");
"""

# Configuration: review stays usable while busy; only the apply step is locked
CONFIGURATION = CHECKS + r"""
let applied = 0;
Object.assign(window.pywebview.api, {
  load_configuration: async (profileId) => ok({profile_id: profileId, target: "server",
    relative_path: "serverDZ.cfg", profile_revision: 3, settings_revision: 2, digest: "d", server_config_digest: null,
    fields: [{key: "hostname", kind: "string", present: true, value: "Old name", secret: false}]}),
  preview_configuration: async () => ok({changed_fields: ["hostname"]}),
  apply_configuration: async () => { applied += 1; return ok({operation_id: "applied", state: "QUEUED"}); },
});
await start("configuration"); await wait(50);
const action = () => document.getElementById("configuration-apply");
const field = document.querySelector("[data-configuration-field]");
await push(busyOperation);
field.value = "New name"; field.dispatchEvent(new Event("input", {bubbles: true}));
check(action().textContent === "Review changes", "review label");
free(action(), "Review changes while busy");
free(document.getElementById("configuration-discard"), "Discard changes");
check(!field.disabled, "a field was disabled while busy");
action().click(); await wait(10);
check(action().textContent === "Apply changes", "the review did not run while busy");
locked(action(), "Apply changes");
action().click(); await wait(10);
check(applied === 0, "a locked apply was submitted");
await push(idleOperation); await wait(30);
free(action(), "Apply changes after");
field.value = "Newer name"; field.dispatchEvent(new Event("input", {bubbles: true}));
check(action().textContent === "Review changes" && !("busyLock" in action().dataset), "review step keeps a mark");
action().click(); await wait(10);
action().click(); await wait(10);
check(applied === 1, "an unlocked apply was not submitted");
"""

# Settings and Profiles: save actions are locked, browse stays usable, a locked form does not submit
SETTINGS_AND_PROFILES = CHECKS + r"""
let saved = 0; let profileSaves = 0;
Object.assign(window.pywebview.api, {
  save_settings: async () => { saved += 1; return ok({operation_id: "saved", state: "QUEUED"}); },
  save_profile: async () => { profileSaves += 1; return ok({operation_id: "profile", state: "QUEUED"}); },
});
await start("settings"); await wait(30);
document.getElementById("backup-mode-custom").click(); await wait(10);
const save = () => document.getElementById("save-path-settings");
check(!save().disabled, "Save locations did not become available");
await push(busyOperation);
locked(save(), "Save locations");
free(document.querySelector("[data-settings-browse='dayz_root']"), "Choose folder");
free(document.getElementById("open-legacy-import"), "Open legacy import");
save().click(); await wait(10);
check(saved === 0, "a locked save was submitted");
// A redraw of the page while busy keeps the lock.
document.getElementById("backup-mode-portable").click(); await wait(10);
locked(save(), "Save locations after a redraw");
window.ServerManTransitions.setDirty("settings-paths", false);
// Profiles: the submit button is locked and the form does not submit, also from the keyboard path.
commitSection("profiles"); await wait(40);
const form = document.getElementById("profile-form");
locked(button("Save profile"), "Save profile");
locked(button("Delete profile"), "Delete profile");
free(button("Preview launch command"), "Preview launch command");
free(button("New profile"), "New profile");
button("Save profile").click(); form.requestSubmit(); await wait(10);
check(profileSaves === 0, "a locked profile form was submitted");
await push(idleOperation); await wait(30);
free(button("Save profile"), "Save profile after");
form.requestSubmit(); await wait(10);
check(profileSaves === 1, "an unlocked profile form was not submitted");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class BusyControlsDynamicTests(unittest.TestCase):
    """Specification section 3: mutating controls lock while an operation runs or waits."""

    def test_overview_controls_and_open_dialog(self) -> None:
        """Lifecycle buttons lock with a reason; an open confirmation locks and explains; Cancel works."""
        self.assertEqual(run_shell_harness(OVERVIEW, budget=6000), "PASS")

    def test_mods_controls(self) -> None:
        """Mods actions lock, keep their own description, and Cancel lives in the bar only."""
        self.assertEqual(run_shell_harness(MODS), "PASS")

    def test_backups_controls_and_page_result(self) -> None:
        """Backup creation locks; the page shows the bar's result wording and no progress of its own."""
        self.assertEqual(run_shell_harness(BACKUPS, budget=6000), "PASS")

    def test_configuration_controls(self) -> None:
        """Review stays available while busy; only the apply step locks."""
        self.assertEqual(run_shell_harness(CONFIGURATION), "PASS")

    def test_settings_and_profile_form(self) -> None:
        """Save locations locks and a locked form does not submit."""
        self.assertEqual(run_shell_harness(SETTINGS_AND_PROFILES), "PASS")


if __name__ == "__main__":
    unittest.main()
