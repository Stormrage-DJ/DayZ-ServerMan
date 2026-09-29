"""Static shell document, token, and frontend script contract tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.host.assets import compose_shell_html  # noqa: E402


FRONTEND = PROJECT_ROOT / "runnable" / "src" / "frontend"
SECTIONS = ("Overview", "Profiles", "Configuration", "Tweaks", "Mods", "Backups", "Logs", "Settings")


class UiShellStaticTests(unittest.TestCase):
    """Shipped shell navigation, security, and workflow contracts."""
    @classmethod
    def setUpClass(cls) -> None:
        """Load the shell document, styles, tokens, scripts, and composed HTML once."""
        cls.document = (FRONTEND / "index.html").read_text(encoding="utf-8")
        cls.styles = (FRONTEND / "styles.css").read_text(encoding="utf-8")
        cls.tokens = (FRONTEND / "tokens.css").read_text(encoding="utf-8")
        cls.script = "\n".join(
            (FRONTEND / name).read_text(encoding="utf-8")
            for name in (
                "shell_ui.js", "workspace_context.js", "transition_guard.js", "profile_context.js",
                "configuration_catalog.js", "tweaks_catalog.js", "tweaks_render.js", "tweaks.js",
                "configuration_edit.js", "configuration_context.js",
                "configuration.js", "backup_display.js", "backups.js", "app.js",
                "restore.js", "profiles_mods.js", "profiles.js", "profile_delete.js", "migration.js",
                "settings.js", "overview_backup.js", "overview.js", "logs.js",
            )
        )
        cls.composed = compose_shell_html(FRONTEND)

    def test_shell_has_navigation_landmarks(self) -> None:
        """The shell exposes navigation landmarks and accessibility hooks."""
        # Confirm every workspace section is a navigation button
        for section in SECTIONS:
            self.assertIn(f">{section}</button>", self.document)
        self.assertIn('href="#main-content"', self.document)
        self.assertIn("<main id=\"main-content\"", self.document)
        self.assertIn("<h1 id=\"page-title\"", self.document)
        self.assertEqual(self.document.count('aria-current="page"'), 1)
        self.assertIn('aria-live="polite"', self.document)

    def test_initial_document_is_complete_operational_shell(self) -> None:
        """The initial document is a complete operational shell."""
        self.assertIn('aria-busy="true"', self.document)
        self.assertIn("Loading workspace", self.document)
        self.assertIn("Local manager", self.document)
        self.assertIn("Application status", self.document)
        self.assertIn('id="application-status-text"', self.document)
        self.assertNotIn('class="topbar"', self.document)
        self.assertNotIn('id="host-status"', self.document)
        self.assertNotIn('id="topbar-title"', self.document)
        self.assertNotIn("Interactive wireframe", self.document)
        self.assertNotIn("Review state", self.document)
        self.assertIn('data-shell-ready="false"', self.document)

    def test_application_status_aggregates_substatuses_by_severity(self) -> None:
        """Application status aggregation covers every severity contract."""
        for contract in (
            "const applicationStatuses = new Map",
            '"is-busy": 1',
            '"is-warning": 2',
            '"is-recovery": 3',
            '"is-error": 4',
            'statuses.every((item) => item.kind === "")',
            'const text = healthy ? "Ready" : overall.text',
            "syncOperationStatus(operation)",
        ):
            self.assertIn(contract, self.script)

    def test_security_policy_and_composed_assets_are_local_only(self) -> None:
        """The content policy and composed assets stay local only."""
        # Confirm the policy directives are embedded in the composed shell
        for directive in (
            "default-src 'none'",
            "connect-src 'none'",
            "frame-src 'none'",
            "form-action 'none'",
            "object-src 'none'",
        ):
            self.assertIn(directive, self.composed)
        # Confirm no remote or inline resources leak into the composed HTML
        for forbidden in ("http://", "https://", "<script src=", 'rel="stylesheet"'):
            self.assertNotIn(forbidden, self.composed)
        self.assertNotIn("INLINE_STYLES", self.composed)
        self.assertNotIn("INLINE_SCRIPT", self.composed)

    def test_tokens_responsive_focus_and_motion_hooks_are_present(self) -> None:
        """Token layers, focus, and reduced-motion hooks are present."""
        for layer in ("Layer 1: primitive tokens", "Layer 2: semantic tokens", "Layer 3: component tokens"):
            self.assertIn(layer, self.tokens)
        self.assertIn(":focus-visible", self.styles)
        self.assertIn("prefers-reduced-motion: reduce", self.styles)
        self.assertIn("max-width: 859px", self.styles)
        self.assertIn("Segoe UI", self.styles)

    def test_backend_text_is_never_inserted_as_markup(self) -> None:
        """Backend text is rendered as text, never as markup."""
        self.assertIn("textContent", self.script)
        self.assertNotIn("innerHTML", self.script)
        self.assertNotIn("insertAdjacentHTML", self.script)

    def test_frontend_uses_no_browser_persistence_or_generic_dispatch(self) -> None:
        """The frontend avoids browser persistence and generic dispatch."""
        for storage_api in ("localStorage", "sessionStorage", "indexedDB"):
            self.assertNotIn(storage_api, self.script)
        self.assertNotIn("pywebview.api.dispatch", self.script)
        self.assertNotIn("reviewState", self.script)

    def test_profile_switchers_share_one_persisted_context(self) -> None:
        """Every profile switcher shares the persisted profile context."""
        for selector in (
            "global-profile", "overview-profile", "configuration-profile",
            "backup-profile", "profile-workspace-selector",
        ):
            self.assertIn(f'"{selector}"', self.script)
        self.assertIn("syncVisibleProfileSelectors", self.script)
        self.assertGreaterEqual(self.script.count("ServerManProfileContext.select"), 5)

    def test_tweaks_render_partial_target_failures_instead_of_staying_busy(self) -> None:
        """Tweaks render partial target failures instead of staying busy."""
        self.assertIn("tweaksState.errors", self.script)
        self.assertIn("results.filter(([, result]) => result.success)", self.script)
        self.assertIn("Unavailable", self.script)
        self.assertIn("renderTweaks();", self.script)

    def test_event_cursor_and_session_changes_reload_snapshot(self) -> None:
        """Expired cursors and session changes reload the snapshot."""
        self.assertIn('error.code === "EVENT_CURSOR_EXPIRED"', self.script)
        self.assertIn("result.value.session_id !== shellState.sessionId", self.script)
        self.assertGreaterEqual(self.script.count("await loadSnapshot();"), 2)
        self.assertIn("document.hidden", self.script)
        self.assertIn('addEventListener("visibilitychange"', self.script)

    def test_backend_progress_cancellation_and_shutdown_use_named_safe_hooks(self) -> None:
        """Progress, cancellation, and shutdown use the named safe hooks."""
        self.assertIn("renderOperation(operation.value)", self.script)
        self.assertIn("renderOperation(active)", self.script)
        self.assertIn("request_operation_cancellation", self.script)
        self.assertIn('role", "progressbar"', self.script)
        self.assertIn("aria-valuenow", self.script)
        self.assertIn("renderShutdown", self.script)
        self.assertIn("declared safe point", self.script)
        self.assertNotIn("request_shutdown({", self.script)

    def test_configuration_has_explicit_preview_apply_discard_and_unsaved_state(self) -> None:
        """Configuration exposes explicit preview, apply, and discard states."""
        for method in (
            "list_profiles",
            "load_configuration",
            "preview_configuration",
            "apply_configuration",
        ):
            self.assertIn(f"pywebview.api.{method}", self.script)
        self.assertIn("Discard changes", self.script)
        self.assertIn("Review changes", self.script)
        self.assertIn("Apply changes", self.script)
        self.assertIn("unsaved change", self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_configuration_review_is_generation_context_and_fingerprint_bound(self) -> None:
        """Configuration review is bound to generation, context, and fingerprint."""
        for guard in (
            "editGeneration",
            "server_config_digest",
            "fingerprint",
            "Object.freeze",
            "generation !== configurationState.editGeneration",
            "apply_configuration(...reviewed.args)",
        ):
            self.assertIn(guard, self.script)
        self.assertIn("captureConfigurationEdit().fingerprint", self.script)

    def test_integer_input_rejects_partial_or_non_finite_coercion(self) -> None:
        """Integer input rejects partial and non-finite coercion."""
        self.assertIn("input.checkValidity()", self.script)
        self.assertIn("Number.isFinite(value)", self.script)
        self.assertIn("Number.isInteger(value)", self.script)
        self.assertIn("Number.isSafeInteger(value)", self.script)
        self.assertIn("must be a whole integer", self.script)
        self.assertNotIn("Number.parseInt", self.script)

    def test_tweaks_mission_review_is_digest_bound_and_uses_named_host_methods(self) -> None:
        """Mission review is digest bound and uses the named host methods."""
        for method in ("load_mission_configuration", "preview_mission_configuration", "apply_mission_configuration"):
            self.assertIn(f'"{method}"', self.script)
        for guard in ("tweaksState.generation", "snapshot.digest", "reviewed.args", "fingerprint"):
            self.assertIn(guard, self.script)
        self.assertIn('target === "starter_loadout"', self.script)
        self.assertIn("Select Apply to publish one file", self.script)

    def test_shared_dirty_guard_covers_navigation_selection_reload_and_close(self) -> None:
        """The shared dirty guard covers navigation, selection, reload, and close."""
        for value in (
            '"shared-configuration"', "registerOwner(", "requestProfile",
            "Leaving this section", "beforeunload", "requestNativeClose",
            "hasUnsavedChanges()", "Unsaved edits preserved",
        ):
            self.assertIn(value, self.script)
        self.assertIn('textContent = "Stay"', self.script)
        self.assertIn('textContent = "Discard changes"', self.script)
        self.assertIn('role", "dialog"', self.script)
        self.assertIn('aria-modal", "true"', self.script)
        self.assertIn("stayButton.focus()", self.script)

    def test_async_responses_require_active_workspace_generation(self) -> None:
        """Async responses require an active workspace generation."""
        for value in (
            "ServerManWorkspace.activate(shellState.section)",
            'ServerManWorkspace.capture("configuration")',
            "ServerManWorkspace.isActive(workspace)",
            "ServerManWorkspace.invalidate()",
        ):
            self.assertIn(value, self.script)
        self.assertGreaterEqual(self.script.count("ServerManWorkspace.isActive(workspace)"), 8)

    def test_configuration_is_server_only_and_uses_guided_groups(self) -> None:
        """Configuration stays server only and uses guided groups."""
        self.assertIn('setDirty("shared-configuration"', self.script)
        self.assertIn('capture("configuration", "shared-configuration")', self.script)
        self.assertIn("ServerManProfileContext.select(profileId)", self.script)
        self.assertIn('const target = "server"', self.script)
        self.assertIn("ServerManConfigurationCatalog.serverGroups", self.script)
        self.assertIn("guided-setting-row", self.script)
        self.assertNotIn("configuration-target", self.script)
        self.assertNotIn("mission-values", self.script)
        self.assertNotIn("Scoped updates (JSON)", self.script)
        self.assertIn("ownerGenerations", self.script)

    def test_guard_keyboard_and_background_accessibility_contract(self) -> None:
        """The guard keyboard and background accessibility contract holds."""
        for value in (
            'event.key === "Escape"', 'event.key !== "Tab"',
            "element.inert = true", "element.inert = inert",
            "returnFocus.isConnected", "action.focus()",
        ):
            self.assertIn(value, self.script)

    def test_context_fingerprints_and_reviewed_arguments_are_immutable(self) -> None:
        """Context fingerprints and reviewed arguments stay immutable."""
        self.assertIn("canonicalFingerprint", self.script)
        self.assertIn("Object.keys(value).sort()", self.script)
        self.assertIn("immutableCopy", self.script)
        self.assertIn("Object.freeze(value.map", self.script)
        self.assertIn("reviewed !== configurationState.reviewed", self.script)
        self.assertIn("result.value.profile_id !== profile", self.script)
        self.assertIn("result.value.target !== target", self.script)

    def test_terminal_operation_refresh_keeps_dirty_until_successful_reload(self) -> None:
        """Terminal operation refresh keeps dirty state until a successful reload."""
        self.assertIn("pendingOperation = result.value.operation_id", self.script)
        self.assertIn("configurationOperationFinished", self.script)
        self.assertIn('operation.state === "SUCCEEDED"', self.script)
        self.assertIn("loadSelectedConfiguration()", self.script)
        self.assertIn("loadSelectedConfiguration(true)", self.script)

    def test_backup_workspace_uses_named_context_bound_verified_workflow(self) -> None:
        """The backup workspace uses the named context-bound verified workflow."""
        for value in (
            "pywebview.api.list_backups", "pywebview.api.create_backup",
            'ServerManWorkspace.capture("backups")', "profile_revision",
            "Create backup?", 'role", "alertdialog"',
            "No backups exist", "diagnostic.message",
            "backupOperationFinished", "progress_phase", "loadBackupHistory()",
            "profileGeneration", "isBackupProfileActive", "pending.context",
            'aria-modal", "true"', 'event.key === "Escape"',
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
            "Restore this backup?", 'role", "alertdialog"',
            'aria-modal", "true"', 'event.key === "Escape"',
            'event.key !== "Tab"', "element.inert = true", "element.inert = inert",
            "Recovery required", "all new mutations are blocked",
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
            "backup-options", "No custom folder selected",
            "current.portable_backup_root",
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
            "profile.revision", "settingsRevision", "Save & Stop",
            "Backup after stop", "save_backup_after_stop", "backup-after-stop",
            "ServerManOverviewBackup", "backupAfterStop",
            'role", "alertdialog"', 'aria-modal", "true"',
            'event.key === "Escape"', "RUNNING_EXTERNAL", "diagnostic_code",
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
        # Confirm the server selection region is appended before the other panels
        self.assertLess(
            self.script.index("region.append(selection)"),
            self.script.index("region.append(panel, controls, details)"),
        )

    def test_global_profile_context_and_guided_tweak_topology_are_present(self) -> None:
        """The global profile context and guided tweak topology are present."""
        for value in (
            'id="global-profile"', "serverman:profile-change", "save_selected_profile",
            "Gameplay Tweaks", "In-Game Events", "Vehicles & Animals",
            "Player", "Stamina", "Map & Loot", "Weather", "Respawns",
            "Starter Items", "Condition & Decay", "load_medical_features",
            "preview_medical_feature", "apply_medical_feature", "guided-setting-hint",
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
        self.assertIn('[["manager", "Manager log"], ["server", "DayZ server log"]]', self.script)
        for value in (
            "captureLogViewport", "restoreLogViewport", "followsEnd",
            "output.scrollTop", "refreshLogs(true, false)",
        ):
            self.assertIn(value, self.script)
        self.assertIn("textContent", self.script)
        self.assertNotIn("read_file", self.script)


if __name__ == "__main__":
    unittest.main()
