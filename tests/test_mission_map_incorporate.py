"""The reversal of the applied realization and the incorporation rule of leaf 8.7 (Architect F2, PO 23:42:49)."""

from __future__ import annotations

import unittest

from dayz_serverman.application.mission_map_apply_records import realization
from dayz_serverman.repositories.mission_map_incorporate import (
    OWNED_VALUE_ABSENT_TEXT, OWNED_VALUE_CHANGED, OWNED_VALUE_TEXT, REVERSAL_FAILED, incorporation,
)
from dayz_serverman.repositories.mission_map_infected import MARKER_BEGIN
from dayz_serverman.repositories.mission_map_layout import TargetClass
from dayz_serverman.repositories.mission_map_mapgroup_projection import owned_digest as mapgroup_digest
from dayz_serverman.repositories.mission_map_owned import owned_digest
from dayz_serverman.repositories.mission_map_reverse import ReversalError, reverse
from tests.mission_map_mapgroup_support import (
    BASELINE, DIRTS, HOUSE, INSIDE_SHED, POSITIONS, PROTOTYPE, build, contents, plan, zone,
)
from tests.test_mission_map_infected import BASELINE as TERRITORIES, build as build_territory, encounter

PROTO, POS, DIRT, TERRITORY = "mapgroupproto.xml", "mapgrouppos.xml", "mapgroupdirt.xml", "env/zombie_territories.xml"
# The usage edit of an ordinary group, which the editor does not own
FARM, VILLAGE = b'<usage name="Farm" />', b'<usage name="Village" />'


def applied(plan_value: dict) -> tuple[dict[str, bytes | None], list[dict]]:
    """Return the candidate bytes of a plan over the test baseline and the realization the record stores."""
    result = build(plan_value)
    return contents(result), [realization(clone) for clone in result.clones]


class ReversalTests(unittest.TestCase):
    """Each writable file returns to the working-baseline values; other bytes stay."""

    def test_global_ceilings_go_back_to_their_text_or_absence(self) -> None:
        """Changed ceilings get their baseline text; an added lootmax goes again, so the bytes are the baseline."""
        output, groups = applied(plan(50))
        self.assertNotEqual(output[PROTO], PROTOTYPE)
        self.assertIn(b'<group name="Land_House" lootmax=', output[PROTO])
        self.assertEqual(reverse(PROTO, output[PROTO], PROTOTYPE, groups), PROTOTYPE)
        self.assertEqual(reverse(PROTO, output[PROTO].replace(FARM, VILLAGE), PROTOTYPE, groups),
                         PROTOTYPE.replace(FARM, VILLAGE))

    def test_generated_groups_and_detached_placements_are_reversed(self) -> None:
        """Generated groups go, generated Dirt placements go, and detached placements come back to the Pos file."""
        output, groups = applied(plan(0, zone("rich")))
        self.assertTrue(groups and groups[0]["detached"])
        self.assertEqual(reverse(PROTO, output[PROTO], PROTOTYPE, groups), PROTOTYPE)
        self.assertEqual(reverse(DIRT, output[DIRT], DIRTS, groups), DIRTS)
        restored = reverse(POS, output[POS], POSITIONS, groups)
        self.assertEqual(mapgroup_digest(POS, restored), mapgroup_digest(POS, POSITIONS))
        # Without the realization the detached placements stay missing, and the projection check refuses
        self.assertNotEqual(mapgroup_digest(POS, reverse(POS, output[POS], POSITIONS, [])),
                            mapgroup_digest(POS, POSITIONS))

    def test_repeated_detached_placements_come_back_with_their_multiplicity(self) -> None:
        """Two equal placements of one group detach as one binding with multiplicity 2, and both come back."""
        positions = POSITIONS.replace(INSIDE_SHED, INSIDE_SHED + INSIDE_SHED)
        baseline = dict(BASELINE, **{POS: positions})
        result = build(plan(0, zone("rich")), baseline)
        groups = [realization(clone) for clone in result.clones]
        self.assertIn(2, [item["multiplicity"] for group in groups for item in group["detached"]])
        restored = reverse(POS, contents(result)[POS], positions, groups)
        self.assertEqual(mapgroup_digest(POS, restored), mapgroup_digest(POS, positions))
        self.assertEqual(restored.count(b'name="Land_Shed" pos="510.25'), 2)

    def test_territory_block_and_other_files(self) -> None:
        """The marked block goes with its lines; areaflags.map and absent files stay; damaged markers refuse."""
        output = build_territory(objects=[encounter("e1")]).content
        self.assertEqual(reverse(TERRITORY, output, TERRITORIES, []), TERRITORIES)
        self.assertEqual(reverse(TERRITORY, TERRITORIES, TERRITORIES, []), TERRITORIES)
        self.assertRaises(ReversalError, reverse, TERRITORY, TERRITORIES.replace(b"</territory-type>",
                                                                                MARKER_BEGIN + b"</territory-type>"),
                          TERRITORIES, [])
        self.assertEqual(reverse("areaflags.map", b"flags", b"other", []), b"flags")
        self.assertIsNone(reverse(PROTO, None, PROTOTYPE, []))
        self.assertRaises(ReversalError, reverse, "env/wolf_territories.xml", b"x", b"x", [])
        self.assertRaises(ReversalError, reverse, PROTO, PROTOTYPE, None, [])

    def test_structure_that_cannot_pair_refuses(self) -> None:
        """A group that the baseline lacks, or other containers, cannot be reversed."""
        extra = PROTOTYPE.replace(b"</prototype>", HOUSE + b"</prototype>")
        self.assertRaises(ReversalError, reverse, PROTO, extra, PROTOTYPE, [])
        renamed = PROTOTYPE.replace(b'<container name="lootShelves">', b'<container name="lootOther">')
        self.assertRaises(ReversalError, reverse, PROTO, renamed, PROTOTYPE, [])


class IncorporationRuleTests(unittest.TestCase):
    """Incorporate is available only when no editor-owned value changed and the reversal gives the baseline values."""

    def test_available_owned_change_and_failed_reversal(self) -> None:
        """A usage edit is available; a lootmax edit, a damaged block and a missing realization are refused."""
        output, groups = applied(plan(50))
        owned = owned_digest(TargetClass.MISSION, PROTO, output[PROTO])
        result = incorporation(PROTO, output[PROTO].replace(FARM, VILLAGE), PROTOTYPE, owned, groups)
        self.assertEqual((result.available, result.data), (True, PROTOTYPE.replace(FARM, VILLAGE)))
        self.assertEqual(result.to_dict(), {"available": True, "reason": None, "message": None})
        edited = output[PROTO].replace(b'<group name="Land_Shed" lootmax="', b'<group name="Land_Shed" lootmax="9')
        refused = incorporation(PROTO, edited, PROTOTYPE, owned, groups)
        self.assertEqual((refused.available, refused.reason, refused.message),
                         (False, OWNED_VALUE_CHANGED, OWNED_VALUE_TEXT.format(path=PROTO)))
        territory = build_territory(objects=[encounter("e1")]).content
        damaged = territory.replace(b"mission-map end", b"mission-map stop")
        self.assertEqual(incorporation(TERRITORY, damaged, TERRITORIES, owned_digest(
            TargetClass.MISSION, TERRITORY, territory), []).reason, OWNED_VALUE_CHANGED)
        moved, detached = applied(plan(0, zone("rich")))
        pos_owned = owned_digest(TargetClass.MISSION, POS, moved[POS])
        self.assertEqual(incorporation(POS, moved[POS], POSITIONS, pos_owned, []).reason, REVERSAL_FAILED)
        self.assertTrue(incorporation(POS, moved[POS], POSITIONS, pos_owned, detached).available)
        self.assertEqual(incorporation(PROTO, None, PROTOTYPE, owned, groups).reason, OWNED_VALUE_CHANGED)
        self.assertEqual(incorporation(PROTO, output[PROTO], BASELINE[PROTO].replace(b"Land_Shed", b"Land_X"),
                                       owned, groups).reason, REVERSAL_FAILED)


class AbsentFileTextTests(unittest.TestCase):
    """A deleted file has no copy to save, so its text ends after the replace sentence (PO 07:10:16, evidence 8.8)."""

    def test_a_deleted_file_gets_the_text_without_the_copy_sentence(self) -> None:
        """Both refusal reasons of an absent file give the short text; a present file keeps the full text."""
        self.assertEqual(OWNED_VALUE_ABSENT_TEXT.format(path=DIRT), (
            "The change to mapgroupdirt.xml affects values that the map editor sets, so the map editor cannot keep "
            "this change. Use the map editor to change these values. You can still replace the file."))
        self.assertEqual(OWNED_VALUE_TEXT, OWNED_VALUE_ABSENT_TEXT + " DayZ-ServerMan saves a copy of the current "
                         "file before it replaces the file.")
        output, groups = applied(plan(50))
        owned = owned_digest(TargetClass.MISSION, PROTO, output[PROTO])
        # The owned digest changed (null against the reference), or the reversal of nothing misses the baseline
        for reference, reason in ((owned, OWNED_VALUE_CHANGED), (None, REVERSAL_FAILED)):
            with self.subTest(reason=reason):
                refused = incorporation(PROTO, None, PROTOTYPE, reference, groups)
                self.assertEqual((refused.available, refused.reason, refused.message),
                                 (False, reason, OWNED_VALUE_ABSENT_TEXT.format(path=PROTO)))
        present = incorporation(PROTO, output[PROTO], BASELINE[PROTO].replace(b"Land_Shed", b"Land_X"), owned, groups)
        self.assertEqual((present.reason, present.message), (REVERSAL_FAILED, OWNED_VALUE_TEXT.format(path=PROTO)))

    def test_an_existing_empty_file_keeps_the_copy_sentence(self) -> None:
        """Empty bytes are a file that Replace copies first; only an absent file (None) gets the short text."""
        for path in (PROTO, DIRT, TERRITORY):
            with self.subTest(path=path):
                empty = incorporation(path, b"", BASELINE.get(path, TERRITORIES), "f" * 64, [])
                absent = incorporation(path, None, BASELINE.get(path, TERRITORIES), "f" * 64, [])
                self.assertEqual((empty.reason, empty.message), (OWNED_VALUE_CHANGED, OWNED_VALUE_TEXT.format(path=path)))
                self.assertEqual((absent.reason, absent.message),
                                 (OWNED_VALUE_CHANGED, OWNED_VALUE_ABSENT_TEXT.format(path=path)))

    def test_an_absent_file_over_an_unreadable_baseline(self) -> None:
        """The reversal-error path: a baseline whose owned block cannot be read refuses with the short text."""
        territory = build_territory(objects=[encounter("e1")]).content
        damaged = territory.replace(b"mission-map end", b"mission-map stop")
        absent = incorporation(TERRITORY, None, damaged, None, [])
        self.assertEqual((absent.available, absent.reason, absent.message),
                         (False, REVERSAL_FAILED, OWNED_VALUE_ABSENT_TEXT.format(path=TERRITORY)))
        # The same baseline under a present file without the marker block keeps the copy sentence
        present = incorporation(TERRITORY, TERRITORIES, damaged, owned_digest(TargetClass.MISSION, TERRITORY,
                                                                            TERRITORIES), [])
        self.assertEqual((present.reason, present.message), (REVERSAL_FAILED, OWNED_VALUE_TEXT.format(path=TERRITORY)))


class NoProjectionTests(unittest.TestCase):
    """A placement file without an owned projection (invalid geometry) compares its exact bytes instead."""

    def test_an_edit_of_a_file_without_projection_is_refused(self) -> None:
        """Both files have no projection, so only equal bytes count as the same owned values."""
        invalid = POSITIONS.replace(b"</map>", b'    <group name="Land_House" rpy="0 0 0" a="0" />\r\n</map>')
        self.assertIsNone(owned_digest(TargetClass.MISSION, POS, invalid))
        edited = invalid.replace(b"<map>", b"<map><!-- admin -->")
        self.assertEqual(incorporation(POS, edited, invalid, None, []).reason, REVERSAL_FAILED)
        self.assertTrue(incorporation(POS, invalid, invalid, None, []).available)


if __name__ == "__main__":
    unittest.main()
