"""Static contracts for the shell's operational workspaces."""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"
SCRIPT_FILES = (
    "shell_ui.js", "workspace_context.js", "profile_context.js", "configuration_catalog.js",
    "tweaks_catalog.js", "tweaks_render.js", "tweaks.js", "backup_display.js", "backups.js", "backup_history.js", "profile_restore.js",
    "restore.js", "migration.js", "settings.js", "overview_backup.js", "overview_readiness.js",
    "overview_lifecycle_dialog.js", "overview.js", "logs.js",
)


class WorkspaceFeaturesStaticTests(unittest.TestCase):
    """Shipped backup, settings, Overview, tweaks, and log contracts."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load shared frontend artifacts once."""
        cls.document = (FRONTEND / "index.html").read_text(encoding="utf-8")
        cls.styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")
        cls.script = "\n".join(
            (FRONTEND / name).read_text(encoding="utf-8") for name in SCRIPT_FILES
        )

    def test_backup_workspace_uses_named_context_bound_verified_workflow(self) -> None:
        """The backup workspace uses the named context-bound verified workflow."""
        for value in (
            "pywebview.api.list_backups", "pywebview.api.create_backup",
            'ServerManWorkspace.capture("backups")', "profile_revision",
            "Create backup?", 'role", "alertdialog"', "No backups exist",
            "diagnostic.message", "backupOperationFinished", "progress_phase",
            "loadBackupHistory()", "profileGeneration", "isBackupProfileActive",
            "pending.context", 'aria-modal", "true"', 'event.key === "Escape"',
            'event.key !== "Tab"', "element.inert = true", "element.inert = inert",
            "Backup destination", "This profile",
        ):
            self.assertIn(value, self.script)
        for forbidden in ("restore_backup", "delete_backup", "browse_backup_path"):
            self.assertNotIn(forbidden, self.script)

    def test_restore_uses_named_reviewed_context_and_accessible_confirmation(self) -> None:
        """Restore uses the named reviewed context and accessible confirmation."""
        for value in (
            "pywebview.api.preview_restore", "pywebview.api.apply_restore",
            "pywebview.api.inspect_restore_recovery", "value.fingerprint",
            "manifest_digest", "restoreContextActive", "pending.context",
            "Restore this backup?", 'role", "alertdialog"', 'aria-modal", "true"',
            'event.key === "Escape"', 'event.key !== "Tab"', "element.inert = true",
            "element.inert = inert", "Recovery required", "all new mutations are blocked",
        ):
            self.assertIn(value, self.script)

    def test_migration_is_opaque_preview_bound_and_accessible(self) -> None:
        """Migration is opaque, preview bound, and accessible."""
        for value in (
            "pywebview.api.select_legacy_root", "pywebview.api.preview_legacy_import",
            "pywebview.api.apply_legacy_import", "preview_fingerprint",
            "backup_inventory.status", "The source remains unchanged", "generation",
            'role", "alertdialog"', 'aria-modal", "true"',
            'event.key === "Escape"', 'event.key !== "Tab"',
            "element.inert = true", "element.inert = inert", "textContent",
        ):
            self.assertIn(value, self.script)
        self.assertNotIn("legacy_root_path", self.script)

    def test_settings_supports_fresh_native_assisted_setup(self) -> None:
        """Settings support fresh native-assisted setup."""
        for value in (
            "Application locations", "dayz_root", "dayz_executable", "Resolved automatically",
            "steamcmd_root", "steamcmd_executable", "workshop_content_root",
            "custom_backup_root", "Portable default", "Moves with the DayZ-ServerMan folder",
            "backup-options", "No custom folder selected", "current.portable_backup_root",
            "pywebview.api.select_settings_path", "pywebview.api.save_settings",
            'registerOwner(\n  "settings-paths"', "settingsState.revision",
            "Unsaved settings changes", "Open legacy import", "textContent",
            "settingsState.generation", "ServerManWorkspace.isActive",
        ):
            self.assertIn(value, self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_overview_exposes_confirmed_revision_bound_lifecycle_controls(self) -> None:
        """Overview exposes confirmed revision-bound lifecycle controls."""
        for value in (
            "pywebview.api.get_server_status", "pywebview.api.start_server",
            "pywebview.api.stop_server", "pywebview.api.restart_server",
            "profile.revision", "settingsRevision", "Save & Stop", "Backup after stop",
            "save_backup_after_stop", "backup-after-stop", "ServerManOverviewBackup",
            "backupAfterStop",
            'role", "alertdialog"', 'aria-modal", "true"', 'event.key === "Escape"',
            "RUNNING_EXTERNAL", "diagnostic_code",
        ):
            self.assertIn(value, self.script)
        self.assertIn("actions.append(backupChoice, start, stop, restart)", self.script)
        self.assertNotIn("force_kill", self.script)

    def test_overview_selects_server_first_and_persists_the_choice(self) -> None:
        """The overview selects the server profile first and persists it."""
        for value in (
            "overview-profile-bar", "Server profile", "get_ui_preferences",
            "save_selected_profile", "Profile selection could not be remembered",
        ):
            self.assertIn(value, self.script + self.styles)
        self.assertLess(
            self.script.index("region.append(selection)"),
            self.script.index("region.append(panel, controls, details)"),
        )

    def test_global_profile_context_and_guided_tweak_topology_are_present(self) -> None:
        """The global profile context and guided tweak topology are present."""
        for value in (
            'id="global-profile"', "serverman:profile-change", "save_selected_profile",
            "Gameplay Tweaks", "In-Game Events", "Vehicles & Animals", "Player", "Stamina",
            "Map & Loot", "Weather", "Respawns", "Starter Items", "Condition & Decay",
            "load_medical_features", "preview_medical_feature", "apply_medical_feature",
            "guided-setting-hint",
        ):
            self.assertIn(value, self.document + self.script)
        self.assertNotIn('label: "Server Config"', self.script)
        self.assertNotIn('subtab: "server"', self.script)

    def test_legacy_starter_conversion_is_explicit_and_digest_bound(self) -> None:
        """Legacy starter conversion is explicit and digest bound."""
        for value in (
            "conversion_required", "Review conversion", "Convert legacy loadout",
            "convert_starter_loadout", "snapshot.profile_revision",
            "snapshot.settings_revision", "snapshot.digest", 'role", "alertdialog"',
            "does not replace its statements or reorder its items",
        ):
            self.assertIn(value, self.script)

    def test_logs_use_fixed_named_query_and_plain_text_rendering(self) -> None:
        """Logs use the fixed named query and plain text rendering."""
        self.assertIn("pywebview.api.read_log", self.script)
        for value in (
            "captureLogViewport", "restoreLogViewport", "followsEnd", "output.scrollTop",
            "refreshLogs(true, false)", "Manager activity", "Manager diagnostics",
            "DayZ server output", "revisions: new Map()", "lastAutomaticAt",
            "previousRevision === result.value.revision", "5000",
        ):
            self.assertIn(value, self.script)
        self.assertIn("textContent", self.script)
        self.assertNotIn("read_file", self.script)


if __name__ == "__main__":
    unittest.main()
