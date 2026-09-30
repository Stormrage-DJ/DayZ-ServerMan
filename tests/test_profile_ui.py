"""Profile workspace UI tests: static composition checks plus a dynamic Edge harness."""
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


def scripts() -> str:
    """Concatenate the frontend scripts the profile workspace uses."""
    return "\n".join(
        (FRONTEND / name).read_text(encoding="utf-8")
        for name in (
            "workspace_context.js", "transition_guard.js", "profiles_mods.js",
            "profile_copy_mods.js", "profiles.js", "profile_delete.js",
        )
    )


class ProfileUiStaticTests(unittest.TestCase):
    """Contract: profile editing renders safely with explicit dirty and dialog guards."""
    @classmethod
    def setUpClass(cls) -> None:
        """Load the frontend sources shared by the static checks."""
        cls.source = scripts()
        cls.create = (FRONTEND / "profile_create.js").read_text(encoding="utf-8")
        cls.app = (FRONTEND / "app.js").read_text(encoding="utf-8")

    def test_named_api_v2_context_and_safe_rendering(self) -> None:
        """Use the named API v2 surface with safe rendering and context guards."""
        # Named API and context-guard markers must appear in the sources
        for value in (
            "pywebview.api.list_profiles", "pywebview.api.save_profile",
            "pywebview.api.delete_profile", "pywebview.api.preview_profile_command",
            '"runtime_profile"', "launch_scope", '"workshop"', '"external"',
            "Workshop-managed", "Local / unmanaged", "Move up", "Move down", "Remove",
            "captureProfileContext", "profileContextActive", "contextGeneration",
            "editGeneration", "preferredProfileId", 'registerOwner("profiles"',
        ):
            self.assertIn(value, self.source)
        # Rendering must stay text-safe
        self.assertIn("textContent", self.source)
        self.assertNotIn("innerHTML", self.source)
        # Generic mod mutation surfaces must stay absent
        for forbidden in ("download_mod", "publish_mod", "delete_mod", "sync_keys"):
            self.assertNotIn(forbidden, self.source)

    def test_delete_modal_and_central_dirty_refresh_guards_are_explicit(self) -> None:
        """Guard the delete dialog and unsaved edits with central refresh checks."""
        # Dialog and dirty-guard markers must appear in the sources
        for value in (
            'setAttribute("role", "alertdialog")', 'setAttribute("aria-modal", "true")',
            'event.key === "Escape"', 'event.key !== "Tab"', "element.inert = true",
            "returnFocus?.isConnected", "Object.freeze", "replacementAfterDelete",
            "expectedEditGeneration", "Unsaved profile edits preserved",
            "exclusive world storage", "Delete profile and all data",
        ):
            self.assertIn(value, self.source)
        # The app router must consult the central unsaved-changes guard
        self.assertIn('shellState.section === "profiles"', self.app)
        self.assertIn("ServerManTransitions.hasUnsavedChanges()", self.app)

    def test_port_and_tagged_source_conversion_are_not_coerced(self) -> None:
        """Keep port parsing and tagged source conversion strict."""
        self.assertIn(r"/^\d+$/.test(portText)", self.source)
        self.assertIn("Number.isSafeInteger(port)", self.source)
        self.assertIn('kind === "workshop"', self.source)
        self.assertIn("input.required = workshop", self.source)

    def test_mod_editor_is_a_compact_ordered_grid(self) -> None:
        """Render the mod editor as a compact ordered grid."""
        styles = "\n".join((FRONTEND / name).read_text(encoding="utf-8") for name in (
            "controls.css", "styles.css", "profiles.css",
        ))
        # The accessible grid markers live across the sources and styles
        for value in (
            'setAttribute("role", "table")', 'setAttribute("role", "row")',
            'setAttribute("role", "columnheader")', "profile-mod-header",
            "profile-mod-list", "profile-mod-order", "profile-mod-action",
            'dataset.modMove = "up"', 'dataset.modMove = "down"', "refreshModRows",
            ".profile-mod-toolbar { gap: 8px; }",
        ):
            self.assertIn(value, self.source + styles)
        # The old three-column layout must be gone
        self.assertIn("grid-template-columns: 32px", styles)
        self.assertNotIn(".profile-mod { display: grid; grid-template-columns: repeat(3", styles)

    def test_mods_can_be_copied_from_another_profile_into_the_draft(self) -> None:
        """Copying mods offers safe merge and explicit replacement without an immediate save."""
        for value in (
            '"Copy mods"', '"Copy from profile"', '"Add missing mods"',
            '"Replace current list"', "profileModAlreadyListed", "copyProfileMods",
            "source.mods.filter", "modRows.replaceChildren()", "setProfileDirty()",
            "Save the profile to keep this change.",
        ):
            self.assertIn(value, self.source)

    def test_profile_basics_and_runtime_are_compact_responsive_controls(self) -> None:
        """Keep profile basics and runtime as compact responsive controls."""
        styles = "\n".join((FRONTEND / name).read_text(encoding="utf-8") for name in (
            "controls.css", "styles.css", "profiles.css",
        ))
        # Profile basics and runtime styles must stay compact and responsive
        for value in (
            "configuration-fields profile-basics", "profile-runtime-toggle",
            'profileNode("h2", "", "Edit profile")',
            ".profile-basics { grid-template-columns: repeat(3",
            ".profile-runtime { display: grid", 'input[type="checkbox"]',
            ".profile-basics, .profile-runtime { grid-template-columns: 1fr; }",
        ):
            self.assertIn(value, self.source + styles)
        # The outdated schema label must not appear
        self.assertNotIn("Profile schema v2", self.source)

    def test_guided_creation_has_explicit_action_mission_choice_and_generated_paths(self) -> None:
        """Create mode is discoverable and explains every generated input."""
        combined = self.source + self.create
        for marker in (
            '"New profile"', '"New server profile"', "list_profile_missions",
            '"Custom mission…"', "custom_mission", "profileIdFromName",
            "DayZServer_x64.exe", "data-generated-config", "provision_profile",
            "refreshAndSelect", '"Go to Overview"',
        ):
            self.assertIn(marker, combined)
        self.assertIn("readOnly = true", self.source)

    def test_creation_discard_is_non_rendering_and_async_load_is_context_guarded(self) -> None:
        """Leaving a dirty creation draft must not reopen it or let its load finish later."""
        styles = (FRONTEND / "profiles.css").read_text(encoding="utf-8")
        self.assertIn('registerOwner("profiles", discardProfileChanges', self.source)
        self.assertIn("function discardProfileChanges()", self.source)
        discard = self.source.split("function discardProfileChanges()", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("renderProfileForm", discard)
        for marker in (
            'ServerManWorkspace.capture("profiles")',
            "ServerManWorkspace.isActive(workspace)",
            'shellState.section !== "profiles"',
            "loadGeneration !== profileState.loadGeneration",
            "editGeneration !== profileState.editGeneration",
        ):
            self.assertIn(marker, self.create)
        self.assertIn(".profile-create-custom-mission[hidden] { display: none; }", styles)
        self.assertNotIn("cancelTop", self.create)

    def test_creation_form_uses_aligned_rows_and_keeps_failures_in_context(self) -> None:
        """Keep guided fields aligned and actionable provisioning errors on the form."""
        styles = (FRONTEND / "profiles.css").read_text(encoding="utf-8")
        for marker in (
            "function profileCreateRow", "profile-create-copy", "profile-create-label",
            "Compatible files left by a deleted profile are reused",
        ):
            self.assertIn(marker, self.create)
        self.assertIn(".profile-create-row {", styles)
        provision_failure = self.source.split('if (pending.kind === "provision")', 1)[1]
        provision_failure = provision_failure.split("window.ServerManUi.renderOperation", 1)[0]
        self.assertIn("return true", provision_failure)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ProfileUiDynamicTests(unittest.TestCase):
    """End-to-end browser checks of profile races, reloads, and delete accessibility."""
    def test_profile_context_races_terminal_reload_and_delete_accessibility(self) -> None:
        """Run the profile race, terminal reload, and delete accessibility harness."""
        harness = r"""
const output = document.getElementById("result");
function check(value, message) { if (!value) throw new Error(message); }
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
async function flush() { await Promise.resolve(); await Promise.resolve(); await new Promise((done) => setTimeout(done, 0)); }
function record(id, revision = 1) { return { revision, semantic_digest: "a".repeat(64), profile_id: id,
  display_name: id.toUpperCase(), server_executable: "DayZServer_x64.exe",
  server_config: "serverDZ.cfg", runtime_profile: null,
  mission_root: "mpmissions\\dayzOffline.test", game_port: 2302,
  mods: [{ directory: `@${id}`, launch_scope: "client",
    source: { kind: "workshop", workshop_id: id === "alpha" ? "1" : "2" } }],
  extra_arguments: ["-doLogs"] }; }
const alpha = record("alpha"); const bravo = record("bravo");
const host = { list: null, save: null, preview: null, remove: null };
window.pywebview = { api: {
  list_profiles: () => host.list.promise, save_profile: () => host.save.promise,
  delete_profile: () => host.remove.promise, preview_profile_command: () => host.preview.promise,
} };
window.ServerManUi = { renderHostError: () => {}, renderOperation: () => {},
  setHostStatus: (text) => { document.getElementById("host").textContent = text; },
  clearHostStatus: () => { document.getElementById("host").textContent = ""; } };
window.shellState = { section: "profiles" };
(async () => {
  window.ServerManWorkspace.activate("profiles"); host.list = deferred();
  const staleList = openProfilesWorkspace(); window.ServerManWorkspace.activate("overview");
  host.list.resolve({ success: true, value: [alpha, bravo] }); await staleList;
  check(document.getElementById("content-region").textContent === "sentinel", "stale list rendered");

  window.ServerManWorkspace.activate("profiles"); shellState.section = "profiles"; host.list = deferred();
  const initial = openProfilesWorkspace("alpha", true);
  host.list.resolve({ success: true, value: [alpha, bravo] }); await initial;
  check(profileState.selected.profile_id === "alpha", "initial preferred profile was not selected");

  host.preview = deferred(); const preview = previewProfileCommand(); renderProfileForm(bravo);
  host.preview.resolve({ success: true, value: { argv: ["stale-alpha"] } }); await preview;
  check(!document.getElementById("profile-feedback").textContent.includes("stale-alpha"),
    "stale preview crossed selection");

  renderProfileForm(alpha); host.save = deferred();
  const staleSave = saveProfile({ preventDefault() {}, currentTarget: document.getElementById("profile-form") });
  renderProfileForm(bravo); host.save.resolve({ success: true, value: { operation_id: "stale-save" } });
  await staleSave; check(profileState.pending === null, "stale save crossed selection");

  renderProfileForm(bravo); host.save = deferred();
  const reverseSave = saveProfile({ preventDefault() {}, currentTarget: document.getElementById("profile-form") });
  renderProfileForm(alpha); host.save.resolve({ success: true, value: { operation_id: "reverse-stale-save" } });
  await reverseSave; check(profileState.pending === null && profileState.selected.profile_id === "alpha",
    "reverse stale save crossed selection");

  renderProfileForm(bravo);
  host.save = deferred(); const currentForm = document.getElementById("profile-form");
  const activeSave = saveProfile({ preventDefault() {}, currentTarget: currentForm });
  host.save.resolve({ success: true, value: { operation_id: "save-bravo" } }); await activeSave;
  currentForm.querySelector('[name="display_name"]').value = "newer edit";
  currentForm.querySelector('[name="display_name"]').dispatchEvent(new Event("input", { bubbles: true }));
  check(profileOperationFinished({ operation_id: "save-bravo", state: "SUCCEEDED" }),
    "terminal save was not correlated");
  check(profileState.selected.profile_id === "bravo" && profileState.dirty,
    "terminal save discarded newer edits");
  check(document.getElementById("profile-feedback").textContent.includes("Newer profile edits"),
    "newer-edit outcome was not actionable");

  host.list = deferred(); const dirtyRefresh = openProfilesWorkspace("alpha", true);
  host.list.resolve({ success: true, value: [alpha, bravo] }); await dirtyRefresh;
  check(profileState.selected.profile_id === "bravo" && profileState.dirty,
    "refresh replaced dirty profile context");

  renderProfileForm(bravo); host.save = deferred();
  const cleanSave = saveProfile({ preventDefault() {}, currentTarget: document.getElementById("profile-form") });
  host.save.resolve({ success: true, value: { operation_id: "clean-save" } }); await cleanSave;
  host.list = deferred(); profileOperationFinished({ operation_id: "clean-save", state: "SUCCEEDED" });
  host.list.resolve({ success: true, value: [alpha, record("bravo", 2)] }); await flush();
  check(profileState.selected.profile_id === "bravo" && profileState.selected.revision === 2,
    "successful save fell back to first profile");

  renderProfileForm(alpha); const deleteButton = [...document.querySelectorAll("button")]
    .find((item) => item.textContent === "Delete profile"); deleteButton.focus(); deleteButton.click();
  const dialog = document.getElementById("profile-delete-confirmation");
  check(dialog.getAttribute("aria-modal") === "true" && document.getElementById("background").inert,
    "delete dialog did not isolate background");
  const buttons = dialog.querySelectorAll("button"); buttons[1].focus();
  buttons[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  check(document.activeElement === buttons[0], "delete dialog forward Tab escaped");
  buttons[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  check(document.activeElement === buttons[1], "delete dialog reverse Tab escaped");
  dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check(!document.getElementById("profile-delete-confirmation") && !document.getElementById("background").inert,
    "Escape did not cancel delete dialog");
  check(document.activeElement === deleteButton, "delete cancel did not restore focus");

  deleteButton.click(); host.remove = deferred();
  const deleting = confirmDeleteProfile(); host.remove.resolve({ success: true,
    value: { operation_id: "delete-alpha" } }); await deleting;
  host.list = deferred(); profileOperationFinished({ operation_id: "delete-alpha", state: "SUCCEEDED" });
  host.list.resolve({ success: true, value: [record("bravo", 2)] }); await flush();
  check(profileState.selected.profile_id === "bravo", "delete did not choose deterministic neighbor");
  output.textContent = "PASS";
})().catch((error) => { output.textContent = `FAIL: ${error.stack || error.message}`; });
"""
        # Compose a minimal page that hosts the profile scripts and the harness
        page_text = (
            "<!doctype html><html><body><div id='background'><div id='content-region'>sentinel</div>"
            "<h1 id='page-title'>Profiles</h1><span id='host'></span></div>"
            "<pre id='result'>PENDING</pre><script>" + scripts() + harness + "</script></body></html>"
        )
        # Execute the harness in headless Edge
        with tempfile.TemporaryDirectory(prefix="serverman_profile_ui_") as temporary:
            root = Path(temporary); page = root / "profile.html"
            page.write_text(page_text, encoding="utf-8")
            result = subprocess.run(
                [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={root / 'edge-data'}", "--dump-dom", page.as_uri()],
                capture_output=True, text=True, timeout=20, check=False,
            )
        # The harness prints PASS only when every interaction passed
        evidence = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, evidence)
        self.assertIn('<pre id="result">PASS</pre>', result.stdout, evidence)


if __name__ == "__main__":
    unittest.main()
