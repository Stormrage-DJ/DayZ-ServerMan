"""Headless browser tests for the settings shell workflows."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dayz_serverman.host.assets import compose_shell_html  # noqa: E402
from tests.test_mods_ui_dynamic import _center_rgb, _read_stable_screenshot  # noqa: E402


EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / (
    "Microsoft/Edge/Application/msedge.exe"
)


HARNESS = r"""
<script>
(async () => {
  const ok = (value) => ({success: true, value});
  const wait = () => new Promise((resolve) => setTimeout(resolve, 0));
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const diagnostic = (role, path, status = "UNCONFIGURED") => ({role, status,
    configured_path: path, message: `${role} ${status.toLowerCase()}`,
    action: status === "READY" ? "No action is required." : "Choose the location."});
  let customBackup = null;
  const snapshot = () => ({settings: {revision: 4, dayz_root: null,
    dayz_executable: null, steamcmd_root: null, steamcmd_executable: null,
    workshop_content_root: null, custom_backup_root: customBackup,
    steam_account_name: "Operator_1", steam_authentication_mode: "ACCOUNT"},
    portable_backup_root: "C:\\Manager Root\\backups",
    diagnostics: [diagnostic("dayz_root", null), diagnostic("dayz_executable", null),
      diagnostic("steamcmd_root", null), diagnostic("steamcmd_executable", null),
      diagnostic("workshop_content_root", null),
      diagnostic("backup_root", customBackup || "C:\\Manager Root\\backups", "READY")],
    operations: [], operation_session_id: "settings-test"});
  let selector = async () => ok({cancelled: true});
  let save = async () => ok({operation_id: "save-settings", state: "QUEUED"});
  let savedPayload = null; let savedRevision = null;
  window.pywebview = {api: {
    select_settings_path: (role) => selector(role),
    save_settings: (payload, revision) => { savedPayload = payload; savedRevision = revision;
      return save(payload, revision); },
    get_application_snapshot: async () => ok(snapshot()),
  }};
  try {
    window.ServerManWorkspace.activate("settings");
    await window.ServerManSettings.open(snapshot());
    check(document.getElementById("settings-dayz_root").value === "Not configured", "fresh state");
    check(document.body.textContent.includes("C:\\Manager Root\\backups"), "portable default");
    check(document.getElementById("backup-mode-portable").checked, "portable choice");
    check(document.getElementById("open-legacy-import"), "legacy action remains separate");

    customBackup = "E:\\Custom Backups";
    await window.ServerManSettings.open(snapshot());
    check(document.getElementById("backup-mode-custom").checked, "existing custom choice");
    check(document.querySelector("[data-backup-choice='portable'] .backup-choice-path").textContent
      === "C:\\Manager Root\\backups", "portable path changed with the custom diagnostic");
    document.getElementById("backup-mode-portable").click();
    check(document.querySelector("[data-backup-choice='custom'] .backup-choice-path").textContent
      === "No custom folder selected", "inactive custom choice repeated the portable path");
    document.getElementById("save-path-settings").click(); await wait();
    check(savedPayload.custom_backup_root === null, "custom to portable payload");
    customBackup = null;
    window.ServerManSettings.operationFinished({operation_id: "save-settings", state: "SUCCEEDED"});
    await wait(); await wait();
    check(document.getElementById("backup-mode-portable").checked, "portable reload choice");
    check(document.querySelector("[data-backup-choice='portable'] .backup-choice-path").textContent
      === "C:\\Manager Root\\backups", "portable path after reload");

    let releaseSelection;
    selector = () => new Promise((resolve) => { releaseSelection = resolve; });
    document.querySelector("[data-settings-browse='dayz_root']").click();
    await window.ServerManSettings.open(snapshot());
    releaseSelection(ok({role: "dayz_root", path: "D:\\Stale DayZ", status: "READY",
      message: "ready", action: "No action is required."}));
    await wait();
    check(document.getElementById("settings-dayz_root").value === "Not configured",
      "late selector response crossed generation");

    selector = async (role) => ok({role, path: "D:\\DayZ Server ő", status: "READY",
      message: "dayz root is ready", action: "No action is required.",
      resolved_paths: {dayz_executable: "D:\\DayZ Server ő\\DayZServer_x64.exe"}});
    document.querySelector("[data-settings-browse='dayz_root']").click(); await wait();
    check(document.getElementById("settings-dayz_root").value.includes("ő"), "Unicode selection");
    check(document.body.textContent.includes("DayZServer_x64.exe"), "derived executable shown");
    check(document.body.textContent.includes("Unsaved settings changes"), "dirty state");
    check(window.ServerManTransitions.requestNativeClose() === false, "dirty close veto");
    const guard = document.querySelector("[role='dialog']");
    check(guard?.getAttribute("aria-modal") === "true", "dirty guard modal");
    guard.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));

    save = async () => ({success: false, error: {message: "<img src=x> stale revision"}});
    document.getElementById("save-path-settings").click(); await wait();
    const alert = document.querySelector("#settings-feedback [role='alert']");
    check(alert && document.activeElement === alert, "save error focus");
    check(!alert.querySelector("img") && alert.textContent.includes("<img"), "safe backend text");

    save = async () => ok({operation_id: "save-settings", state: "QUEUED"});
    document.getElementById("save-path-settings").click(); await wait();
    check(Object.keys(savedPayload).length === 3, "exact root settings payload");
    check(!Object.hasOwn(savedPayload, "dayz_executable"), "derived paths stay internal");
    check(savedPayload.custom_backup_root === null, "portable backup stores null");
    check(savedRevision === 4, "optimistic revision");
    window.ServerManSettings.operationFinished({operation_id: "save-settings", state: "FAILED",
      terminal_error: {message: "Revision changed safely."}});
    check(document.activeElement.getAttribute("role") === "alert", "terminal error focus");

    document.getElementById("backup-mode-custom").click();
    selector = async (role) => ok({role, path: "E:\\Custom Backups", status: "READY",
      message: "backup root is ready", action: "No action is required."});
    document.querySelector("[data-settings-browse='custom_backup_root']").click(); await wait();
    check(document.querySelector("[data-backup-choice='custom'] .backup-choice-path").textContent.includes("Custom"),
      "custom backup selection");
    document.body.replaceChildren(); document.body.style.background = "rgb(0, 255, 0)";
  } catch (error) {
    document.body.replaceChildren(); document.body.style.background = "rgb(255, 0, 0)";
    document.body.textContent = error.message;
  }
})();
</script>
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class SettingsUiDynamicTests(unittest.TestCase):
    """Headless browser contract for the settings workspace behaviors."""
    def test_fresh_setup_races_dirty_guard_errors_and_backup_modes(self) -> None:
        """Run the fresh setup, dirty guard, error, and backup mode scenario."""
        with tempfile.TemporaryDirectory(prefix="settings ui ő ") as temporary:
            root = Path(temporary)
            page = root / "settings.html"
            # Compose the settings shell with the scenario harness
            page.write_text(
                compose_shell_html(ROOT / "runnable" / "src" / "frontend").replace(
                    "</body>", HARNESS + "</body>"
                ),
                encoding="utf-8",
            )
            screenshot = root / "result.png"
            profile = root / "edge-data"
            # Render the page in headless Edge and capture a screenshot
            completed = subprocess.run([
                str(EDGE), "--headless", "--disable-gpu", "--no-first-run",
                "--hide-scrollbars", "--window-size=800,560", "--virtual-time-budget=2500",
                f"--user-data-dir={profile}", f"--screenshot={screenshot}", page.as_uri(),
            ], capture_output=True, text=True, timeout=25, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            # The green background proves the success path ran
            red, green, blue = _center_rgb(_read_stable_screenshot(screenshot, profile))
            self.assertGreater(green, 240)
            self.assertLess(red, 15)
            self.assertLess(blue, 15)


if __name__ == "__main__":
    unittest.main()
