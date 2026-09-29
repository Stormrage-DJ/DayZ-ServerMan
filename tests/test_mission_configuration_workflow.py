"""Cover the queued mission configuration workflow over its named bridge methods."""

from __future__ import annotations

import tempfile
import unittest
import time
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.application.mission_configuration import MissionConfigurationService, MissionPathError
from dayz_serverman.application.mission_configuration_coordinator import MissionConfigurationCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import OperationState
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.application.operations.models import OperationCancelled
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.domain.mission_configuration import GLOBAL_BOOLEAN_KEYS, GLOBAL_INTEGER_KEYS
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.repositories.atomic_file import ContentChangedError
from dayz_serverman.repositories.atomic_file import AtomicFilePublisher


class FakeProfiles:
    """Provide a minimal profile reader returning one fixed record."""

    def __init__(self, profile: ProfileRecord) -> None:
        """Store the profile record returned by every read."""
        self.profile = profile
    def read(self, _profile_id: object) -> ProfileRecord:
        """Return the fixed profile record regardless of identifier."""
        return self.profile


class FakeSettings:
    """Provide a minimal settings reader bound to one manager root."""

    def __init__(self, root: Path) -> None:
        """Store the manager root exposed through the settings value."""
        self.value = SimpleNamespace(dayz_root=str(root), revision=4)
    def load(self) -> object:
        """Return the fixed settings value with its root and revision."""
        return self.value


def profile(mission_root: str | None = "mpmissions\\dayzOffline.test") -> ProfileRecord:
    """Build a main profile record with the given mission root and revision 3."""
    return ProfileRecord(3, ProfileInput("main", "Main", "DayZServer_x64.exe", "serverDZ.cfg",
        mission_root, 2302, (), ()))


class MissionWorkflowTests(unittest.TestCase):
    """Verify mission configuration load, preview, apply, and conversion workflows."""

    def setUp(self) -> None:
        """Create a temporary mission tree with an economy globals file."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_mission_flow_")
        self.root = Path(self.temp.name)
        self.mission = self.root / "mpmissions" / "dayzOffline.test"
        (self.mission / "db").mkdir(parents=True)
        self.globals = self.mission / "db" / "globals.xml"
        self.globals.write_text(economy_xml(), encoding="utf-8")
        self.service = MissionConfigurationService(FakeProfiles(profile()), FakeSettings(self.root))  # type: ignore[arg-type]

    def tearDown(self) -> None:
        """Remove the temporary mission tree."""
        self.temp.cleanup()

    def test_load_preview_apply_use_revision_and_digest_proofs(self) -> None:
        """Verify load, preview, and apply chain revision and digest proofs."""
        loaded = self.service.load("main", "economy")
        preview = self.service.preview("main", "economy", 3, 4, loaded["digest"], {"ZombieMaxCount": 600})
        self.assertNotEqual(preview["proposed_digest"], loaded["digest"])
        self.assertIn('value="500"', self.globals.read_text(encoding="utf-8"))
        # Apply the approved update and verify the file changed
        result = self.service.apply("main", "economy", 3, 4, loaded["digest"],
            {"ZombieMaxCount": 600}, lambda _phase, _progress: None)
        self.assertEqual(result["changed_fields"], ["ZombieMaxCount"])
        self.assertIn('value="600"', self.globals.read_text(encoding="utf-8"))

    def test_mission_folder_is_inferred_from_server_config_when_omitted(self) -> None:
        """Verify an omitted mission root is inferred from the server configuration."""
        (self.root / "serverDZ.cfg").write_text(
            'class Missions { class DayZ { template = "dayzOffline.test"; }; };\n',
            encoding="utf-8",
        )
        service = MissionConfigurationService(
            FakeProfiles(profile(None)), FakeSettings(self.root),  # type: ignore[arg-type]
        )
        loaded = service.load("main", "economy")
        self.assertEqual(loaded["relative_path"], "mpmissions\\dayzOffline.test\\db\\globals.xml")

    def test_stale_file_or_revision_never_publishes(self) -> None:
        """Verify stale revisions and changed files never publish."""
        loaded = self.service.load("main", "economy")
        with self.assertRaises(RevisionConflict):
            self.service.preview("main", "economy", 2, 4, loaded["digest"], {"ZombieMaxCount": 600})
        # Change the file bytes after load so apply must refuse
        self.globals.write_text(economy_xml().replace('name="ZombieMaxCount" type="0" value="500"', 'name="ZombieMaxCount" type="0" value="700"'), encoding="utf-8")
        before = self.globals.read_bytes()
        with self.assertRaises(ContentChangedError):
            self.service.apply("main", "economy", 3, 4, loaded["digest"],
                {"ZombieMaxCount": 600}, lambda _phase, _progress: None)
        self.assertEqual(self.globals.read_bytes(), before)

    def test_cancellation_before_publication_preserves_file(self) -> None:
        """Verify cancellation before publication leaves the file untouched."""
        loaded = self.service.load("main", "economy")
        before = self.globals.read_bytes()
        def checkpoint(phase: str, _progress: int) -> None:
            """Raise OperationCancelled once validation has completed."""
            if phase == "validated":
                raise OperationCancelled("cancel")
        with self.assertRaises(OperationCancelled):
            self.service.apply("main", "economy", 3, 4, loaded["digest"], {"ZombieMaxCount": 600}, checkpoint)
        self.assertEqual(self.globals.read_bytes(), before)

    def test_missing_mission_target_and_traversal_fail_closed(self) -> None:
        """Verify traversal and missing mission targets fail closed."""
        service = MissionConfigurationService(FakeProfiles(profile("..\\outside")), FakeSettings(self.root))  # type: ignore[arg-type]
        with self.assertRaises(MissionPathError):
            service.load("main", "economy")
        self.globals.unlink()
        with self.assertRaises(MissionPathError):
            self.service.load("main", "economy")

    def test_named_bridge_rejects_extra_fields_and_queues_apply(self) -> None:
        """Verify the bridge rejects extra fields and queues a valid apply."""
        operations = OperationManager(OperationStore(self.root / "operations"))
        bridge = BridgeFacade(MissionConfigurationCoordinator(self.service, operations).handlers())
        invalid = bridge.dispatch(_request("load_mission_configuration",
            {"profile_id": "main", "target": "economy", "path": "arbitrary"}))
        self.assertEqual(invalid["error"]["code"], "INVALID_REQUEST")
        loaded = self.service.load("main", "economy")
        parameters = {"profile_id": "main", "target": "economy",
            "expected_profile_revision": 3, "expected_settings_revision": 4,
            "expected_digest": loaded["digest"], "updates": {"ZombieMaxCount": 650}}
        result = bridge.dispatch(_request("apply_mission_configuration", parameters))
        operation_id = result["value"]["operation_id"]
        # Wait for the queued apply operation to reach a terminal state
        deadline = time.monotonic() + 2
        while operations.get(operation_id).state not in {OperationState.SUCCEEDED, OperationState.FAILED}:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        self.assertEqual(operations.get(operation_id).state, OperationState.SUCCEEDED)
        operations.shutdown(2)

    def test_reparse_or_symlink_mission_path_is_rejected(self) -> None:
        """Verify symlinked mission paths are rejected."""
        actual = self.root / "actual-mission"
        (actual / "db").mkdir(parents=True)
        (actual / "db" / "globals.xml").write_text(
            '<variables><var name="ZombieMaxCount" value="1"/></variables>', encoding="utf-8")
        # Skip when the environment cannot create directory symlinks
        link = self.root / "mpmissions" / "linked"
        try:
            link.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlink creation is unavailable")
        service = MissionConfigurationService(FakeProfiles(profile("mpmissions\\linked")), FakeSettings(self.root))  # type: ignore[arg-type]
        with self.assertRaises(MissionPathError):
            service.load("main", "economy")

    def test_atomic_publication_failure_preserves_old_single_file(self) -> None:
        """Verify a failed atomic replace preserves the old file and removes temp files."""
        def fail_replace(_source: Path, _target: Path) -> None:
            """Raise the synthetic failure for every replacement attempt."""
            raise OSError("synthetic replace failure")
        service = MissionConfigurationService(
            FakeProfiles(profile()), FakeSettings(self.root), AtomicFilePublisher(fail_replace),  # type: ignore[arg-type]
        )
        loaded = service.load("main", "economy")
        before = self.globals.read_bytes()
        with self.assertRaises(OSError):
            service.apply("main", "economy", 3, 4, loaded["digest"],
                {"ZombieMaxCount": 900}, lambda _phase, _progress: None)
        self.assertEqual(self.globals.read_bytes(), before)
        self.assertEqual(list(self.globals.parent.glob("*.tmp")), [])

    def test_partial_updates_validate_complete_economy_and_weather_state(self) -> None:
        """Verify partial updates still validate the complete economy and weather state."""
        loaded = self.service.load("main", "economy")
        with self.assertRaisesRegex(Exception, "LootDamageMin"):
            self.service.preview("main", "economy", 3, 4, loaded["digest"], {"LootDamageMin": .9})
        # Corrupt one economy value and require load to reject it
        invalid = economy_xml().replace('name="FoodDecay" type="0" value="1"', 'name="FoodDecay" type="0" value="2"')
        self.globals.write_text(invalid, encoding="utf-8")
        with self.assertRaises(Exception):
            self.service.load("main", "economy")

        # Add a weather file whose limits break the range rules
        weather = self.mission / "cfgweather.xml"
        weather.write_text('<weather enable="0" reset="0"><rain><limits min="0.0" max="1.0"/>'
            '<timelimits min="60.5" max="120"/><changelimits min="0.0" max="1.0"/>'
            '<thresholds end="60"/></rain></weather>', encoding="utf-8")
        with self.assertRaises(Exception):
            self.service.load("main", "weather")

    def test_cross_field_rules_merge_partial_updates_before_preview(self) -> None:
        """Verify cross-field rules merge partial updates before preview validation."""
        weather = self.mission / "cfgweather.xml"
        weather.write_text('<weather enable="0" reset="0"><rain><limits min="0.0" max="1.0"/>'
            '<timelimits min="60" max="120"/><changelimits min="0.0" max="1.0"/>'
            '<thresholds end="60"/></rain></weather>', encoding="utf-8")
        loaded = self.service.load("main", "weather")
        with self.assertRaisesRegex(Exception, "rain_time_min"):
            self.service.preview("main", "weather", 3, 4, loaded["digest"], {"rain_time_min": 121})

        # Probe an event update whose minimum exceeds the stored maximum
        events = self.mission / "db" / "events.xml"
        events.write_text(f"<events>{population_event('AnimalBear')}</events>", encoding="utf-8")
        loaded = self.service.load("main", "events")
        with self.assertRaisesRegex(Exception, "AnimalBear.min"):
            self.service.preview("main", "events", 3, 4, loaded["digest"],
                {"events": {"AnimalBear": {"min": 5}}})
        events.write_text(f"<events>{population_event('AnimalBear').replace('<active>1</active>', '<active>2</active>')}</events>", encoding="utf-8")
        with self.assertRaises(Exception):
            self.service.load("main", "events")

    def test_starter_preview_adopts_marker_once_and_apply_round_trips(self) -> None:
        """Verify starter preview adopts the marker once and apply round-trips."""
        init = self.mission / "init.c"
        original = ('class CustomMission {\n\toverride void StartingEquipSetup(PlayerBase player, bool clothesChosen)\n'
                    '\t{\n\t\tEntityAI itemEnt;\n\t\tCustomCall();\n\t}\n};\n')
        init.write_text(original, encoding="utf-8")
        loaded = self.service.load("main", "starter_loadout")
        self.assertTrue(loaded["adoption_required"])
        preview = self.service.preview("main", "starter_loadout", 3, 4, loaded["digest"],
            {"items": ["WaterBottle", "TaloonBag_Green"]})
        self.assertTrue(preview["adopts_marker_region"])
        self.assertEqual(init.read_text(encoding="utf-8"), original)
        # Apply the adopted loadout and verify the reload round trip
        self.service.apply("main", "starter_loadout", 3, 4, loaded["digest"],
            {"items": ["WaterBottle", "TaloonBag_Green"]}, lambda _phase, _progress: None)
        reloaded = self.service.load("main", "starter_loadout")
        self.assertFalse(reloaded["adoption_required"])
        self.assertEqual(reloaded["values"]["items"], ["WaterBottle", "TaloonBag_Green"])
        self.assertIn("CustomCall();", init.read_text(encoding="utf-8"))

    def test_unmarked_legacy_starter_items_convert_through_digest_bound_operation(self) -> None:
        """Verify unmarked legacy starter items convert through a digest-bound operation."""
        init = self.mission / "init.c"
        init.write_text(
            'class CustomMission {\n\toverride void StartingEquipSetup(PlayerBase player, bool clothesChosen)\n'
            '\t{\n\t\tEntityAI itemEnt;\n'
            '\t\titemEnt = player.GetInventory().CreateInInventory( "WaterBottle" );\n'
            '\t\titemEnt = player.GetInventory().CreateAttachment( "HipPack_Medical" );\n\t}\n};\n',
            encoding="utf-8",
        )
        before = init.read_bytes()
        operations = OperationManager(OperationStore(self.root / "legacy-error-operations"))
        bridge = BridgeFacade(MissionConfigurationCoordinator(self.service, operations).handlers())
        loaded_result = bridge.dispatch(_request("load_mission_configuration",
            {"profile_id": "main", "target": "starter_loadout"}))
        self.assertTrue(loaded_result["success"])
        loaded = loaded_result["value"]
        self.assertTrue(loaded["conversion_required"])
        self.assertEqual(init.read_bytes(), before)
        # Convert the legacy block through the queued bridge operation
        result = bridge.dispatch(_request("convert_starter_loadout", {
            "profile_id": "main", "expected_profile_revision": 3,
            "expected_settings_revision": 4, "expected_digest": loaded["digest"],
        }))
        self.assertTrue(result["success"])
        operation_id = result["value"]["operation_id"]
        deadline = time.monotonic() + 2
        while operations.get(operation_id).state not in {
            OperationState.SUCCEEDED, OperationState.FAILED, OperationState.RECOVERY_REQUIRED,
        } and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(operations.get(operation_id).state, OperationState.SUCCEEDED)
        converted = init.read_text(encoding="utf-8")
        self.assertEqual(converted.count("WaterBottle"), 1)
        self.assertEqual(converted.count("HipPack_Medical"), 1)
        self.assertIn("DayZ-ServerMan starter loadout begin", converted)
        operations.shutdown(2)

    def test_legacy_starter_conversion_rejects_stale_digest(self) -> None:
        """Verify legacy starter conversion rejects a stale digest."""
        init = self.mission / "init.c"
        init.write_text(
            'class CustomMission {\n\toverride void StartingEquipSetup(PlayerBase player, bool clothesChosen)\n'
            '\t{\n\t\tEntityAI itemEnt;\n'
            '\t\titemEnt = player.GetInventory().CreateInInventory("Apple");\n\t}\n};\n',
            encoding="utf-8",
        )
        loaded = self.service.load("main", "starter_loadout")
        # Change the file after load so conversion must refuse
        init.write_text(init.read_text(encoding="utf-8") + "// changed\n", encoding="utf-8")
        with self.assertRaises(ContentChangedError):
            self.service.convert_starter("main", 3, 4, loaded["digest"],
                                         lambda _phase, _progress: None)

    def test_spawnable_preview_is_explicitly_lower_only(self) -> None:
        """Verify spawnable damage preview only lowers values."""
        spawnable = self.mission / "cfgspawnabletypes.xml"
        spawnable.write_text('<spawnabletypes><!-- <damage min=".9" max=".9"/> -->'
            '<type name="A" min="7"><damage min="1e-1" max="8e-1"/></type></spawnabletypes>',
            encoding="utf-8")
        loaded = self.service.load("main", "spawnable_damage")
        preview = self.service.preview("main", "spawnable_damage", 3, 4, loaded["digest"], {"maximum": .2})
        self.assertNotEqual(preview["proposed_digest"], loaded["digest"])
        # Require rejection when the requested cap exceeds the current damage
        for value in (.8, .9):
            with self.subTest(value=value), self.assertRaisesRegex(Exception, "below"):
                self.service.preview("main", "spawnable_damage", 3, 4, loaded["digest"], {"maximum": value})


def economy_xml() -> str:
    """Build a complete economy variables document with a known ZombieMaxCount."""
    values = [f'<var name="{name}" type="0" value="{500 if name == "ZombieMaxCount" else 1}"/>'
              for name in sorted(GLOBAL_INTEGER_KEYS)]
    values += [f'<var name="{name}" type="0" value="1"/>' for name in sorted(GLOBAL_BOOLEAN_KEYS)]
    values += ['<var name="LootDamageMin" type="1" value="0.00"/>',
               '<var name="LootDamageMax" type="1" value="0.20"/>']
    return "<variables>" + "".join(values) + "</variables>\n"


def population_event(name: str) -> str:
    """Build a population event block with fixed active, nominal, and range values."""
    return (f'<event name="{name}"><active>1</active><nominal>3</nominal>'
            '<min>1</min><max>4</max></event>')


def _request(method: str, parameters: dict[str, object]) -> dict[str, object]:
    """Wrap bridge parameters in a versioned request envelope."""
    return {"contract_version": 1, "request_id": f"test-{method}",
            "method": method, "parameters": parameters}


if __name__ == "__main__":
    unittest.main()
