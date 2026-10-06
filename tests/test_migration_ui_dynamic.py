"""Cover migration workspace generation guards, confirmation, and correlation."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"
# Headless browser binary used to run the migration UI harness
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / (
    "Microsoft/Edge/Application/msedge.exe"
)


# Browser harness driving migration selection, confirmation, and apply flows
HARNESS = r"""
const output = document.getElementById("result");
function check(condition, message) { if (!condition) throw new Error(message); }
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve }; }
async function flush() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }
const selections = []; const previews = []; const applies = [];
window.pywebview = { api: {
  select_legacy_root: () => selections.shift().promise,
  preview_legacy_import: () => previews.shift().promise,
  apply_legacy_import: () => applies.shift().promise,
} };
function preview() { return { success: true, value: {
  preview_id: "a".repeat(32), preview_fingerprint: "b".repeat(64),
  source_label: "Selected legacy manager", source_digest: "c".repeat(64), inventory: [],
  settings: { item_id: "settings:dayz-installation", selectable: true,
    fields: [{ role: "dayz_root", action: "SET" }], warnings: [], conflicts: [] },
  profiles: [{ item_id: "profile:profiles/main.json", selectable: true,
    warnings: [], conflicts: [], profile: { display_name: "Main", mods: [],
      runtime_profile: "profile" } }],
  ignored: ["Legacy UI selection state is not imported."],
  backup_inventory: { item_id: "backups:external-index", count: 2, size: 42,
    status: "EXTERNAL_REFERENCE", selectable: true, action: "CREATE",
    entries: [], warnings: [], conflicts: [], copied: false },
} }; }

(async () => {
  window.ServerManWorkspace.activate("settings");
  renderMigrationWorkspace();
  const staleSelect = deferred(); selections.push(staleSelect);
  document.getElementById("migration-choose").click();
  renderMigrationWorkspace();
  staleSelect.resolve({ success: true, value: { selection_id: "stale" } }); await flush();
  check(previews.length === 0, "stale folder response started preview");

  const activeSelect = deferred(); const activePreview = deferred();
  selections.push(activeSelect); previews.push(activePreview);
  document.getElementById("migration-choose").click();
  activeSelect.resolve({ success: true, value: { selection_id: "active" } }); await flush();
  activePreview.resolve(preview()); await flush();
  check(document.querySelectorAll("[data-migration-item]:checked").length === 3,
    "conflict-free items were not selected");
  check(document.getElementById("migration-results").textContent.includes("External reference only"),
    "external backup policy was not explicit");
  check(!document.getElementById("content-region").textContent.includes("C:\\"),
    "absolute source path reached UI");

  const applyButton = document.getElementById("migration-apply");
  applyButton.focus(); applyButton.click();
  const dialog = document.querySelector('[role="alertdialog"]');
  check(dialog.getAttribute("aria-modal") === "true", "confirmation is not modal");
  check(document.querySelector("main").inert, "confirmation did not isolate background");
  const buttons = dialog.querySelectorAll("button");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  check(document.activeElement === buttons[1], "reverse Tab escaped confirmation");
  buttons[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  check(document.activeElement === buttons[0], "forward Tab escaped confirmation");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check(!document.querySelector('[role="alertdialog"]'), "Escape did not cancel confirmation");
  check(document.activeElement === applyButton, "cancel did not restore focus");

  applyButton.click(); const staleApply = deferred(); applies.push(staleApply);
  document.querySelectorAll('[role="alertdialog"] button')[1].click();
  renderMigrationWorkspace();
  staleApply.resolve({ success: true, value: { operation_id: "stale" } }); await flush();
  check(migrationState.pending === null, "late apply crossed workspace generation");

  const selectAgain = deferred(); const previewAgain = deferred();
  selections.push(selectAgain); previews.push(previewAgain);
  document.getElementById("migration-choose").click();
  selectAgain.resolve({ success: true, value: { selection_id: "again" } }); await flush();
  previewAgain.resolve(preview()); await flush();
  document.getElementById("migration-apply").click();
  const activeApply = deferred(); applies.push(activeApply);
  document.querySelectorAll('[role="alertdialog"] button')[1].click();
  activeApply.resolve({ success: true, value: { operation_id: "active-operation" } }); await flush();
  check(migrationState.pending.operationId === "active-operation", "operation was not correlated");
  check(migrationOperationFinished({ operation_id: "active-operation", state: "SUCCEEDED",
    progress_phase: "REPORT", progress_percent: 100 }), "terminal operation was not handled");
  check(migrationState.pending === null, "terminal correlation was not cleared");
  output.textContent = "PASS";
})().catch((error) => { output.textContent = `FAIL: ${error.stack || error.message}`; });
"""


class MigrationUiDynamicTests(unittest.TestCase):
    """Verify the migration UI through a headless browser harness."""

    def test_generation_modal_and_operation_correlation(self) -> None:
        """Verify stale responses, modal focus handling, and terminal correlation."""
        if not EDGE.is_file():
            self.skipTest("Microsoft Edge is unavailable")
        # Load the frontend scripts and embed them into the harness page
        scripts = "\n".join(
            (FRONTEND / name).read_text(encoding="utf-8")
            for name in ("operation_labels.js", "operation_messages.js", "diagnostic_labels.js", "workspace_context.js",
                         "transition_guard.js", "migration.js")
        )
        page_text = (
            "<!doctype html><html><body><main><h1 id='page-title'>Settings</h1>"
            "<div id='content-region'></div></main><pre id='result'>PENDING</pre><script>"
            + scripts + HARNESS + "</script></body></html>"
        )
        # Run the harness in headless Edge and collect the rendered DOM
        with tempfile.TemporaryDirectory(prefix="serverman_migration_ui_") as temporary:
            root = Path(temporary)
            page = root / "migration.html"
            page.write_text(page_text, encoding="utf-8")
            completed = subprocess.run(
                [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={root / 'edge-data'}", "--dump-dom", page.as_uri()],
                capture_output=True, text=True, timeout=20, check=False, encoding="utf-8", errors="replace",
            )
        # Require a clean run that reached the PASS marker
        evidence = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, evidence)
        self.assertIn('<pre id="result">PASS</pre>', completed.stdout, evidence)


if __name__ == "__main__":
    unittest.main()
