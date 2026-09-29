"""Host workflow tests for settings saves, selectors, and failures."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.settings import expand_location_roots  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402
from dayz_serverman.host.api import HostApi  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.application.operations.models import TERMINAL_STATES  # noqa: E402


class SettingsHostWorkflowTests(unittest.TestCase):
    """Host API contracts for the settings workflow and path selection."""
    def setUp(self) -> None:
        """Create a manager composition and host API per test."""
        self.temporary = tempfile.TemporaryDirectory(prefix="settings host ő ")
        self.root = Path(self.temporary.name)
        self.composition = build_composition(self.root / "Manager Root")
        self.api = HostApi(self.composition.host_bridge)
        self.paths = self._fixture_paths()

    def tearDown(self) -> None:
        """Shut down operations and remove the temporary tree."""
        self.composition.operations.shutdown(2)
        self.temporary.cleanup()

    def _fixture_paths(self) -> dict[str, str | None]:
        """Create the fixture directory tree and return the configured roots."""
        dayz = self.root / "DayZ Server ő"
        steam = self.root / "Steam CMD"
        workshop = steam / "steamapps" / "workshop" / "content" / "221100"
        backup = self.root / "Custom Backups"
        # Create the fixture trees before returning configured roots
        for directory in (dayz, steam, workshop, backup):
            directory.mkdir(parents=True, exist_ok=True)
        dayz_executable = dayz / "DayZServer_x64.exe"
        steam_executable = steam / "steamcmd.exe"
        dayz_executable.write_bytes(b"fixture")
        steam_executable.write_bytes(b"fixture")
        return {
            "dayz_root": str(dayz), "steamcmd_root": str(steam),
            "custom_backup_root": None,
        }

    def _settings_input(self, **changes: str | None) -> SettingsInput:
        """Build a settings input from the fixture paths plus the changes."""
        roots = {**self.paths, **changes}
        return SettingsInput(**expand_location_roots(roots))

    def _wait(self, operation_id: str):
        """Block until the operation reaches a terminal state or fail the test."""
        deadline = time.monotonic() + 2
        # Poll until the operation leaves the running states
        while time.monotonic() < deadline:
            record = self.composition.operations.get(operation_id)
            if record.state in TERMINAL_STATES:
                return record
            time.sleep(0.01)
        self.fail("settings operation did not finish")

    def test_fresh_unicode_setup_and_explicit_portable_backup(self) -> None:
        """A fresh Unicode setup keeps the portable backup root explicit."""
        result = self.api.save_settings(self.paths, None)
        self.assertTrue(result["success"])
        # The settings save runs as a queued operation
        terminal = self._wait(result["value"]["operation_id"])
        self.assertEqual(terminal.state.value, "SUCCEEDED")
        saved = self.composition.settings.load()
        self.assertEqual(saved.custom_backup_root, None)
        self.assertIn("ő", saved.dayz_root)
        self.assertEqual(Path(saved.dayz_executable).name, "DayZServer_x64.exe")
        self.assertEqual(Path(saved.steamcmd_executable).name, "steamcmd.exe")
        self.assertEqual(
            Path(saved.workshop_content_root),
            Path(saved.steamcmd_root) / "steamapps" / "workshop" / "content" / "221100",
        )
        # The snapshot keeps the portable backup root visible
        snapshot = self.api.get_application_snapshot()["value"]
        self.assertEqual(
            Path(snapshot["portable_backup_root"]), self.composition.paths.backups,
        )
        backup = next(item for item in snapshot["diagnostics"] if item["role"] == "backup_root")
        self.assertEqual(Path(backup["configured_path"]), self.composition.paths.backups)

    def test_portable_snapshot_path_stays_distinct_from_custom_effective_root(self) -> None:
        """The portable snapshot path stays distinct from a custom effective root."""
        custom = self.root / "Custom Backups"
        custom.mkdir(exist_ok=True)
        initial = self.composition.settings.save(
            self._settings_input(custom_backup_root=str(custom)), None,
        )
        # Capture the effective and portable backup roles
        before = self.api.get_application_snapshot()["value"]
        effective = next(
            item for item in before["diagnostics"] if item["role"] == "backup_root"
        )
        self.assertTrue(Path(effective["configured_path"]).samefile(custom))
        self.assertEqual(Path(before["portable_backup_root"]), self.composition.paths.backups)
        # Clearing the custom root restores the portable default
        changed = {**self.paths, "custom_backup_root": None}
        terminal = self._wait(
            self.api.save_settings(changed, initial.revision)["value"]["operation_id"]
        )
        self.assertEqual(terminal.state.value, "SUCCEEDED")
        after = self.api.get_application_snapshot()["value"]
        self.assertIsNone(after["settings"]["custom_backup_root"])
        self.assertEqual(Path(after["portable_backup_root"]), self.composition.paths.backups)

    def test_path_save_preserves_authentication_and_revision_conflicts(self) -> None:
        """Path saves preserve authentication and reject stale revisions."""
        initial = self.composition.settings.save(SettingsInput(
            **expand_location_roots(self.paths),
            steam_account_name="Operator_1",
            steam_authentication_mode="ACCOUNT",
        ), None)
        custom = self.root / "Other Backups"
        custom.mkdir()
        changed = {**self.paths, "custom_backup_root": str(custom)}
        # Saving new roots must not disturb stored authentication
        accepted = self.api.save_settings(changed, initial.revision)
        terminal = self._wait(accepted["value"]["operation_id"])
        self.assertEqual(terminal.state.value, "SUCCEEDED")
        saved = self.composition.settings.load()
        self.assertEqual(saved.steam_account_name, "Operator_1")
        self.assertEqual(saved.steam_authentication_mode, "ACCOUNT")
        # A stale revision conflicts without changing saved settings
        stale = self.api.save_settings(self.paths, initial.revision)
        self.assertEqual(self._wait(stale["value"]["operation_id"]).terminal_error.code,
                         "REVISION_CONFLICT")

    def test_selector_is_role_limited_typed_cancellable_and_read_only(self) -> None:
        """The selector is role limited, cancellable, and read only."""
        calls: list[tuple[str, str]] = []
        self.api._set_settings_path_selector(
            lambda role, kind: calls.append((role, kind)) or self.paths[role]
        )
        # Select each role through the injected selector
        folder = self.api.select_settings_path("dayz_root")
        steam_folder = self.api.select_settings_path("steamcmd_root")
        self.assertEqual(calls, [("dayz_root", "folder"), ("steamcmd_root", "folder")])
        self.assertEqual(folder["value"]["role"], "dayz_root")
        resolved_dayz = Path(folder["value"]["resolved_paths"]["dayz_executable"])
        self.assertEqual(resolved_dayz.name, "DayZServer_x64.exe")
        self.assertTrue(resolved_dayz.parent.samefile(Path(self.paths["dayz_root"])))
        self.assertEqual(steam_folder["value"]["status"], "READY")
        resolved_steamcmd = Path(
            steam_folder["value"]["resolved_paths"]["steamcmd_executable"]
        )
        self.assertEqual(resolved_steamcmd.name, "steamcmd.exe")
        self.assertTrue(resolved_steamcmd.parent.samefile(Path(self.paths["steamcmd_root"])))
        # Selection stays read-only and rejects non-selectable roles
        self.assertIsNone(self.composition.settings.load().revision)
        self.assertFalse(self.api.select_settings_path("dayz_executable")["success"])
        invalid = self.api.select_settings_path("arbitrary_path")
        self.assertFalse(invalid["success"])
        self.assertEqual(len(calls), 2)
        self.api._set_settings_path_selector(lambda _role, _kind: None)
        self.assertTrue(self.api.select_settings_path("steamcmd_root")["value"]["cancelled"])

    def test_selector_failure_payload_and_bridge_extra_fields_fail_safely(self) -> None:
        """Selector failures and extra bridge fields fail safely."""
        self.api._set_settings_path_selector(
            lambda _role, _kind: (_ for _ in ()).throw(OSError("private path"))
        )
        # A failing selector reports a sanitized invalid request
        failed = self.api.select_settings_path("custom_backup_root")
        self.assertEqual(failed["error"]["code"], "INVALID_REQUEST")
        malformed = self.api.save_settings({"dayz_root": "C:\\x"}, None)
        self.assertFalse(malformed["success"])
        self.assertNotIn("private path", str(failed))
        # Extra bridge fields are rejected
        direct = self.composition.host_bridge.dispatch({
            "contract_version": 1, "request_id": "settings-extra",
            "method": "validate_settings_path_selection",
            "parameters": {"role": "dayz_root", "path": self.paths["dayz_root"],
                           "extra": True},
        })
        self.assertEqual(direct["error"]["code"], "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
