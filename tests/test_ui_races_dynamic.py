"""Headless Edge harness for workspace races and modal keyboard behavior."""
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


def script_source() -> str:
    """Concatenate the frontend scripts that participate in the race scenarios."""
    names = (
        "workspace_context.js", "transition_guard.js", "configuration_catalog.js",
        "configuration_edit.js", "configuration_context.js", "configuration_fields.js",
        "configuration.js",
    )
    return "\n".join((FRONTEND / name).read_text(encoding="utf-8") for name in names)


HARNESS = r"""
const output = document.getElementById("result");
function check(condition, message) { if (!condition) throw new Error(message); }
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve }; }
async function flush() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }
const host = { profile: null, config: null, applyConfig: null };
window.pywebview = { api: {
  list_profiles: () => host.profile.promise,
  load_configuration: () => host.config.promise,
  apply_configuration: () => host.applyConfig.promise,
} };
window.ServerManUi = { renderHostError: () => {}, setHostStatus: () => {} };
window.ServerManProfileContext = { selectedId: () => "alpha", select: () => {} };

(async () => {
  window.ServerManWorkspace.activate("configuration");
  host.profile = deferred();
  const listWork = openConfigurationWorkspace();
  window.ServerManWorkspace.activate("overview");
  host.profile.resolve({ success: true, value: [{ profile_id: "alpha", display_name: "Alpha" }] });
  await listWork;
  check(document.getElementById("content-region").textContent === "sentinel", "stale list rendered");

  window.ServerManWorkspace.activate("configuration");
  host.config = deferred();
  configurationState.loaded = null;
  const loadWork = loadSelectedConfiguration();
  window.ServerManWorkspace.activate("configuration");
  host.config.resolve({ success: true, value: { profile_id: "alpha", target: "server",
    relative_path: "serverDZ.cfg", fields: [] } });
  await loadWork;
  check(configurationState.loaded === null, "stale shared load changed state");
  check(document.getElementById("configuration-fields").textContent === "shared sentinel",
    "stale shared load changed DOM");

  const oldShared = { profile_id: "alpha", target: "server", fields: [] };
  configurationState.loaded = oldShared;
  configurationState.pendingOperation = "shared-op";
  host.config = deferred();
  check(configurationOperationFinished({ operation_id: "shared-op", state: "SUCCEEDED" }),
    "shared terminal event was not handled");
  configurationState.editGeneration += 1;
  host.config.resolve({ success: true, value: { profile_id: "alpha", target: "server",
    relative_path: "new.cfg", fields: [] } });
  await flush();
  check(configurationState.loaded === oldShared, "shared reload overwrote a newer edit");
  check(!document.getElementById("configuration-discard").disabled, "shared controls stayed disabled");

  // Register the guard owner before a terminal event can mark it dirty.
  let sharedDiscard = 0; let sharedFocus = 0; let commits = 0;
  window.ServerManTransitions.registerOwner("shared-configuration",
    () => { sharedDiscard += 1; }, () => { sharedFocus += 1; document.getElementById("shared-focus").focus(); });
  const sharedContext = { profile_id: "alpha", target: "server", profile_revision: 1,
    settings_revision: 1, digest: "shared-digest", server_config_digest: null };
  configurationState.loaded = sharedContext;
  configurationState.values = new Map();
  const sharedCapture = captureConfigurationEdit();
  configurationState.reviewed = Object.freeze({ generation: configurationState.editGeneration,
    fingerprint: sharedCapture.fingerprint, args: sharedCapture.args, result: {} });
  host.applyConfig = deferred();
  const sharedApply = reviewOrApplyConfiguration();
  host.applyConfig.resolve({ success: true, value: { operation_id: "shared-apply-op" } });
  await sharedApply;
  check(configurationState.pendingOperation === "shared-apply-op",
    "configuration apply response was not correlated");
  check(configurationOperationFinished({ operation_id: "shared-apply-op", state: "FAILED" }),
    "configuration operation correlation was lost");

  window.ServerManTransitions.setDirty("shared-configuration", true);
  window.ServerManTransitions.requestOwnerTransition("shared-configuration",
    "Discard server configuration changes?",
    () => { commits += 1; });
  document.querySelectorAll(".unsaved-guard button")[1].click();
  check(sharedDiscard === 1 && commits === 1, "configuration discard did not commit once");

  window.ServerManTransitions.setDirty("shared-configuration", true);
  document.getElementById("trigger").focus();
  window.ServerManTransitions.requestOwnerTransition("shared-configuration", "Keep focus safe.", () => {});
  check(document.getElementById("background").inert, "modal background was not inert");
  document.querySelector(".unsaved-guard").dispatchEvent(
    new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check(!document.querySelector(".unsaved-guard") && !document.getElementById("background").inert,
    "Escape did not perform Stay and restore background");
  check(sharedFocus === 1 && document.activeElement.id === "shared-focus", "Stay focus was not deterministic");

  document.getElementById("trigger").focus();
  window.ServerManTransitions.requestOwnerTransition("shared-configuration", "Trap focus.", () => { commits += 1; });
  const buttons = document.querySelectorAll(".unsaved-guard button");
  buttons[1].focus();
  buttons[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  check(document.activeElement === buttons[0], "forward Tab escaped modal");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  check(document.activeElement === buttons[1], "reverse Tab escaped modal");
  buttons[1].click();
  check(document.activeElement.id === "trigger", "Discard did not restore invoking focus");
  output.textContent = "PASS";
})().catch((error) => { output.textContent = `FAIL: ${error.stack || error.message}`; });
"""


class UiRaceDynamicTests(unittest.TestCase):
    """Workspace race, owner scope, and modal keyboard contracts."""
    def test_workspace_races_owner_scopes_and_modal_keyboard_contract(self) -> None:
        """Stale workspace work cannot render and modal focus stays deterministic."""
        if not EDGE.is_file():
            self.skipTest("Microsoft Edge is unavailable")
        # Assemble one page from the frontend scripts and the race harness
        fixture = """<!doctype html><html><body><div id='background'>
<button id='trigger'>Trigger</button><button id='shared-focus'>Shared</button>
<div id='content-region'>sentinel</div>
<div id='configuration-fields'>shared sentinel</div><div id='configuration-feedback'></div>
<span id='configuration-unsaved'></span><button id='configuration-apply'></button>
<button id='configuration-discard'></button><code id='configuration-path'></code></div>
<pre id='result'>PENDING</pre><script>""" + script_source() + HARNESS + "</script></body></html>"
        # Render the page in headless Edge and capture the resulting DOM
        with tempfile.TemporaryDirectory(prefix="serverman_ui_race_") as temporary:
            root = Path(temporary)
            page = root / "race.html"
            page.write_text(fixture, encoding="utf-8")
            completed = subprocess.run(
                [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={root / 'edge-data'}", "--dump-dom", page.as_uri()],
                capture_output=True, text=True, timeout=20, check=False,
            )
        # Confirm the harness observed PASS in the rendered DOM
        evidence = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, evidence)
        self.assertIn('<pre id="result">PASS</pre>', completed.stdout, evidence)


if __name__ == "__main__":
    unittest.main()
