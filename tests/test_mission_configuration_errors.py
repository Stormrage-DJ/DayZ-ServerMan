"""Cover the error mapping of invalid persisted mission files."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dayz_serverman.application.mission_configuration import MissionConfigurationService
from dayz_serverman.application.mission_configuration_coordinator import MissionConfigurationCoordinator
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from tests.test_mission_configuration_workflow import FakeProfiles, FakeSettings, economy_xml, profile


class MissionConfigurationErrorTests(unittest.TestCase):
    """Verify invalid persisted mission files raise the recovery error, not a crash."""

    def setUp(self) -> None:
        """Create a temporary mission tree with an economy globals file."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_mission_errors_")
        self.root = Path(self.temp.name)
        self.mission = self.root / "mpmissions" / "dayzOffline.test"
        (self.mission / "db").mkdir(parents=True)
        self.globals = self.mission / "db" / "globals.xml"
        self.service = MissionConfigurationService(FakeProfiles(profile()), FakeSettings(self.root))  # type: ignore[arg-type]

    def tearDown(self) -> None:
        """Remove the temporary mission tree."""
        self.temp.cleanup()

    def write_invalid_economy(self) -> None:
        """Write a parseable economy file whose loot damage range is inverted."""
        # Each ratio parses, but the domain rule requires minimum <= maximum
        text = economy_xml().replace('name="LootDamageMin" type="1" value="0.00"',
                                     'name="LootDamageMin" type="1" value="0.50"')
        self.assertIn('value="0.50"', text)
        self.globals.write_text(text, encoding="utf-8")

    def test_invalid_persisted_state_raises_configuration_file_error(self) -> None:
        """Verify an inverted persisted damage range raises ConfigurationFileError."""
        self.write_invalid_economy()
        with self.assertRaises(ConfigurationFileError):
            self.service.load("main", "economy")

    def test_bridge_maps_invalid_persisted_state_to_recovery_required(self) -> None:
        """Verify the bridge reports an invalid persisted file as RECOVERY_REQUIRED."""
        self.write_invalid_economy()
        coordinator = MissionConfigurationCoordinator(self.service, None)  # type: ignore[arg-type]
        with self.assertRaises(ApplicationCallError) as caught:
            coordinator.load({"profile_id": "main", "target": "economy"})
        self.assertEqual(caught.exception.code, ErrorCode.RECOVERY_REQUIRED)

    def test_a_starter_routine_that_ends_early_is_refused_by_name(self) -> None:
        """A routine that returns early cannot get the layer; the bridge names it instead of a recovery."""
        init = self.mission / "init.c"
        init.write_text('class CustomMission {\n\toverride void StartingEquipSetup(PlayerBase player, '
                        'bool clothesChosen)\n\t{\n\t\tif ( player ) return;\n\t}\n};\n', encoding="utf-8")
        loaded = self.service.load("main", "starter_loadout")
        before = init.read_bytes()
        coordinator = MissionConfigurationCoordinator(self.service, None)  # type: ignore[arg-type]
        with self.assertRaises(ApplicationCallError) as caught:
            coordinator.preview({"profile_id": "main", "target": "starter_loadout", "expected_profile_revision": 3,
                                 "expected_settings_revision": 4, "expected_digest": loaded["digest"],
                                 "updates": {"items": ["Apple"]}})
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_REQUEST)
        self.assertIn("ends early", caught.exception.safe_message)
        self.assertEqual(init.read_bytes(), before)

if __name__ == "__main__":
    unittest.main()
