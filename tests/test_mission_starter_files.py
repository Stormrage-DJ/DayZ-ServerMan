"""Cover the starter items of init.c: the layer over the mission's own starting setup."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.mission_files import load_mission_file, transform_mission_file
from dayz_serverman.repositories.mission_starter_layer import (
    CALL_MARKER, METHOD_START, OFF_START, StarterLayoutError,
)

# A mission routine in the style of a community map: random choices, quantities, and fixed items
ROUTINE = (
    '\t\tEntityAI itemTop;\n\t\tEntityAI itemEnt;\n\t\tItemBase itemBs;\n'
    '\t\titemTop = player.FindAttachmentBySlotName("Body");\n'
    '\t\tif ( itemTop )\n\t\t{\n'
    '\t\t\titemEnt = itemTop.GetInventory().CreateInInventory("Rag");\n'
    '\t\t\tif ( Class.CastTo(itemBs, itemEnt ) )\n\t\t\t\titemBs.SetQuantity(4);\n'
    '\t\t\tstring lights[] = { "Chemlight_White", "Chemlight_Red" };\n'
    '\t\t\titemEnt = itemTop.GetInventory().CreateInInventory(lights[Math.RandomInt(0, 2)]);\n'
    '\t\t\tif ( Math.RandomFloat01() < 0.5 )\n'
    '\t\t\t\titemEnt = player.GetInventory().CreateInInventory("Apple");\n'
    '\t\t\telse\n'
    '\t\t\t\titemEnt = player.GetInventory().CreateInInventory("Pear");\n'
    '\t\t\tplayer.SetQuickBarEntityShortcut(itemEnt, 1);\n'
    '\t\t\titemEnt = player.GetInventory().CreateInInventory("Rag");\n'
    '\t\t}\n'
)


def starter_script(body: str) -> str:
    """Wrap a starting-equipment body in a minimal CustomMission class."""
    return ("void Other() {}\nclass CustomMission {\n"
            "\toverride void StartingEquipSetup(PlayerBase player, bool clothesChosen)\n"
            "\t{\n" + body + "\t}\n};\nvoid Tail() {}\n")


class StarterLayerFileTests(unittest.TestCase):
    """The layer adds items and turns the mission's own items off without editing the mission's statements."""

    def setUp(self) -> None:
        """Create a temporary directory for script fixtures."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_starter_files_")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.temp.cleanup()

    def load(self, content: str | bytes, name: str = "init.c") -> dict:
        """Write a fixture verbatim and load it as the starter target."""
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="")
        return load_mission_file(path, "starter_loadout")

    def write(self, snapshot: dict, **updates) -> str:
        """Return the file after the updates."""
        return transform_mission_file(snapshot, "starter_loadout", updates).decode("utf-8")

    def test_the_mission_routine_is_described_and_left_alone(self) -> None:
        """Reading names the routine's items, choices and effects; no layer means the file stays byte for byte."""
        script = starter_script(ROUTINE)
        snapshot = self.load(script)
        self.assertEqual(snapshot["values"], {"items": [], "mission_items": True})
        self.assertEqual(snapshot["mission_starter"], {
            "items": [{"name": "Rag", "count": 2}, {"name": "Apple", "count": 1}, {"name": "Pear", "count": 1}],
            "choices": [["Chemlight_White", "Chemlight_Red"]], "random": True,
            "effects": ["puts items on the quickbar", "sets item quantities"]})
        self.assertEqual(self.write(snapshot, items=[], mission_items=True), script)

    def test_own_items_are_added_after_the_mission_routine(self) -> None:
        """The method goes before the routine, its call is the last statement, all other code stays."""
        script = starter_script(ROUTINE)
        changed = self.write(self.load(script), items=["Apple", "WaterBottle", "TaloonBag_Green"])
        self.assertIn(ROUTINE, changed)
        self.assertIn("void Other() {}", changed)
        self.assertIn("void Tail() {}", changed)
        self.assertLess(changed.index(METHOD_START), changed.index("override void StartingEquipSetup"))
        self.assertTrue(changed.index(CALL_MARKER) > changed.index('CreateInInventory("Rag");\n\t\t}'))
        self.assertIn('CreateInInventory( "Apple" )', changed)
        self.assertIn('CreateAttachment( "TaloonBag_Green" )', changed)
        self.assertIn("SetLiquidType( LIQUID_WATER )", changed)
        reloaded = self.load(changed, "changed.c")
        self.assertEqual(reloaded["values"], {"items": ["Apple", "WaterBottle", "TaloonBag_Green"], "mission_items": True})
        # The description still names only the mission's own items
        self.assertEqual(reloaded["mission_starter"]["items"][0], {"name": "Rag", "count": 2})
        self.assertEqual(self.write(reloaded, items=["Apple", "WaterBottle", "TaloonBag_Green"]), changed)

    def test_the_mission_items_turn_off_and_on_exactly(self) -> None:
        """Off wraps the routine in a block that does not run; on again gives back the original file."""
        script = starter_script(ROUTINE)
        off = self.write(self.load(script), items=["BandageDressing"], mission_items=False)
        self.assertIn(OFF_START, off)
        self.assertIn("if ( false )", off)
        self.assertIn(ROUTINE, off)
        reloaded = self.load(off, "off.c")
        self.assertEqual(reloaded["values"], {"items": ["BandageDressing"], "mission_items": False})
        self.assertEqual(reloaded["mission_starter"]["items"][0], {"name": "Rag", "count": 2})
        self.assertEqual(self.write(reloaded, items=[], mission_items=True), script)
        # Only one of the two values may change
        self.assertNotIn(OFF_START, self.write(reloaded, mission_items=True))

    def test_an_inline_block_of_an_earlier_version_moves_into_the_method(self) -> None:
        """Items of a marked block among the mission's statements become the layer's items."""
        body = ('\t\tEntityAI itemEnt;\n\t\tif ( true )\n\t\t{\n'
                '\t\t\t// DayZ-ServerMan starter loadout begin\n'
                '\t\titemEnt = player.GetInventory().CreateInInventory( "Apple" );\n'
                '\t\titemEnt = player.GetInventory().CreateAttachment( "HipPack_Medical" );\n'
                '\t\t\t// DayZ-ServerMan starter loadout end\n'
                '\t\t\tplayer.SetQuickBarEntityShortcut(itemEnt, 2);\n\t\t}\n')
        snapshot = self.load(starter_script(body))
        self.assertEqual(snapshot["values"], {"items": ["Apple", "HipPack_Medical"], "mission_items": True})
        self.assertEqual(snapshot["mission_starter"]["items"], [])
        moved = self.write(snapshot, items=["Apple", "HipPack_Medical"])
        self.assertNotIn("starter loadout begin", moved)
        self.assertIn("\t\tif ( true )\n\t\t{\n\t\t\tplayer.SetQuickBarEntityShortcut(itemEnt, 2);", moved)
        self.assertEqual(moved.count('CreateAttachment( "HipPack_Medical" )'), 1)

    def test_marker_text_in_strings_and_block_comments_is_no_marker(self) -> None:
        """A marker inside a string or a block comment is left as it is and does not count."""
        body = ('\t\tstring one = "// DayZ-ServerMan starter items begin";\n'
                '\t\t/*\n\t\t// DayZ-ServerMan starter items begin\n\t\t*/\n'
                '\t\tCustomCall();\n')
        snapshot = self.load(starter_script(body))
        self.assertEqual(snapshot["values"]["items"], [])
        changed = self.write(snapshot, items=["Apple"])
        self.assertIn(body, changed)
        self.assertEqual(changed.count("CreateInInventory"), 1)

    def test_damaged_markers_and_unusable_routines_fail_closed(self) -> None:
        """A method without its end, a damaged off block, or no routine refuses; an early return names itself."""
        cases = {
            "open method": starter_script("\t\tEntityAI itemEnt;\n").replace(
                "class CustomMission {\n", "class CustomMission {\n\t" + METHOD_START + "\n"),
            "damaged off": starter_script("\t\t" + OFF_START + "\n\t\tCustomCall();\n"),
            "no routine": "void StartingEquipSetup() {}",
        }
        for name, script in cases.items():
            with self.subTest(name=name), self.assertRaises(ConfigurationFileError):
                self.load(script, f"{name}.c")
        early = self.load(starter_script("\t\tif ( player ) return;\n\t\tCustomCall();\n"), "early.c")
        with self.assertRaisesRegex(StarterLayoutError, "ends early"):
            self.write(early, items=["Apple"])

    def test_cr_only_newlines_are_kept(self) -> None:
        """A file with CR line ends keeps them."""
        script = starter_script("\t\tEntityAI itemEnt;\n\t\tCustomCall();\n").replace("\n", "\r")
        snapshot = self.load(script.encode("utf-8"))
        proposed = transform_mission_file(snapshot, "starter_loadout", {"items": ["Apple"], "mission_items": False})
        self.assertNotIn(b"\n", proposed)
        self.assertIn(b"\r", proposed)
        reloaded = self.load(proposed, "cr-written.c")
        self.assertEqual(reloaded["values"], {"items": ["Apple"], "mission_items": False})


if __name__ == "__main__":
    unittest.main()
