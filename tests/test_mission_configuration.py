"""Cover mission file parsing and transformation of the XML targets; starter tests are in their own module."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.domain.mission_configuration import (
    GLOBAL_BOOLEAN_KEYS, GLOBAL_INTEGER_KEYS, GLOBAL_KEYS, LOOT_DAMAGE_KEYS,
    MissionValidationError, validate_mission_updates,
)
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.mission_files import load_mission_file, transform_mission_file


class MissionFileTests(unittest.TestCase):
    """Verify mission file patching preserves content and rejects invalid input."""

    def setUp(self) -> None:
        """Create a temporary directory for mission file fixtures."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_mission_files_")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.temp.cleanup()

    def snapshot(self, name: str, content: str, target: str) -> tuple[Path, dict]:
        """Write a fixture file verbatim and load it for the given target."""
        path = self.root / name
        path.write_text(content, encoding="utf-8", newline="")
        return path, load_mission_file(path, target)

    def test_economy_patches_only_unique_attributes_and_preserves_comments(self) -> None:
        """Verify economy patching updates only known attributes and preserves comments."""
        path, snapshot = self.snapshot("globals.xml", economy_xml(), "economy")
        self.assertEqual(set(snapshot["values"]), GLOBAL_KEYS)
        self.assertEqual(GLOBAL_BOOLEAN_KEYS, {"FoodDecay", "WorldWetTempUpdate"})
        self.assertEqual(GLOBAL_INTEGER_KEYS, {
            "SpawnInitial", "RespawnAttempt", "ZombieMaxCount",
            "CleanupLifetimeDeadInfected", "ZoneSpawnDist", "AnimalMaxCount",
            "CleanupLifetimeDeadAnimal", "CleanupAvoidance", "LootSpawnAvoidance",
            "CleanupLifetimeRuined", "CleanupLifetimeDefault", "CleanupLifetimeDeadPlayer",
            "TimeLogin", "TimeLogout", "TimePenalty", "InitialSpawn", "RespawnTypes", "RespawnLimit", "RestartSpawn",
        })
        # Apply a mixed update across integers, decimals, and booleans
        proposed = transform_mission_file(snapshot, "economy", {
            "ZombieMaxCount": 600, "LootDamageMax": .35,
            "FoodDecay": False, "WorldWetTempUpdate": True,
        })
        text = proposed.decode()
        self.assertIn('value="600"', text)
        self.assertIn('value="0.35"', text)
        self.assertIn('name="FoodDecay" type="0" value="0"', text)
        self.assertIn('name="WorldWetTempUpdate" type="0" value="1"', text)
        self.assertIn('<!-- keep -->', text)
        self.assertIn('<var name="Unknown" type="7" value="9"/>', text)
        self.assertIn("\r\n", text)
        # Write the proposal back and verify a clean reload
        path.write_bytes(proposed)
        self.assertEqual(load_mission_file(path, "economy")["content"], proposed)

    def test_economy_rejects_duplicate_and_malformed_xml(self) -> None:
        """Verify duplicate and malformed economy documents are rejected."""
        duplicate = '<variables><var name="ZombieMaxCount" value="1"/><var name="ZombieMaxCount" value="2"/></variables>'
        with self.assertRaises(ConfigurationFileError):
            self.snapshot("duplicate.xml", duplicate, "economy")
        path = self.root / "bad.xml"
        path.write_text("<variables>", encoding="utf-8")
        with self.assertRaises(ConfigurationFileError):
            load_mission_file(path, "economy")
        # Reject an integer economy key that carries a fractional value
        fractional = self.root / "fractional.xml"
        fractional.write_text(economy_xml().replace('name="TimeLogin" type="0" value="1"',
            'name="TimeLogin" type="0" value="1.5"'), encoding="utf-8")
        with self.assertRaises(ConfigurationFileError):
            load_mission_file(fractional, "economy")

    def test_weather_changes_only_requested_rain_scalars(self) -> None:
        """Verify weather patching changes only the requested rain values."""
        xml = '<weather enable="0" reset="0"><overcast><limits min="0.2" max="0.8"/></overcast><rain><limits min="0.0" max="1.0"/><timelimits min="60" max="120"/><changelimits min="0.0" max="1.0"/><thresholds end="60"/></rain><!--keep--></weather>'
        _, snapshot = self.snapshot("cfgweather.xml", xml, "weather")
        # Update the rain toggle and one time limit
        text = transform_mission_file(snapshot, "weather", {"rain_disabled": True, "rain_time_min": 90}).decode()
        self.assertIn('<weather enable="1" reset="1">', text)
        self.assertIn('<timelimits min="90" max="120"/>', text)
        self.assertIn('<overcast><limits min="0.2" max="0.8"/></overcast>', text)
        self.assertIn("<!--keep-->", text)

    def test_spawnable_cap_never_increases_values_and_preserves_unknown_content(self) -> None:
        """Verify spawnable capping lowers values and preserves unknown content."""
        xml = ('<spawnabletypes><!-- <damage min="0.99" max="0.99"/> -->'
               '<type name="A" min="7" max="9"><damage min="1e-1" max="8E-1"/>'
               '<unknown x="1"/></type></spawnabletypes>')
        _, snapshot = self.snapshot("cfgspawnabletypes.xml", xml, "spawnable_damage")
        text = transform_mission_file(snapshot, "spawnable_damage", {"maximum": .2}).decode()
        self.assertIn('min="1e-1" max="0.2"', text)
        self.assertIn('<type name="A" min="7" max="9">', text)
        self.assertIn('<unknown x="1"/>', text)
        self.assertIn('<!-- <damage min="0.99" max="0.99"/> -->', text)
        # Reload the capped document and require a lower-only cap
        reloaded = self.snapshot("capped.xml", text, "spawnable_damage")[1]
        self.assertEqual(reloaded["values"]["maximum"], .2)
        for maximum in (.2, .3):
            with self.subTest(maximum=maximum), self.assertRaisesRegex(ConfigurationFileError, "below"):
                transform_mission_file(reloaded, "spawnable_damage", {"maximum": maximum})

    def test_spawnable_rejects_missing_malformed_and_out_of_range_damage(self) -> None:
        """Verify missing, malformed, and out-of-range damage values are rejected."""
        # Probe each invalid damage shape and require rejection
        for index, damage in enumerate((
            '<damage min="0.1"/>', '<damage min="bad" max="0.2"/>',
            '<damage min="-0.1" max="0.2"/>', '<damage min="0.1" max="1.01"/>',
        )):
            path = self.root / f"bad-damage-{index}.xml"
            path.write_text(f"<spawnabletypes>{damage}</spawnabletypes>", encoding="utf-8")
            with self.subTest(damage=damage), self.assertRaises(ConfigurationFileError):
                load_mission_file(path, "spawnable_damage")

    def test_events_patch_supported_scalar_locally_and_reject_duplicates(self) -> None:
        """Verify events patch supported scalars and reject duplicate blocks."""
        block = ('<event name="StaticTrain"><active>1</active><nominal>3</nominal><lifetime>2100</lifetime>'
                 '<restock>0</restock><saferadius>1000</saferadius><distanceradius>1000</distanceradius>'
                 '<cleanupradius>1000</cleanupradius><unknown>z</unknown></event>')
        _, snapshot = self.snapshot("events.xml", f'<events><!--keep-->{block}</events>', "events")
        # Patch one scalar and keep unknown siblings intact
        text = transform_mission_file(snapshot, "events", {"events": {"StaticTrain": {"nominal": 7}}}).decode()
        self.assertIn("<nominal>7</nominal>", text)
        self.assertIn("<unknown>z</unknown>", text)
        with self.assertRaises(ConfigurationFileError):
            self.snapshot("duplicate-events.xml", f'<events>{block}{block}</events>', "events")

    def test_validation_rejects_bad_shapes_and_event_boundaries(self) -> None:
        """Verify validation rejects bad shapes and event boundaries."""
        with self.assertRaises(MissionValidationError):
            validate_mission_updates("economy", {"Unknown": 1})
        with self.assertRaises(MissionValidationError):
            validate_mission_updates("events", {"events": {"AnimalBear": {"min": 5, "max": 4}}})
        with self.assertRaises(MissionValidationError):
            validate_mission_updates("events", {"events": {"StaticTrain": {"active": 2}}})
        with self.assertRaises(MissionValidationError):
            validate_mission_updates("starter_loadout", {"items": ["Árvíz_Item"]})
        self.assertEqual(validate_mission_updates("spawnable_damage", {"maximum": .2}), {"maximum": .2})

    def test_namespaced_xml_fails_closed_instead_of_losing_namespace_content(self) -> None:
        """Verify namespaced documents fail closed instead of losing content."""
        path = self.root / "namespaced.xml"
        path.write_text('<variables xmlns="urn:test"><var name="ZombieMaxCount" value="1"/></variables>', encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationFileError, "namespaced"):
            load_mission_file(path, "economy")


def economy_xml() -> str:
    """Build an economy document with CRLF newlines and one unknown variable."""
    values = []
    for name in sorted(GLOBAL_INTEGER_KEYS):
        value = "500" if name == "ZombieMaxCount" else "1"
        values.append(f'<var name="{name}" type="0" value="{value}"/>')
    for name in sorted(GLOBAL_BOOLEAN_KEYS):
        values.append(f'<var name="{name}" type="0" value="1"/>')
    values.extend((
        '<var name="LootDamageMin" type="1" value="0.00"/>',
        '<var name="LootDamageMax" type="1" value="0.20"/>',
        '<var name="Unknown" type="7" value="9"/>',
    ))
    return '<?xml version="1.0"?>\r\n<variables>\r\n<!-- keep -->\r\n' + "\r\n".join(values) + "\r\n</variables>\r\n"


if __name__ == "__main__":
    unittest.main()
