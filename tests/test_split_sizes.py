"""Size and allowlist checks of the splits before the CLI work (task 2.1)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.composition import ApplicationComposition, build_composition  # noqa: E402
from dayz_serverman.composition_model import ApplicationComposition as ModelComposition  # noqa: E402


# The package folder that holds the split modules
PACKAGE = PROJECT_ROOT / "runnable" / "src" / "python" / "dayz_serverman"
# Completion gate of the source-file size rule
LINE_LIMIT = 300
# Owner allowlist of the bridge, in handler-table order, as built before the split (HEAD 6d52dfa)
OWNER_METHODS_BEFORE_SPLIT = (
    "get_application_snapshot", "get_operation", "read_operation_events", "request_operation_cancellation",
    "request_shutdown", "save_settings", "validate_settings_path_selection", "list_profiles", "read_profile",
    "preview_profile_command", "save_profile", "delete_profile", "list_profile_missions", "provision_profile",
    "get_ui_preferences", "save_selected_profile", "save_backup_after_stop", "save_automatic_update_checks",
    "list_backups", "list_backup_catalog", "create_backup", "preview_restore", "apply_restore",
    "inspect_restore_recovery", "preview_profile_restore", "restore_profile_from_backup", "inspect_backup_archive",
    "load_configuration", "preview_configuration", "apply_configuration", "load_mission_configuration",
    "preview_mission_configuration", "apply_mission_configuration", "convert_starter_loadout",
    "load_medical_features", "preview_medical_feature", "apply_medical_feature", "select_legacy_root",
    "preview_legacy_import", "apply_legacy_import", "list_legacy_backup_references",
    "revalidate_legacy_backup_references", "get_server_status", "start_server", "stop_server", "restart_server",
    "get_online_players", "get_lifecycle_schedule", "save_lifecycle_schedule", "save_steam_settings",
    "authenticate_steamcmd", "update_workshop_items", "list_mod_inventory", "preview_mod_publication",
    "publish_mods_and_keys", "apply_mods_and_restart", "verify_mod_files", "get_update_status",
    "request_update_check", "read_log",
)


class SplitSizeTests(unittest.TestCase):
    """The split modules stay at or below the size gate and keep the bridge allowlist."""

    def test_split_modules_are_at_most_300_lines(self) -> None:
        """composition.py, its two extracted modules and every application/lifecycle*.py file fit the gate."""
        # The fixed files of 1.1 and every lifecycle module of 1.2, including later siblings
        paths = [PACKAGE / name for name in ("composition.py", "composition_model.py", "bridge_composition.py")]
        paths += sorted((PACKAGE / "application").glob("lifecycle*.py"))
        names = {path.name for path in paths}
        self.assertTrue({"lifecycle.py", "lifecycle_ownership.py", "lifecycle_checks.py"} <= names, names)
        for path in paths:
            with self.subTest(path=path.name):
                lines = len(path.read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(lines, LINE_LIMIT, f"{path.name} has {lines} lines")

    def test_publication_journal_got_shorter_through_the_a12_conversion(self) -> None:
        """mod_publication_journal.py (351 lines before task 2.2) converts to shared_files without growing."""
        path = PACKAGE / "repositories" / "mod_publication_journal.py"
        self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 351)

    def test_composition_model_stays_importable_from_composition(self) -> None:
        """The dataclass moved, and the old import path names the same class."""
        self.assertIs(ApplicationComposition, ModelComposition)

    def test_owner_allowlist_is_equal_before_and_after_the_split(self) -> None:
        """The handler table keeps every method, in the same order, and the D11 check on apply and restart."""
        # Build a real composition on a disposable manager root
        temporary = tempfile.TemporaryDirectory(prefix="serverman_split_")
        self.addCleanup(temporary.cleanup)
        composition = build_composition(Path(temporary.name) / "Manager")
        self.addCleanup(composition.operations.shutdown, 2)
        # Compare the set, then the order of the handler table that the facade copied
        self.assertEqual(composition.bridge.allowed_methods, frozenset(OWNER_METHODS_BEFORE_SPLIT))
        self.assertEqual(tuple(composition.bridge._handlers), OWNER_METHODS_BEFORE_SPLIT)
        # "Update & restart" is still wrapped by the running-profile check of the composition root
        handler = composition.bridge._handlers["apply_mods_and_restart"]
        self.assertEqual(handler.__qualname__, "for_running_profile.<locals>.checked")


if __name__ == "__main__":
    unittest.main()
