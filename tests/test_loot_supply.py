"""Cover the loot supply groups, their scaling, and the localized edits of the types and spawnable files."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dayz_serverman.domain.loot_supply import (
    SUPPLY_KEYS, scale_chance, scale_counts, scale_fill, supply_group, validate_percent, LootSupplyError,
)
from dayz_serverman.domain.mission_configuration import MissionValidationError, validate_mission_updates
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.loot_supply import patch_supply, read_supply
from dayz_serverman.repositories.mission_files import load_mission_file, transform_mission_file
from dayz_serverman.repositories.weapon_magazines import patch_magazines, read_magazines


def item(name: str, nominal: int, minimum: int, category: str | None, fill: tuple[int, int] = (-1, -1)) -> str:
    """Return one type block in the server layout with CRLF line breaks."""
    lines = [f'    <type name="{name}">', f"        <nominal>{nominal}</nominal>", "        <lifetime>3600</lifetime>",
             f"        <min>{minimum}</min>", f"        <quantmin>{fill[0]}</quantmin>",
             f"        <quantmax>{fill[1]}</quantmax>"]
    if category is not None:
        lines.append(f'        <category name="{category}"/>')
    lines.append("    </type>")
    return "\r\n".join(lines)


# A types document with every kind of type that the supply reads, a comment and a commented-out type
TYPES = "\r\n".join((
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>', "<types>", "    <!-- keep -->",
    item("AKM", 3, 2, "weapons"), item("KobraOptic", 10, 5, "weapons"), item("Mag_AKM_30Rnd", 5, 2, "weapons", (5, 50)),
    item("Ammo_762x39", 50, 30, "weapons", (10, 40)), item("AmmoBox_762x39_20Rnd", 20, 15, "weapons"),
    item("BandageDressing", 40, 30, "tools"), item("Hammer", 13, 18, "tools"), item("TunaCan", 30, 20, "food"),
    item("Rag", 0, 0, "tools"), item("Wreck_Debris", 4, 2, "lootdispatch"), item("Paper", 7, 3, None),
    '    <!-- <type name="Commented"><nominal>9</nominal><min>9</min><category name="food"/></type> -->',
    "</types>", ""))


class LootSupplyRuleTests(unittest.TestCase):
    """The group of an item type and the scaling rules."""

    def test_groups_follow_category_and_class_name(self) -> None:
        """Prefixes, weapon part names and medical names give the group; dispatch and no category give none."""
        cases = {("AKM", "weapons"): "weapons", ("Crossbow_Black", "weapons"): "weapons",
                 ("MK4Optic_Black", "weapons"): "attachments", ("AK_Suppressor", "weapons"): "attachments",
                 ("UniversalLight", "weapons"): "attachments", ("NVGoggles", "weapons"): "attachments",
                 ("Mag_STANAG_30Rnd", "weapons"): "magazines", ("Ammo_556x45", "weapons"): "ammo",
                 ("AmmoBox_556x45_20Rnd", "weapons"): "ammo_boxes", ("BandageDressing", "tools"): "medical",
                 ("Hammer", "tools"): "tools", ("TunaCan", "food"): "food", ("Jeans_Blue", "clothes"): "clothing",
                 ("TaloonBag_Blue", "containers"): "containers", ("M67Grenade", "explosives"): "explosives",
                 ("Wreck_Debris", "lootdispatch"): None, ("Paper", None): None}
        for (name, category), group in cases.items():
            with self.subTest(name=name):
                self.assertEqual(supply_group(name, category), group)

    def test_counts_round_half_up_and_keep_a_spawning_type_alive(self) -> None:
        """Rounding is half up; a count above 0 stays at least 1 above 0%; 0% gives 0; the order stays."""
        self.assertEqual(scale_counts(3, 2, 150), (5, 3))
        self.assertEqual(scale_counts(1, 1, 5), (1, 1))
        self.assertEqual(scale_counts(10, 5, 0), (0, 0))
        self.assertEqual(scale_counts(13, 18, 200), (26, 36))
        self.assertEqual(scale_fill(10, 40, 300), (30, 100))
        self.assertEqual(scale_fill(60, 90, 200), (100, 100))
        self.assertEqual(scale_fill(-1, -1, 300), (-1, -1))
        self.assertEqual(scale_chance(0.1, 300), 0.3)
        self.assertEqual(scale_chance(0.5, 300), 1.0)

    def test_percentages_are_steps_of_five_from_0_through_300(self) -> None:
        """Values off the steps, above 300, below 0 or not integers are refused."""
        self.assertEqual(validate_percent(300, "ammo"), 300)
        for value in (-5, 302, 305, 2.5, True, "100"):
            with self.subTest(value=value), self.assertRaises(LootSupplyError):
                validate_percent(value, "ammo")
        with self.assertRaisesRegex(MissionValidationError, "steps of 5"):
            validate_mission_updates("loot_supply", {"supply_ammo": 7})
        with self.assertRaises(MissionValidationError):
            validate_mission_updates("loot_supply", {"unknown": 100})
        with self.assertRaisesRegex(MissionValidationError, "0 through 100"):
            validate_mission_updates("economy", {"InitialSpawn": 105})


class LootSupplyFileTests(unittest.TestCase):
    """Localized edits of `db/types.xml`."""

    def test_read_gives_original_percentages_and_group_facts(self) -> None:
        """Without a marker every percentage is 100; types with a nominal of 0 and dispatch loot are not managed."""
        state = read_supply(TYPES)
        self.assertEqual(state.percents, {key: 100 for key in SUPPLY_KEYS})
        self.assertEqual({item.name for item in state.items},
                         {"AKM", "KobraOptic", "Mag_AKM_30Rnd", "Ammo_762x39", "AmmoBox_762x39_20Rnd",
                          "BandageDressing", "Hammer", "TunaCan"})
        facts = state.facts()
        self.assertEqual(facts["groups"]["supply_ammo"], {"types": 1, "nominal": 50})
        self.assertEqual(facts["groups"]["supply_clothing"], {"types": 0, "nominal": 0})
        self.assertEqual(facts["fill_types"], 2)

    def test_changes_scale_from_the_originals_and_100_restores_the_bytes(self) -> None:
        """Two changes do not compound; only managed values change; 100 everywhere gives the original bytes."""
        first = patch_supply(TYPES, {"supply_ammo": 200, "ammo_fill": 150, "supply_weapons": 0})
        second = patch_supply(first, {"supply_ammo": 300})
        self.assertIn("<nominal>150</nominal>", second)
        self.assertIn("<quantmin>15</quantmin>", second)
        self.assertIn("<quantmax>60</quantmax>", second)
        self.assertEqual(read_supply(second).percents["supply_ammo"], 300)
        self.assertIn('<type name="AKM">\r\n        <nominal>0</nominal>', second)
        self.assertIn("<!-- keep -->", second)
        self.assertIn('<type name="Commented"><nominal>9</nominal>', second)
        self.assertIn('<type name="Wreck_Debris">\r\n        <nominal>4</nominal>', second)
        self.assertIn("<types>\r\n    <!-- DayZ-ServerMan loot supply: ", second)
        self.assertEqual(patch_supply(second, {"supply_ammo": 100, "ammo_fill": 100, "supply_weapons": 100}), TYPES)

    def test_hand_edits_count_as_scaled_and_new_types_as_original(self) -> None:
        """An edited value scales from its matching original; a type added by hand scales from its own value."""
        doubled = patch_supply(TYPES, {"supply_food": 200})
        edited = doubled.replace("<nominal>60</nominal>", "<nominal>80</nominal>")
        added = edited.replace("</types>", item("SardinesCan", 10, 5, "food") + "\r\n</types>")
        state = read_supply(added)
        self.assertEqual(state.edited, 1)
        self.assertEqual({item.name: item.original[0] for item in state.items if item.group == "food"},
                         {"TunaCan": 40, "SardinesCan": 10})
        tripled = patch_supply(added, {"supply_food": 300})
        self.assertIn("<nominal>120</nominal>", tripled)
        self.assertIn("<nominal>30</nominal>", tripled)

    def test_repeated_names_stay_and_damaged_markers_refuse(self) -> None:
        """A type name that occurs twice keeps its values; a damaged or repeated marker is refused."""
        repeated = TYPES.replace("</types>", item("TunaCan", 30, 20, "food") + "\r\n</types>")
        self.assertEqual(read_supply(repeated).duplicates, 1)
        self.assertNotIn("<nominal>60</nominal>", patch_supply(repeated, {"supply_food": 200}))
        damaged = TYPES.replace("<types>", '<types><!-- DayZ-ServerMan loot supply: {"version":1} -->')
        marker = '<!-- DayZ-ServerMan loot supply: {"version":1,"percent":{},"original":{}} -->'
        twice = TYPES.replace("<types>", "<types>" + marker + marker)
        for text in (damaged, twice):
            with self.subTest(text=text[:120]), self.assertRaises(ConfigurationFileError):
                read_supply(text)

    def test_mission_file_target_loads_values_and_facts(self) -> None:
        """The mission file layer reads percentages and facts and writes through the supply patcher."""
        with tempfile.TemporaryDirectory(prefix="serverman_supply_") as temporary:
            path = Path(temporary) / "types.xml"
            path.write_bytes(TYPES.encode("utf-8"))
            snapshot = load_mission_file(path, "loot_supply")
            self.assertEqual(snapshot["values"]["supply_medical"], 100)
            self.assertEqual(snapshot["facts"]["groups"]["supply_medical"], {"types": 1, "nominal": 40})
            proposed = transform_mission_file(snapshot, "loot_supply", {"supply_medical": 250}).decode("utf-8")
            self.assertIn("<nominal>100</nominal>", proposed)
            self.assertIn("<min>75</min>", proposed)


# A spawnable document with a magazine block, a mixed block, a commented block and a block without chance
SPAWNABLE = "\n".join((
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>', "<spawnabletypes>",
    '    <type name="AKM">', '        <attachments chance="1.00">', '            <item name="AK_WoodBttstck" chance="1.00" />',
    "        </attachments>", '        <!-- <attachments chance="0.5"><item name="Mag_X" chance="1"/></attachments> -->',
    '        <attachments chance="0.10">', '            <item name="Mag_AKM_30Rnd" chance="1.00" />',
    "        </attachments>", '        <attachments chance="0.40">', '            <item name="Mag_AKM_Drum75Rnd" chance="0.5" />',
    '            <item name="AK_Suppressor" chance="0.5" />', "        </attachments>", "    </type>",
    '    <type name="M4A1">', '        <attachments chance="0.5">', '            <item name="Mag_STANAG_30Rnd" chance="1.00" />',
    "        </attachments>", "    </type>", "</spawnabletypes>", ""))


class WeaponMagazineFileTests(unittest.TestCase):
    """Localized edits of the magazine blocks of `cfgspawnabletypes.xml`."""

    def test_only_magazine_blocks_scale_and_100_restores_the_text(self) -> None:
        """Magazine-only blocks scale up to 1; other and commented blocks stay; 100 gives the original bytes."""
        percent, blocks = read_magazines(SPAWNABLE)
        self.assertEqual((percent, [block.key for block in blocks]), (100, ["AKM#2", "M4A1#1"]))
        tripled = patch_magazines(SPAWNABLE, 300)
        self.assertIn('<attachments chance="0.3">', tripled)
        self.assertIn('<attachments chance="1">', tripled)
        self.assertIn('<attachments chance="0.40">', tripled)
        self.assertIn('<!-- <attachments chance="0.5">', tripled)
        halved = patch_magazines(tripled, 50)
        self.assertIn('<attachments chance="0.05">', halved)
        self.assertIn('<attachments chance="0.25">', halved)
        self.assertEqual(read_magazines(halved)[0], 50)
        self.assertEqual(patch_magazines(halved, 100), SPAWNABLE)

    def test_mission_file_target_reads_and_writes_the_percentage(self) -> None:
        """The mission file layer reads the magazine percentage and writes through the magazine patcher."""
        with tempfile.TemporaryDirectory(prefix="serverman_magazines_") as temporary:
            path = Path(temporary) / "cfgspawnabletypes.xml"
            path.write_bytes(SPAWNABLE.encode("utf-8"))
            snapshot = load_mission_file(path, "weapon_magazines")
            self.assertEqual(snapshot["values"], {"magazine_chance": 100})
            proposed = transform_mission_file(snapshot, "weapon_magazines", {"magazine_chance": 200})
            self.assertIn(b'<attachments chance="0.2">', proposed)
            # The damage cap target reads the same file with the marker in it
            path.write_bytes(proposed.replace(b"<attachments chance=\"1.00\">",
                                              b'<damage min="0.1" max="0.5"/><attachments chance="1.00">'))
            self.assertEqual(load_mission_file(path, "spawnable_damage")["values"], {"maximum": 0.5})


if __name__ == "__main__":
    unittest.main()
