"""Transactional profile provisioning tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.mission_catalog import MissionCatalog  # noqa: E402
from dayz_serverman.application.profile_provisioning import (  # noqa: E402
    ProfileProvisioningError,
    ProfileProvisioningService,
)
from dayz_serverman.application.profiles import ProfileService  # noqa: E402
from dayz_serverman.application.settings import SettingsService  # noqa: E402
from dayz_serverman.adapters.windows.diagnostics import WindowsPathDiagnostics  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput  # noqa: E402
from dayz_serverman.repositories.json_store import VersionedJsonRepository  # noqa: E402
from dayz_serverman.repositories.paths import PortablePaths  # noqa: E402
from dayz_serverman.repositories.profiles import ProfileRepository  # noqa: E402
from dayz_serverman.repositories.provisioning_journal import (  # noqa: E402
    ProvisioningJournal,
    ProvisioningJournalRepository,
)


def request(**overrides: object) -> dict[str, object]:
    """Return a valid guided-creation request."""
    value: dict[str, object] = {
        "profile_id": "pripyat",
        "display_name": "Pripyat",
        "server_executable": "DayZServer_x64.exe",
        "mission_root": r"mpmissions\Pripyat.Custom",
        "game_port": 2302,
        "mods": [],
        "extra_arguments": [],
    }
    value.update(overrides)
    return value


class ProfileProvisioningTests(unittest.TestCase):
    """Create profiles with generated files and safe rollback."""

    def setUp(self) -> None:
        """Build disposable manager and DayZ roots."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_provision_")
        root = Path(self.temporary.name)
        self.manager = root / "manager"
        self.dayz = root / "DayZ"
        (self.dayz / "mpmissions" / "Pripyat.Custom").mkdir(parents=True)
        (self.dayz / "DayZServer_x64.exe").write_bytes(b"fixture")
        self.paths = PortablePaths.from_root(self.manager)
        self.paths.create_layout()
        self.settings = SettingsService(
            VersionedJsonRepository(self.paths.manager_config),
            self.paths,
            WindowsPathDiagnostics(),
        )
        saved = self.settings.save(SettingsInput(
            dayz_root=str(self.dayz),
            dayz_executable=str(self.dayz / "DayZServer_x64.exe"),
        ), None)
        self.settings_revision = saved.revision
        self.profiles = ProfileService(ProfileRepository(self.paths.profiles), self.settings)
        self.journals = ProvisioningJournalRepository(
            self.paths.operations / "profile-provisioning",
        )
        self.service = ProfileProvisioningService(
            self.profiles, self.settings, MissionCatalog(), self.journals,
        )
        self.progress: list[tuple[str, int]] = []

    def tearDown(self) -> None:
        """Remove disposable roots."""
        self.temporary.cleanup()

    def provision(self, payload: object | None = None) -> dict[str, object]:
        """Run provisioning with a stable synthetic operation identifier."""
        return self.service.provision(
            request() if payload is None else payload,
            self.settings_revision,
            "a" * 32,
            lambda phase, percent: self.progress.append((phase, percent)),
        )

    def test_creates_config_runtime_profile_and_ready_launch(self) -> None:
        """Generate every required artifact and return a ready command preview."""
        result = self.provision()

        target = self.dayz / "serverman" / "pripyat"
        self.assertTrue((target / "profile").is_dir())
        config = (target / "serverDZ.cfg").read_text(encoding="utf-8")
        self.assertIn('hostname = "Pripyat";', config)
        self.assertIn("steamQueryPort = 2305;", config)
        self.assertIn("enableWhitelist = 0;", config)
        self.assertIn("BattlEye = 1;", config)
        self.assertIn("guaranteedUpdates = 1;", config)
        self.assertIn("instanceId = 1;", config)
        self.assertIn("class Missions", config)
        self.assertIn('template = "Pripyat.Custom";', config)
        self.assertFalse((target / ".serverman-provision.json").exists())
        record = self.profiles.read("pripyat")
        self.assertEqual(record.values.server_config, r"serverman\pripyat\serverDZ.cfg")
        self.assertEqual(record.values.runtime_profile, r"serverman\pripyat\profile")
        self.assertEqual(record.values.mission_root, r"mpmissions\Pripyat.Custom")
        self.assertTrue(result["readiness"]["ready"])
        self.assertEqual(self.journals.list(), ())

    def test_builtin_mission_is_written_as_dayz_template(self) -> None:
        """Write the installed mission folder name into the DayZ mission block."""
        (self.dayz / "mpmissions" / "dayzOffline.chernarusplus").mkdir()

        self.provision(request(
            profile_id="chernarus",
            display_name="Chernarus",
            mission_root=r"mpmissions\dayzOffline.chernarusplus",
        ))

        config = self.dayz / "serverman" / "chernarus" / "serverDZ.cfg"
        self.assertIn(
            'template = "dayzOffline.chernarusplus";',
            config.read_text(encoding="utf-8"),
        )

    def test_query_port_tracks_non_default_game_port(self) -> None:
        """Place Steam discovery three ports above a new profile's game port."""
        self.provision(request(game_port=2402))

        config = self.dayz / "serverman" / "pripyat" / "serverDZ.cfg"
        self.assertIn("steamQueryPort = 2405;", config.read_text(encoding="utf-8"))

    def test_instance_id_skips_existing_profiles_and_storage(self) -> None:
        """Allocate an instance ID that cannot reuse another world's persistence."""
        existing_config = self.dayz / "existing.cfg"
        existing_config.write_text("instanceId = 1;\n", encoding="utf-8")
        existing_runtime = self.dayz / "existing-profile"
        existing_runtime.mkdir()
        self.profiles.save(ProfileInput.parse({
            "profile_id": "existing", "display_name": "Existing",
            "server_executable": "DayZServer_x64.exe",
            "server_config": "existing.cfg", "game_port": 2402,
            "runtime_profile": "existing-profile", "mission_root": None,
            "mods": [], "extra_arguments": [],
        }), None)
        (self.dayz / "mpmissions" / "Other" / "storage_2").mkdir(parents=True)

        self.provision(request(profile_id="isolated", display_name="Isolated"))

        config = self.dayz / "serverman" / "isolated" / "serverDZ.cfg"
        self.assertIn("instanceId = 3;", config.read_text(encoding="utf-8"))

    def test_refuses_existing_target_without_overwriting(self) -> None:
        """Fail before mutation when an existing directory is not a generated profile."""
        target = self.dayz / "serverman" / "pripyat"
        target.mkdir(parents=True)
        sentinel = target / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(ProfileProvisioningError, "not reusable"):
            self.provision()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self.profiles.list(), ())

    def test_reuses_existing_generated_target_without_overwriting(self) -> None:
        """Recreate a deleted profile around compatible generated files."""
        target = self.dayz / "serverman" / "pripyat"
        (target / "profile").mkdir(parents=True)
        config = target / "serverDZ.cfg"
        config.write_text("customized = 1;\n", encoding="utf-8")

        result = self.provision()

        self.assertEqual(config.read_text(encoding="utf-8"), "customized = 1;\n")
        self.assertTrue(result["reused_files"])
        self.assertEqual(self.profiles.read("pripyat").values.profile_id, "pripyat")
        self.assertEqual(self.journals.list(), ())

    def test_profile_save_failure_rolls_back_owned_directory(self) -> None:
        """Remove the published target when profile persistence fails."""
        with patch.object(self.profiles, "save", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.provision()

        self.assertFalse((self.dayz / "serverman" / "pripyat").exists())
        self.assertEqual(self.journals.list(), ())

    def test_recovery_removes_only_marked_uncommitted_target(self) -> None:
        """Use marker proof before removing an interrupted publication."""
        operation_id = "b" * 32
        target = self.dayz / "serverman" / "orphan"
        target.mkdir(parents=True)
        (target / ".serverman-provision.json").write_text(
            json.dumps({"operation_id": operation_id}), encoding="utf-8",
        )
        self.journals.save(ProvisioningJournal(
            operation_id, "orphan", str(self.dayz),
            r"serverman\.orphan.stage", r"serverman\orphan", "PUBLISHED",
        ))

        recovered = self.service.recover()

        self.assertEqual(recovered, ("orphan",))
        self.assertFalse(target.exists())
        self.assertEqual(self.journals.list(), ())

    def test_recovery_refuses_unmarked_target(self) -> None:
        """Never delete ambiguous content that lacks ownership proof."""
        operation_id = "c" * 32
        target = self.dayz / "serverman" / "ambiguous"
        target.mkdir(parents=True)
        self.journals.save(ProvisioningJournal(
            operation_id, "ambiguous", str(self.dayz),
            r"serverman\.ambiguous.stage", r"serverman\ambiguous", "PUBLISHED",
        ))

        with self.assertRaisesRegex(ProfileProvisioningError, "requires review"):
            self.service.recover()

        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
