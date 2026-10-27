"""Cover the starter loadout target of init.c: marker regions, adoption, and legacy conversion."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.mission_files import (
    convert_starter_file, load_mission_file, transform_mission_file,
)


class StarterLoadoutFileTests(unittest.TestCase):
    """Verify starter loadout patching keeps custom script code and fails closed on unclear markers."""

    def setUp(self) -> None:
        """Create a temporary directory for script fixtures."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_starter_files_")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.temp.cleanup()

    def snapshot(self, name: str, content: str, target: str) -> tuple[Path, dict]:
        """Write a fixture file verbatim and load it for the given target."""
        path = self.root / name
        path.write_text(content, encoding="utf-8", newline="")
        return path, load_mission_file(path, target)

    def test_starter_loadout_requires_marker_region_and_preserves_script(self) -> None:
        """Verify starter patching requires the marker region and preserves the script."""
        script = ('void Other() {}\noverride void StartingEquipSetup(PlayerBase player, bool clothesChosen)\n{\n'
                  '\tEntityAI itemEnt;\n\t// DayZ-ServerMan starter loadout begin\n\t\told();\n'
                  '\t// DayZ-ServerMan starter loadout end\n}\nvoid Tail() {}\n')
        _, snapshot = self.snapshot("init.c", script, "starter_loadout")
        text = transform_mission_file(snapshot, "starter_loadout", {"items": ["Apple", "WaterBottle", "TaloonBag_Green"]}).decode()
        self.assertIn('CreateInInventory( "Apple" )', text)
        self.assertIn('CreateAttachment( "TaloonBag_Green" )', text)
        self.assertIn("SetLiquidType( LIQUID_WATER )", text)
        self.assertIn("SetQuantityMax()", text)
        self.assertIn("void Other() {}", text)
        self.assertIn("void Tail() {}", text)
        # Adopt an unmarked starter block and verify the round trip
        ordinary = ('class CustomMission {\n\toverride void StartingEquipSetup(PlayerBase player, bool clothesChosen)\n'
                    '\t{\n\t\tEntityAI itemEnt;\n\t\tCustomCall();\n\t}\n};\n')
        _, adopt = self.snapshot("ordinary.c", ordinary, "starter_loadout")
        self.assertTrue(adopt["adoption_required"])
        adopted = transform_mission_file(adopt, "starter_loadout", {"items": ["WaterBottle"]}).decode()
        self.assertEqual(adopted.count("DayZ-ServerMan starter loadout begin"), 1)
        self.assertIn("CustomCall();", adopted)
        _, round_trip = self.snapshot("adopted.c", adopted, "starter_loadout")
        self.assertFalse(round_trip["adoption_required"])
        self.assertEqual(round_trip["values"]["items"], ["WaterBottle"])
        self.assertEqual(transform_mission_file(round_trip, "starter_loadout", {"items": ["WaterBottle"]}).decode(), adopted)
        path = self.root / "unsafe.c"
        path.write_text("void StartingEquipSetup() {}", encoding="utf-8")
        # Reject a file without a usable starting-equipment block
        with self.assertRaises(ConfigurationFileError):
            load_mission_file(path, "starter_loadout")

    def test_starter_ignores_marker_decoys_and_preserves_custom_code(self) -> None:
        """Verify decoy markers inside literals and comments are ignored."""
        body = (
            '\t\tEntityAI itemEnt;\n'
            '\t\tstring one = "// DayZ-ServerMan starter loadout begin";\n'
            '\t\t// prefix // DayZ-ServerMan starter loadout end\n'
            '\t\t/*\n\t\t// DayZ-ServerMan starter loadout begin\n'
            '\t\t// DayZ-ServerMan starter loadout end\n\t\t*/\n'
            '\t\tCustomCall();\n'
        )
        # Build a script whose decoys sit inside strings and comments
        script = starter_script(body)
        _, snapshot = self.snapshot("decoys.c", script, "starter_loadout")
        self.assertTrue(snapshot["adoption_required"])
        changed = transform_mission_file(snapshot, "starter_loadout", {"items": ["Apple"]}).decode()
        self.assertIn('string one = "// DayZ-ServerMan starter loadout begin";', changed)
        self.assertIn("CustomCall();", changed)
        self.assertEqual(changed.count("CreateInInventory"), 1)

    def test_starter_real_markers_outside_partial_or_duplicate_fail_closed(self) -> None:
        """Verify misplaced, partial, and duplicate markers fail closed."""
        cases = {
            "outside": START_LINE + "\n" + starter_script("\t\tEntityAI itemEnt;\n") + END_LINE + "\n",
            "partial": starter_script("\t\tEntityAI itemEnt;\n\t\t" + START_LINE + "\n"),
            "duplicate": starter_script("\t\tEntityAI itemEnt;\n\t\t" + START_LINE + "\n\t\t" + START_LINE + "\n\t\t" + END_LINE + "\n"),
        }
        # Probe each malformed marker layout and require rejection
        for name, script in cases.items():
            path = self.root / f"{name}.c"
            path.write_text(script, encoding="utf-8")
            with self.subTest(name=name), self.assertRaises(ConfigurationFileError):
                load_mission_file(path, "starter_loadout")

    def test_unmarked_legacy_water_and_backpack_have_safe_conversion_preview(self) -> None:
        """Verify unmarked legacy items produce a safe conversion preview."""
        legacy = starter_script(
            '\t\tEntityAI itemEnt;\n'
            '\t\titemEnt = player.GetInventory().CreateInInventory( "WaterBottle" );\n'
            '\t\titemEnt = player.GetInventory().CreateAttachment( "HipPack_Medical" );\n'
            '\t\tCustomCall();\n'
        )
        path = self.root / "legacy.c"
        path.write_text(legacy, encoding="utf-8", newline="")
        before = path.read_bytes()
        snapshot = load_mission_file(path, "starter_loadout")
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(snapshot["conversion_required"])
        self.assertEqual(snapshot["values"]["items"], ["WaterBottle", "HipPack_Medical"])
        self.assertEqual(snapshot["conversion"]["items"], ["WaterBottle", "HipPack_Medical"])
        with self.assertRaisesRegex(ConfigurationFileError, "convert the legacy starter"):
            transform_mission_file(snapshot, "starter_loadout", {"items": ["Apple"]})
        # Convert the preview and verify single occurrences of each item
        converted = convert_starter_file(snapshot).decode()
        self.assertEqual(converted.count("WaterBottle"), 1)
        self.assertEqual(converted.count("HipPack_Medical"), 1)
        self.assertEqual(converted.count("DayZ-ServerMan starter loadout begin"), 1)
        # Write the converted script and verify it reloads as adopted
        path.write_text(converted, encoding="utf-8", newline="")
        reloaded = load_mission_file(path, "starter_loadout")
        self.assertFalse(reloaded["conversion_required"])
        self.assertEqual(reloaded["values"]["items"], ["WaterBottle", "HipPack_Medical"])

    def test_legacy_clothing_block_conversion_preserves_scope_and_order(self) -> None:
        """Verify clothing block conversion preserves scope and item order."""
        legacy = starter_script(
            '\t\tEntityAI itemEnt;\n'
            '\t\tEntityAI itemClothing = player.FindAttachmentBySlotName("Body");\n'
            '\t\tif (itemClothing)\n\t\t{\n'
            '\t\t\titemEnt = itemClothing.GetInventory().CreateInInventory("Apple");\n'
            '\t\t\tSetPristine(itemEnt);\n'
            '\t\t\titemEnt = itemClothing.GetInventory().CreateInInventory("BandageDressing");\n'
            '\t\t\tSetPristine(itemEnt);\n\t\t}\n'
            '\t\tCustomTail();\n'
        )
        path, snapshot = self.snapshot("clothing.c", legacy, "starter_loadout")
        self.assertEqual(snapshot["values"]["items"], ["Apple", "BandageDressing"])
        # Convert and verify order, scope, and surrounding text
        converted = convert_starter_file(snapshot).decode()
        self.assertIn('\t\t\t// DayZ-ServerMan starter loadout begin\n', converted)
        self.assertLess(converted.index('CreateInInventory("Apple")'),
                        converted.index('CreateInInventory("BandageDressing")'))
        self.assertIn("\t\t}\n\t\tCustomTail();", converted)
        self.assertEqual(converted.replace('\t\t\t// DayZ-ServerMan starter loadout begin\n', '')
                         .replace('\t\t\t// DayZ-ServerMan starter loadout end\n', ''), legacy)
        self.assertEqual(path.read_text(encoding="utf-8"), legacy)

    def test_legacy_conversion_rejects_interleaved_custom_statements(self) -> None:
        """Verify interleaved custom statements block legacy conversion."""
        legacy = starter_script(
            '\t\tEntityAI itemEnt;\n'
            '\t\titemEnt = player.GetInventory().CreateInInventory("Apple");\n'
            '\t\tCustomCall();\n'
            '\t\titemEnt = player.GetInventory().CreateInInventory("Pear");\n'
        )
        path = self.root / "interleaved.c"
        path.write_text(legacy, encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationFileError, "safe contiguous block"):
            load_mission_file(path, "starter_loadout")

    def test_starter_adoption_preserves_cr_only_newlines(self) -> None:
        """Verify adoption preserves CR-only line endings."""
        script = starter_script("\t\tEntityAI itemEnt;\n\t\tCustomCall();\n").replace("\n", "\r")
        path = self.root / "cr-only.c"
        path.write_bytes(script.encode("utf-8"))
        snapshot = load_mission_file(path, "starter_loadout")
        proposed = transform_mission_file(snapshot, "starter_loadout", {"items": ["Apple"]})
        self.assertNotIn(b"\n", proposed)
        self.assertIn(b"\r", proposed)


# Marker lines that delimit the managed starter loadout region
START_LINE = "// DayZ-ServerMan starter loadout begin"
END_LINE = "// DayZ-ServerMan starter loadout end"


def starter_script(body: str) -> str:
    """Wrap a starting-equipment body in a minimal CustomMission class."""
    return ("class CustomMission {\n"
            "\toverride void StartingEquipSetup(PlayerBase player, bool clothesChosen)\n"
            "\t{\n" + body + "\t}\n};\n")


if __name__ == "__main__":
    unittest.main()
