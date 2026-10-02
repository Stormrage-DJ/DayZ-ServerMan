"""Browser interactions for independent catalog and profile restoration review."""

import html
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_backup_ui_dynamic import EDGE, FRONTEND

HARNESS = r'''
const output = document.getElementById("result");
function check(value, message) { if (!value) throw new Error(message); }
async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
let selected = null, profiles = [], submitted = null, previewResolve = null;
let replacing = false, picked = { success: true, value: { cancelled: true } }, catalogCalls = 0;
const backup = { backup_id: "test-backup", profile_id: "test20261001", display_name: "TEST",
  created_at: "2026-10-01T20:00:00Z", total_size: 42, entry_count: 3, can_restore_profile: true };
function value() { return { profile: { profile_id: "test20261001", display_name: "TEST", server_config: "serverman/test/serverDZ.cfg",
  runtime_profile: "serverman/test/profile" }, mission_root: "mpmissions/serverman-test.chernarusplus", instance_id: 9,
  storage_policy: replacing ? "replace_existing" : "allocate_new", game_port: 3302, steam_query_port: 3305,
  affected_profile_ids: replacing ? ["consumer"] : [], missing_mods: [], warnings: [], server_executable_present: true,
  manifest_digest: "a".repeat(64), preview_fingerprint: "b".repeat(64) }; }
window.pywebview = { api: {
  list_profiles: async () => ({ success: true, value: profiles }),
  save_selected_profile: async (id) => { selected = id; return { success: true }; },
  list_backup_catalog: async () => { catalogCalls++; throw new Error("Catalog must not load"); },
  select_backup_archive: async () => picked,
  preview_profile_restore: () => new Promise((resolve) => { previewResolve = resolve; }),
  restore_profile_from_backup: async (...args) => { submitted = args; return { success: true, value: { operation_id: "direct-op" } }; },
  list_backups: async () => ({ success: true, value: { profile_id: "test20261001", profile_revision: 0, settings_revision: 1,
    destination_kind: "default", runtime_profile: "profile", backups: [], diagnostics: [] } }),
} };
window.ServerManUi = { renderHostError() {}, render() {}, setHostStatus() {}, clearHostStatus() {} };
(async () => {
  window.ServerManWorkspace.activate("backups");
  await window.ServerManBackups.open(); await flush();
  const buttons = document.querySelectorAll("#backup-catalog button");
  check(buttons.length === 1 && !buttons[0].disabled && catalogCalls === 0, "browse-only entry point wrong");
  await browseDirectArchive(buttons[0], document.getElementById("backup-catalog"));
  check(!document.getElementById("direct-restore-dialog") && !buttons[0].disabled, "picker dismissal changed workspace");
  picked = { success: false, error: { message: "Older backup lacks complete profile definition." } };
  await browseDirectArchive(buttons[0], document.getElementById("backup-catalog"));
  check(!document.getElementById("direct-restore-dialog") && document.querySelectorAll("#backup-catalog .notice").length === 1, "invalid archive opened modal or repeated warnings");
  let pickerResolve;
  window.pywebview.api.select_backup_archive = () => new Promise(resolve => { pickerResolve = resolve; });
  const stalePick = browseDirectArchive(buttons[0], document.getElementById("backup-catalog"));
  window.ServerManWorkspace.activate("profiles");
  pickerResolve({ success: true, value: backup }); await stalePick;
  check(!document.getElementById("direct-restore-dialog"), "stale picker response opened a modal in another workspace");
  window.ServerManWorkspace.activate("backups");
  window.pywebview.api.select_backup_archive = async () => picked;
  picked = { success: true, value: { ...backup, archive_name: "renamed.zip" } };
  buttons[0].focus(); await browseDirectArchive(buttons[0], document.getElementById("backup-catalog"));
  check(document.getElementById("direct-restore-dialog").open, "restore dialog did not open");
  check(document.activeElement.textContent === "Cancel", "initial focus unsafe");
  document.getElementById("direct-restore-dialog").dispatchEvent(new Event("cancel", { cancelable: true }));
  check(!document.getElementById("direct-restore-dialog") && document.activeElement === buttons[0], "cancel did not restore focus");
  await browseDirectArchive(buttons[0], document.getElementById("backup-catalog"));
  const reviewing = reviewDirectRestore("test-backup");
  document.getElementById("direct-name").value = "Changed";
  document.getElementById("direct-name").dispatchEvent(new Event("input"));
  previewResolve({ success: true, value: value() }); await reviewing;
  check(directRestoreState.preview === null, "stale edited preview accepted");
  replacing = true;
  const replacement = reviewDirectRestore("test-backup"); previewResolve({ success: true, value: value() }); await replacement;
  check(document.getElementById("direct-apply").disabled, "replacement enabled without confirmation");
  check(document.getElementById("direct-review").textContent.includes("consumer"), "affected consumers omitted");
  const confirmation = document.getElementById("direct-overwrite"); confirmation.checked = true; confirmation.dispatchEvent(new Event("change"));
  check(!document.getElementById("direct-apply").disabled, "replacement remained disabled after confirmation");
  await applyDirectRestore();
  check(submitted[8].affected_profile_ids[0] === "consumer" && submitted[8].preview_fingerprint === "b".repeat(64), "confirmation not bound to reviewed consumers/fingerprint");
  profiles = [{ profile_id: "test20261001", display_name: "TEST" }];
  check(directOperationFinished({ operation_id: "direct-op", state: "SUCCEEDED", result: { profile: profiles[0] } }), "commit not correlated");
  await flush();
  check(selected === "test20261001" && window.ServerManProfileContext.selectedId() === selected, "restored profile not selected and remembered");
  check(document.getElementById("global-profile").value === selected, "global selector stale");
  output.textContent = "PASS";
})().catch(error => { output.textContent = "FAIL: " + error.stack; });
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class DirectRestoreUiTests(unittest.TestCase):
    """Use real browser DOM, native modal and shared profile selection code."""

    def test_empty_catalog_review_confirmation_and_selection(self):
        """Verify independent access, stale edit guards and selected profile after commit."""
        scripts = "\n".join((FRONTEND / name).read_text(encoding="utf-8") for name in
            ("workspace_context.js", "transition_guard.js", "profile_context.js", "backup_display.js", "backups.js", "backup_history.js", "profile_restore.js"))
        page = '<!doctype html><html><body><select id="global-profile"></select><div id="content-region"></div><pre id="result">PENDING</pre><script>' + scripts + HARNESS + '</script></body></html>'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "test.html"
            target.write_text(page, encoding="utf-8")
            result = subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
                f"--user-data-dir={root / 'browser'}", "--dump-dom", target.as_uri()], capture_output=True, text=True, timeout=25)
        match = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.S)
        observed = html.unescape(match.group(1)) if match else result.stderr[-1000:]
        self.assertEqual(result.returncode, 0, observed)
        self.assertEqual(observed, "PASS", observed)
