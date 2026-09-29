"""UI preference persistence tests for profile selection and backups."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput  # noqa: E402
from tests.profile_fixtures import profile_payload  # noqa: E402


def request(method: str, parameters: dict[str, object] | None = None) -> dict[str, object]:
    """Build one bridge request envelope for the given method and parameters."""
    return {
        "contract_version": 1,
        "request_id": "preference-request",
        "method": method,
        "parameters": parameters or {},
    }


class UiPreferenceTests(unittest.TestCase):
    """Preference persistence, validation, and corrupt-state contracts."""
    def setUp(self) -> None:
        """Build a composition rooted in a temporary manager directory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_preferences_")
        self.manager_root = Path(self.temporary.name) / "Portable Manager"
        self.composition = build_composition(self.manager_root)

    def tearDown(self) -> None:
        """Shut the operations manager down and remove the temporary directory."""
        self.composition.operations.shutdown(2)
        self.temporary.cleanup()

    def dispatch(self, method: str, parameters: dict[str, object] | None = None) -> dict:
        """Dispatch one bridge request and return its response."""
        return self.composition.bridge.dispatch(request(method, parameters))

    def create_profile(self) -> None:
        """Persist the fixture profile so preference writes can reference it."""
        self.composition.profiles.save(ProfileInput.parse(profile_payload()), None)

    def test_missing_preference_is_a_successful_empty_selection(self) -> None:
        """A missing preference file reports an empty selection with MISSING status."""
        result = self.dispatch("get_ui_preferences")
        self.assertTrue(result["success"])
        self.assertIsNone(result["value"]["selected_profile_id"])
        self.assertEqual(result["value"]["backup_after_stop_profiles"], [])
        self.assertEqual(result["value"]["storage_status"], "MISSING")

    def test_selected_profile_survives_composition_restart(self) -> None:
        """The selected profile survives a composition restart."""
        # Save the selection first, then rebuild the composition from the same root
        self.create_profile()
        saved = self.dispatch("save_selected_profile", {"profile_id": "livonia-main"})
        self.assertTrue(saved["success"])
        self.assertTrue(self.composition.paths.ui_preferences.is_file())

        # Rebuild the composition to prove the selection was persisted
        self.composition.operations.shutdown(2)
        self.composition = build_composition(self.manager_root)
        loaded = self.dispatch("get_ui_preferences")
        self.assertEqual(loaded["value"]["selected_profile_id"], "livonia-main")
        self.assertEqual(loaded["value"]["storage_status"], "VALID")

    def test_backup_after_stop_is_remembered_per_profile_and_preserves_selection(self) -> None:
        """Backup-after-stop is remembered per profile without losing the selection."""
        self.create_profile()
        self.assertTrue(self.dispatch(
            "save_selected_profile", {"profile_id": "livonia-main"},
        )["success"])
        # Enable backup after stop for the selected profile
        enabled = self.dispatch("save_backup_after_stop", {
            "profile_id": "livonia-main", "enabled": True,
        })
        self.assertTrue(enabled["success"])
        loaded = self.dispatch("get_ui_preferences")["value"]
        self.assertEqual(loaded["selected_profile_id"], "livonia-main")
        self.assertEqual(loaded["backup_after_stop_profiles"], ["livonia-main"])
        # Disable it again and confirm the entry list clears
        disabled = self.dispatch("save_backup_after_stop", {
            "profile_id": "livonia-main", "enabled": False,
        })
        self.assertTrue(disabled["success"])
        self.assertEqual(
            self.dispatch("get_ui_preferences")["value"]["backup_after_stop_profiles"], [],
        )

    def test_backup_after_stop_rejects_non_boolean_without_mutation(self) -> None:
        """A non-boolean backup-after-stop flag is rejected without mutation."""
        self.create_profile()
        rejected = self.dispatch("save_backup_after_stop", {
            "profile_id": "livonia-main", "enabled": "yes",
        })
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["error"]["code"], "INVALID_REQUEST")
        self.assertFalse(self.composition.paths.ui_preferences.exists())

    def test_unknown_profile_is_rejected_without_creating_preferences(self) -> None:
        """An unknown profile cannot become the persisted selection."""
        result = self.dispatch("save_selected_profile", {"profile_id": "missing"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "NOT_FOUND")
        self.assertFalse(self.composition.paths.ui_preferences.exists())

    def test_corrupt_preference_is_preserved_and_does_not_block_read(self) -> None:
        """A corrupt preference file is preserved and keeps blocking writes."""
        # Plant a corrupt preference file and remember its bytes
        path = self.composition.paths.ui_preferences
        path.write_text("{broken", encoding="utf-8")
        before = path.read_bytes()
        loaded = self.dispatch("get_ui_preferences")
        self.assertTrue(loaded["success"])
        self.assertEqual(loaded["value"]["storage_status"], "CORRUPT")
        # Confirm the corrupt file still blocks preference writes
        self.create_profile()
        saved = self.dispatch("save_selected_profile", {"profile_id": "livonia-main"})
        self.assertFalse(saved["success"])
        self.assertEqual(saved["error"]["code"], "RECOVERY_REQUIRED")
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
