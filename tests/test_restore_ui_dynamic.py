"""Headless browser tests for restore preview, confirmation, and recovery UI."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / (
    "Microsoft/Edge/Application/msedge.exe"
)


HARNESS = r"""
const output = document.getElementById("result");
function check(value, message) { if (!value) throw new Error(message); }
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve }; }
async function flush() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }
function history(profile, revision) { return { success: true, value: {
  profile_id: profile, profile_revision: revision, settings_revision: 4,
  destination_kind: "default", diagnostics: [], backups: [
    { backup_id: `${profile}-one`, created_at: "2026-09-25T12:00:00Z", entry_count: 1,
      total_size: 5, restore_compatibility: "COMPATIBLE" },
    { backup_id: `${profile}-two`, created_at: "2026-09-25T13:00:00Z", entry_count: 1,
      total_size: 5, restore_compatibility: "COMPATIBLE" },
    { backup_id: `${profile}-pending`, created_at: "2026-09-25T14:00:00Z", entry_count: 2,
      total_size: 8, restore_compatibility: "PENDING_RUNTIME_PROFILE_SUPPORT",
      restore_compatibility_reason: "Runtime-profile restore support is pending." },
  ],
} }; }
function preview(profile, backup, revision) { return { success: true, value: {
  profile_id: profile, backup_id: backup, created_at: "2026-09-25T12:00:00Z",
  profile_revision: revision, settings_revision: 4, manifest_digest: "a".repeat(64),
  fingerprint: "b".repeat(64), replacement_count: 1, creation_count: 0,
  recovery_plan: "Verified reverse compensation.",
  targets: [{ action: "REPLACE", target_relative: "profiles/main/state.json",
    target_kind: "RUNTIME_PROFILE" }],
} }; }
const historyCalls = [];
const previewCalls = [];
const applyCalls = [];
const eventCalls = [];
const operationValues = new Map();
window.pywebview = { api: {
  list_backups: () => historyCalls.shift().promise,
  inspect_restore_recovery: async () => ({ success: true, value: { blocked: false, diagnostics: [] } }),
  preview_restore: () => previewCalls.shift().promise,
  apply_restore: () => applyCalls.shift().promise,
  read_operation_events: async () => eventCalls.shift(),
  get_operation: async (operationId) => operationValues.get(operationId),
} };
window.ServerManUi = {
  renderHostError: () => {}, render: () => {}, renderOperation: () => {},
  setHostStatus: () => {}, sectionCopy: { backups: ["Backups", "Backups"] },
};

(async () => {
  window.ServerManWorkspace.activate("backups");
  const alphaHistory = deferred(); historyCalls.push(alphaHistory);
  renderBackupWorkspace([
    { profile_id: "alpha", display_name: "Alpha" },
    { profile_id: "bravo", display_name: "Bravo" },
  ]);
  alphaHistory.resolve(history("alpha", 3)); await flush(); await flush();
  const restoreSelect = document.getElementById("restore-backup");
  check(![...restoreSelect.options].some((option) => option.value === "alpha-pending"),
            "unavailable backup entered the restore selector");
  check(document.getElementById("restore-panel").textContent.includes("unavailable for restore"),
    "restore dependency notice is missing");
  restoreSelect.value = "alpha-one";
  const stalePreview = deferred(); previewCalls.push(stalePreview);
  previewRestore();
  restoreSelect.value = "alpha-two";
  restoreSelect.dispatchEvent(new Event("change"));
  stalePreview.resolve(preview("alpha", "alpha-one", 3)); await flush();
  check(restoreState.preview === null, "late preview crossed backup generation");

  const activePreview = deferred(); previewCalls.push(activePreview);
  previewRestore();
  activePreview.resolve(preview("alpha", "alpha-two", 3)); await flush();
  check(restoreState.preview.value.backup_id === "alpha-two", "active preview did not bind");
  check(document.getElementById("restore-feedback").textContent.includes("Runtime profile — REPLACE"),
    "runtime target kind is not visible in review");
  document.querySelector("#restore-feedback button").focus();
  showRestoreConfirmation();
  const dialog = document.getElementById("restore-confirmation");
  const buttons = dialog.querySelectorAll("button");
  check(dialog.getAttribute("aria-modal") === "true", "restore confirmation is not modal");
  check(document.querySelector("main").inert, "restore modal did not isolate background");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  check(document.activeElement === buttons[1], "reverse Tab escaped restore modal");
  buttons[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  check(document.activeElement === buttons[0], "forward Tab escaped restore modal");
  const profile = document.getElementById("backup-profile");
  profile.value = "bravo"; profile.dispatchEvent(new Event("change"));
  check(profile.value === "alpha", "profile changed behind restore modal");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check(!document.getElementById("restore-confirmation"), "Escape did not cancel restore modal");
  check(!document.querySelector("main").inert, "restore modal did not restore background");

  showRestoreConfirmation();
  const staleApply = deferred(); applyCalls.push(staleApply);
  document.querySelectorAll("#restore-confirmation button")[1].click();
  const bravoHistory = deferred(); historyCalls.push(bravoHistory);
  profile.value = "bravo"; profile.dispatchEvent(new Event("change"));
  staleApply.resolve({ success: true, value: { operation_id: "alpha-stale" } }); await flush();
  check(restoreState.pendingOperation === null, "late apply crossed alpha-to-bravo context");
  bravoHistory.resolve(history("bravo", 7)); await flush(); await flush();

  const bravoSelect = document.getElementById("restore-backup");
  bravoSelect.value = "bravo-one";
  const bravoPreview = deferred(); previewCalls.push(bravoPreview);
  previewRestore(); bravoPreview.resolve(preview("bravo", "bravo-one", 7)); await flush();
  showRestoreConfirmation();
  const activeApply = deferred(); applyCalls.push(activeApply);
  document.querySelectorAll("#restore-confirmation button")[1].click();
  activeApply.resolve({ success: true, value: { operation_id: "restore-active" } }); await flush();
  check(restoreState.pendingOperation.operationId === "restore-active", "active restore was not tracked");
  restoreOperationFinished({ operation_id: "restore-active", state: "RECOVERY_REQUIRED",
    progress_phase: "compensating", progress_percent: 95 });
  check(document.getElementById("restore-feedback").textContent.includes("New mutations are blocked"),
    "recovery-required explanation is missing");

  schedulePoll = () => {};
  shellState.hostReady = true;
  shellState.section = "backups";
  shellState.sessionId = "session-one";
  shellState.cursor = 0;
  const profileContext = captureBackupProfile();
  function setPending(backupOperation, restoreOperation) {
    backupState.pendingOperation = Object.freeze({ context: profileContext, operationId: backupOperation });
    restoreState.pendingOperation = Object.freeze({
      context: restoreContext(document.getElementById("restore-backup").value),
      operationId: restoreOperation,
    });
    const status = backupNode("div", "notice notice-busy", "Pending backup");
    status.id = "backup-operation";
    document.getElementById("backup-feedback").replaceChildren(status);
  }
  setPending("backup-first", "restore-second");
  eventCalls.push({ success: true, value: { session_id: "session-one", next_cursor: 2,
    events: [{ operation_id: "backup-first" }, { operation_id: "restore-second" }] } });
  operationValues.set("backup-first", { success: true, value: {
    operation_id: "backup-first", state: "FAILED", progress_phase: "failed", progress_percent: 100,
    terminal_error: { message: "Backup failed safely." },
  } });
  operationValues.set("restore-second", { success: true, value: {
    operation_id: "restore-second", state: "RECOVERY_REQUIRED",
    progress_phase: "compensating", progress_percent: 95,
  } });
  await pollEvents();
  check(backupState.pendingOperation === null, "backup-first event was not dispatched");
  check(restoreState.pendingOperation === null, "restore-second event was not dispatched");
  check(document.getElementById("restore-feedback").textContent.includes("Recovery required"),
    "restore recovery terminal state was not rendered");

  setPending("backup-second", "restore-first");
  eventCalls.push({ success: true, value: { session_id: "session-one", next_cursor: 4,
    events: [{ operation_id: "restore-first" }, { operation_id: "backup-second" }] } });
  operationValues.set("restore-first", { success: true, value: {
    operation_id: "restore-first", state: "FAILED", progress_phase: "failed", progress_percent: 100,
    terminal_error: { message: "Restore failed safely." },
  } });
  operationValues.set("backup-second", { success: true, value: {
    operation_id: "backup-second", state: "RECOVERY_REQUIRED",
    progress_phase: "recovery", progress_percent: 100,
    terminal_error: { message: "Backup recovery required." },
  } });
  await pollEvents();
  check(restoreState.pendingOperation === null, "restore-first event was not dispatched");
  check(backupState.pendingOperation === null, "backup-second event was not dispatched");
  check(document.getElementById("backup-operation").classList.contains("notice-recovery"),
    "backup recovery terminal state was not rendered");
  output.textContent = "PASS";
})().catch((error) => { output.textContent = `FAIL: ${error.stack || error.message}`; });
"""


class RestoreUiDynamicTests(unittest.TestCase):
    """Headless browser contract for the restore workspace behaviors."""
    def test_restore_generation_modal_and_recovery_behavior(self) -> None:
        """Run the restore generation, modal, and recovery scenario in headless Edge."""
        # Skip when the packaged Edge binary is unavailable
        if not EDGE.is_file():
            self.skipTest("Microsoft Edge is unavailable")
        # Concatenate the frontend modules that implement the scenario
        scripts = "\n".join(
            (FRONTEND / name).read_text(encoding="utf-8")
            for name in ("workspace_context.js", "backup_display.js", "backups.js", "restore.js", "app.js")
        )
        # Compose a standalone page embedding the modules and the harness
        page_text = (
            "<!doctype html><html><body><button id='menu-button'></button>"
            "<div id='drawer-scrim'></div><select id='review-state'></select>"
            "<main><div id='content-region'></div></main>"
            "<pre id='result'>PENDING</pre><script>" + scripts + HARNESS + "</script></body></html>"
        )
        with tempfile.TemporaryDirectory(prefix="serverman_restore_ui_") as temporary:
            root = Path(temporary)
            page = root / "restore.html"
            page.write_text(page_text, encoding="utf-8")
            # Run the scenario in headless Edge and capture the DOM dump
            completed = subprocess.run(
                [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={root / 'edge-data'}", "--dump-dom", page.as_uri()],
                capture_output=True, text=True, timeout=20, check=False,
            )
        # Keep both streams so browser errors surface in assertion messages
        evidence = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, evidence)
        self.assertIn('<pre id="result">PASS</pre>', completed.stdout, evidence)


if __name__ == "__main__":
    unittest.main()
