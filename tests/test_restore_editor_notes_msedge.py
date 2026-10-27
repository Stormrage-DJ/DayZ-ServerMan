"""Headless Edge test of "Map editor records" in the backup restore review (UI-UX Designer 2026-10-16 21:55:05, R4).

The container holds one h5 and one plain paragraph for each note. The Restore button names the container in
`aria-describedby` only when notes exist, and the focus stays on the button.
"""
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
const backups = ["one", "two"].map((name, index) => ({ backup_id: `alpha-${name}`,
  created_at: `2026-09-25T1${index}:00:00Z`, entry_count: 1, total_size: 5, restore_compatibility: "COMPATIBLE" }));
const history = { success: true, value: { profile_id: "alpha", profile_revision: 3, settings_revision: 4,
  destination_kind: "default", diagnostics: [], backups } };
function preview(backup, notes) { const value = { profile_id: "alpha", backup_id: backup,
  created_at: "2026-09-25T12:00:00Z", profile_revision: 3, settings_revision: 4, manifest_digest: "a".repeat(64),
  fingerprint: "b".repeat(64), replacement_count: 1, creation_count: 0, recovery_plan: "Verified copies.",
  targets: [{ action: "REPLACE", target_relative: "mpmissions/x/areaflags.map", target_kind: "DAYZ_CONFIGURATION" }] };
  if (notes) value.editor_notes = notes;
  return { success: true, value }; }
const historyCalls = [];
const previewCalls = [];
const catalog = [{ profile_id: "alpha", display_name: "Alpha" }];
window.ServerManProfileContext = { selectedId: () => "alpha" };
window.ServerManServerState = { refresh: async () => {} };
window.ServerManUpdateStatus = { poll: async () => {}, open: async () => {} };
window.ServerManNavigation = { setCurrent: () => {} };
window.pywebview = { api: {
  list_backups: () => historyCalls.shift().promise,
  inspect_restore_recovery: async () => ({ success: true, value: { blocked: false, diagnostics: [] } }),
  preview_restore: () => previewCalls.shift().promise,
} };
window.ServerManUi = { renderHostError: () => {}, render: () => {}, setHostStatus: () => {} };
window.ServerManOperationBar = { sync: () => {}, settle: () => {}, reset: () => {}, pageResult: () => ({}) };

(async () => {
  window.ServerManWorkspace.activate("backups");
  const loaded = deferred(); historyCalls.push(loaded);
  renderBackupWorkspace(catalog);
  loaded.resolve(history); await flush(); await flush();
  const withNotes = deferred(); previewCalls.push(withNotes);
  document.querySelector('[data-backup-id="alpha-two"]').click();
  withNotes.resolve(preview("alpha-two", ["First note.", "Second note."])); await flush();
  const container = document.getElementById("restore-editor-notes");
  const button = document.querySelector("#restore-feedback button");
  check(container && container.parentElement.classList.contains("notice-warning"),
    "the notes container is not in the review section");
  check(container.querySelectorAll("h5").length === 1 && container.querySelector("h5").textContent === "Map editor records",
    "the container has not one h5 with the heading");
  const paragraphs = [...container.querySelectorAll("p")];
  check(paragraphs.map((node) => node.textContent).join("|") === "First note.|Second note.", "the notes are wrong");
  check(paragraphs.every((node) => node.className === "") && !container.querySelector(".notice"),
    "a note has a class");
  check(button.getAttribute("aria-describedby") === "restore-editor-notes", "the button does not name the notes");
  check(document.activeElement === button, "the focus is not on the button");
  // The heading does not touch the file list, so no extra margin is needed (R3)
  const list = document.querySelector("#restore-feedback .restore-targets");
  check(container.querySelector("h5").getBoundingClientRect().top > list.getBoundingClientRect().bottom,
    "the heading touches the file list");
  check(button.compareDocumentPosition(container) & Node.DOCUMENT_POSITION_PRECEDING, "the notes are not before the button");

  const withoutNotes = deferred(); previewCalls.push(withoutNotes);
  document.querySelector('[data-backup-id="alpha-one"]').click();
  withoutNotes.resolve(preview("alpha-one", null)); await flush();
  const plain = document.querySelector("#restore-feedback button");
  check(!document.getElementById("restore-editor-notes"), "a review without notes shows the container");
  check(!plain.hasAttribute("aria-describedby"), "a review without notes names a description");
  check(document.activeElement === plain, "the focus is not on the button without notes");
  output.textContent = "PASS";
})().catch((error) => { output.textContent = `FAIL: ${error.stack || error.message}`; });
"""


class RestoreEditorNotesEdgeTests(unittest.TestCase):
    """The window layout of the editor notes in the restore review."""

    def test_the_notes_container_and_the_button_description(self) -> None:
        """Run the review with and without notes in headless Edge."""
        if not EDGE.is_file():
            self.skipTest("Microsoft Edge is unavailable")
        scripts = "\n".join(
            (FRONTEND / name).read_text(encoding="utf-8")
            for name in ("operation_labels.js", "operation_messages.js", "operation_results.js", "diagnostic_labels.js",
                         "host_sentences.js", "workspace_context.js", "backup_display.js", "backups.js",
                         "backup_history.js", "restore.js", "sections.js", "app.js"))
        page_text = ("<!doctype html><html><body><button id='menu-button'></button>"
                     "<div id='drawer-scrim'></div><select id='review-state'></select>"
                     "<main><div id='content-region'></div></main>"
                     "<pre id='result'>PENDING</pre><script>" + scripts + HARNESS + "</script></body></html>")
        with tempfile.TemporaryDirectory(prefix="serverman_restore_notes_") as temporary:
            root = Path(temporary)
            page = root / "restore.html"
            page.write_text(page_text, encoding="utf-8")
            completed = subprocess.run(
                [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={root / 'edge-data'}", "--dump-dom", page.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False)
        evidence = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, evidence)
        self.assertIn('<pre id="result">PASS</pre>', completed.stdout, evidence)


if __name__ == "__main__":
    unittest.main()
