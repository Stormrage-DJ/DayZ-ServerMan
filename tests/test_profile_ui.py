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
            "profile_copy_mods.js", "profile_create.js", "profiles.js", "profile_delete.js",
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
            "exclusive world storage", "Existing backup archives will remain",
            "Delete profile data",
        ):
            self.assertIn(value, self.source)
        # The app router must consult the central unsaved-changes guard
        # The registry owns the per-section dispatch; Profiles keeps unsaved edits on a reload
        registry = (FRONTEND / "sections.js").read_text(encoding="utf-8")
        profiles = registry.split('registerSection("profiles"', 1)[1].split("registerSection(", 1)[0]
        self.assertIn("keepsUnsavedEdits: true", profiles)
        self.assertIn("section.keepsUnsavedEdits", self.app)
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
        self.assertIn("// A failed save or delete keeps the form", provision_failure)
        provision_failure = provision_failure.split("// A failed save or delete keeps the form", 1)[0]
        self.assertIn("return true", provision_failure)
        self.assertIn("ServerManOperationBar?.pageResult(operation)", provision_failure)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ProfileUiDynamicTests(unittest.TestCase):
    """End-to-end browser checks of profile races, reloads, and delete accessibility."""
    def test_profile_context_races_terminal_reload_and_delete_accessibility(self) -> None:
        """Run the profile race, terminal reload, and delete accessibility harness."""
        harness = (PROJECT_ROOT / "tests" / "fixtures" / "profile_workspace_harness.js").read_text(encoding="utf-8")
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
                capture_output=True, text=True, timeout=20, check=False, encoding="utf-8", errors="replace",
            )
        # The harness prints PASS only when every interaction passed
        evidence = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, evidence)
        self.assertIn('<pre id="result">PASS</pre>', result.stdout, evidence)


if __name__ == "__main__":
    unittest.main()
