"""Headless browser tests for backup workspace races and confirmation safety."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"
# Edge binary used to run the headless browser harness
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / (
    "Microsoft/Edge/Application/msedge.exe"
)


# Browser harness driving profile races, focus safety, and stale correlation
HARNESS = r"""
const output = document.getElementById("result");
function check(condition, message) { if (!condition) throw new Error(message); }
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve }; }
async function flush() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }
function history(profile, revision, diagnostics = [], backups = []) {
  return { success: true, value: { profile_id: profile, profile_revision: revision,
    settings_revision: 4, destination_kind: "default", runtime_profile: "profiles\\main",
    semantic_profile_digest: "a".repeat(64), legacy_backups: [], backups, diagnostics } };
}
const historyCalls = [];
const host = { create: null };
window.pywebview = { api: {
  list_backups: () => historyCalls.shift().promise,
  create_backup: () => host.create.promise,
  list_legacy_backup_references: () => Promise.resolve({ success: true,
    value: { revision: null, entries: [] } }),
  revalidate_legacy_backup_references: () => Promise.resolve({ success: true,
    value: { operation_id: "legacy-revalidate" } }),
} };
window.ServerManUi = { renderHostError: () => {}, render: () => {} };
// This isolated workspace harness supplies the shared selection: the page has no selector of its own.
let selected = "alpha";
const catalog = [{ profile_id: "alpha", display_name: "Alpha" }, { profile_id: "bravo", display_name: "Bravo" }];
window.ServerManProfileContext = { selectedId: () => selected };
// A profile change in the sidebar reopens the page for the new selection, as the shell does.
const choose = (profileId) => { selected = profileId; renderBackupWorkspace(catalog); };

(async () => {
  window.ServerManWorkspace.activate("backups");
  const alpha = deferred(); const bravo = deferred();
  historyCalls.push(alpha, bravo);
  renderBackupWorkspace(catalog);
  check(!document.querySelector("#content-region select"), "the page still has a profile selector");
  choose("bravo");
  alpha.resolve(history("alpha", 1));
  await flush();
  check(backupState.history === null, "late profile history replaced active context");
  bravo.resolve(history("bravo", 3,
    [{ code: "CORRUPT", message: "Safe recovery diagnostic.", scope: "DESTINATION", usable: false }]));
  await flush();
  check(backupState.history.profile_id === "bravo", "active history did not render");
  check(document.querySelector('[data-state="recovery"]').textContent ===
    "Backup destination: Safe recovery diagnostic.",
    "recovery diagnostic state did not render safely");

  const createButton = document.getElementById("backup-create");
  createButton.focus();
  showBackupConfirmation();
  check(document.activeElement.textContent === "Cancel", "confirmation focus is not safe");
  const dialog = document.getElementById("backup-confirmation");
  check(dialog.getAttribute("aria-modal") === "true", "confirmation is not modal");
  check(document.querySelector("main").inert, "confirmation did not isolate background");
  const buttons = dialog.querySelectorAll("button");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  check(document.activeElement === buttons[1], "reverse Tab escaped confirmation");
  buttons[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  check(document.activeElement === buttons[0], "forward Tab escaped confirmation");
  check(backupState.confirmation.context.profileId === "bravo", "confirmation is not bound to its profile");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check(!document.getElementById("backup-confirmation"), "Escape did not cancel confirmation");
  check(!document.querySelector("main").inert, "modal background was not restored");
  check(document.activeElement === createButton, "cancel did not restore invoking focus");

  showBackupConfirmation();
  host.create = deferred();
  document.querySelectorAll("#backup-confirmation button")[1].click();
  check(document.activeElement.textContent === "Starting backup creation.",
    "confirm did not move focus to submission status");
  const alphaReload = deferred(); historyCalls.push(alphaReload);
  choose("alpha");
  host.create.resolve({ success: true, value: { operation_id: "stale-operation" } });
  await flush();
  check(backupState.pendingOperation === null, "late create response crossed workspace context");
  alphaReload.resolve(history("alpha", 5)); await flush();

  showBackupConfirmation();
  host.create = deferred();
  document.querySelectorAll("#backup-confirmation button")[1].click();
  const bravoReload = deferred(); historyCalls.push(bravoReload);
  choose("bravo");
  host.create.resolve({ success: false, error: { message: "stale failure" } });
  await flush();
  check(!document.getElementById("backup-feedback").textContent.includes("stale failure"),
    "reverse stale create error crossed profile context");
  bravoReload.resolve(history("bravo", 6)); await flush();

  const bravoContext = captureBackupProfile();
  const alphaAgain = deferred(); historyCalls.push(alphaAgain);
  choose("alpha");
  alphaAgain.resolve(history("alpha", 7)); await flush();
  for (const state of ["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"]) {
    backupState.pendingOperation = Object.freeze({ context: bravoContext, operationId: `bravo-${state}` });
    check(backupOperationFinished({ operation_id: `bravo-${state}`, state,
      progress_phase: "complete", progress_percent: 100 }), `did not correlate ${state}`);
    check(backupState.history.profile_id === "alpha", `${state} crossed bravo-to-alpha context`);
    check(backupState.pendingOperation === null, `${state} did not clear stale correlation`);
  }

  const alphaContext = captureBackupProfile();
  const bravoAgain = deferred(); historyCalls.push(bravoAgain);
  choose("bravo");
  bravoAgain.resolve(history("bravo", 8)); await flush();
  for (const state of ["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"]) {
    backupState.pendingOperation = Object.freeze({ context: alphaContext, operationId: `alpha-${state}` });
    backupOperationFinished({ operation_id: `alpha-${state}`, state,
      progress_phase: "complete", progress_percent: 100 });
    check(backupState.history.profile_id === "bravo", `${state} crossed alpha-to-bravo context`);
  }

  backupState.pendingOperation = Object.freeze({
    context: captureBackupProfile(), operationId: "backup-operation",
  });
  const refreshed = deferred(); historyCalls.push(refreshed);
  check(backupOperationFinished({ operation_id: "backup-operation", state: "SUCCEEDED",
    progress_phase: "complete", progress_percent: 100 }), "terminal operation was not correlated");
  refreshed.resolve(history("bravo", 8, [], [{
      backup_id: "verified-1", created_at: "2026-09-25T12:00:00.000Z",
      entry_count: 7, total_size: 42, status: "USABLE", runtime_profile: "profiles\\main",
      semantic_profile_digest: "b".repeat(64),
      restore_compatibility: "PENDING_RUNTIME_PROFILE_SUPPORT",
      restore_compatibility_reason: "Runtime-profile restore support is pending task 5.2.",
    }]));
  await flush();
  check(backupState.history.backups[0].backup_id === "verified-1",
    "terminal success did not refresh verified history");
  check(!document.getElementById("backup-history").textContent.includes("b".repeat(64)),
    "history exposed an internal profile digest");
  check(!document.getElementById("backup-history").textContent.includes("verified-1"),
    "history exposed an internal backup identifier");
  check(document.getElementById("backup-history").textContent.includes("Cannot be restored")
    && document.getElementById("backup-history").textContent.includes(
      "This backup has content that this version cannot restore."),
    "history did not expose the restore dependency state without color");
  output.textContent = "PASS";
})().catch((error) => { output.textContent = `FAIL: ${error.stack || error.message}`; });
"""


class BackupUiDynamicTests(unittest.TestCase):
    """Backup workspace race and confirmation contracts in a real browser."""
    def test_profile_history_create_races_and_terminal_refresh(self) -> None:
        """Stale responses never cross the active backup profile context."""
        if not EDGE.is_file():
            self.skipTest("Microsoft Edge is unavailable")
        # Assemble the page from the real frontend scripts and the harness
        scripts = "\n".join(
            (FRONTEND / name).read_text(encoding="utf-8")
            for name in ("operation_labels.js", "operation_messages.js", "diagnostic_labels.js", "workspace_context.js",
                         "backup_display.js", "backups.js", "backup_history.js")
        )
        page_text = (
            "<!doctype html><html><body><main><div id='content-region'></div></main>"
            "<pre id='result'>PENDING</pre><script>" + scripts + HARNESS + "</script></body></html>"
        )
        with tempfile.TemporaryDirectory(prefix="serverman_backup_ui_") as temporary:
            root = Path(temporary)
            page = root / "backup.html"
            page.write_text(page_text, encoding="utf-8")
            completed = subprocess.run(
                [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={root / 'edge-data'}", "--dump-dom", page.as_uri()],
                capture_output=True, text=True, timeout=20, check=False,
            )
        # The harness reports PASS only when every checked invariant holds
        evidence = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, evidence)
        self.assertIn('<pre id="result">PASS</pre>', completed.stdout, evidence)


if __name__ == "__main__":
    unittest.main()
