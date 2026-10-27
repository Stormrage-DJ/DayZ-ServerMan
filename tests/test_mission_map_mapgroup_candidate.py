"""Verify the 6.8 mapgroup capacity candidates: routing, byte preservation, reapply, removal and refusals."""

from __future__ import annotations

import unittest
from unittest import mock

from dayz_serverman.repositories import mission_map_mapgroup_routing
from dayz_serverman.repositories.medical_zone_scan import scan_document
from dayz_serverman.repositories.mission_map_mapgroup_read import DIRT, POS, PROTO
from tests.mission_map_mapgroup_support import (
    BASELINE, COMMENT, DEFAULTS, DIRTS, HOUSE, INSIDE_HOSPITAL, INSIDE_SHED, OUTSIDE_HOUSE, OUTSIDE_SHED,
    PLANNER, PLANNER_POS, POSITIONS, PROTOTYPE, SHED, UNPLACED, build, contents, plan, without_ceilings, zone,
)


def reasons(result) -> list[str]:
    """Return the refusal reasons of a result."""
    return [item.reason for item in result.refusals]


class CandidateBytesTests(unittest.TestCase):
    """Only ceilings, generated groups and detached placements change; every other byte stays."""

    def test_neutral_plan_returns_the_baseline_bytes(self) -> None:
        """Global 0 with a Normal zone changes nothing and returns every baseline byte."""
        result = build(plan(0, zone("normal")))
        self.assertFalse(result.refused)
        self.assertEqual(contents(result), BASELINE)
        self.assertEqual([item.changed for item in result.files], [False, False, False])
        self.assertEqual(result.clones, ())

    def test_global_value_changes_only_ordinary_ceilings(self) -> None:
        """Global +50 sets the ceilings of placed groups; planner, unplaced and other bytes stay."""
        result = build(plan(50))
        files = contents(result)
        self.assertEqual(files[POS], POSITIONS)
        self.assertEqual(files[DIRT], DIRTS)
        self.assertEqual(without_ceilings(files[PROTO]), without_ceilings(PROTOTYPE))
        for kept in (DEFAULTS, COMMENT, PLANNER, UNPLACED):
            self.assertIn(kept, files[PROTO])
        # Explicit ceilings change in place; default ceilings get an explicit value
        self.assertIn(b'<group name="Land_Shed" lootmax="5">', files[PROTO])
        self.assertIn(b'<group name="Land_House" lootmax="9">', files[PROTO])
        self.assertIn(b'<container name="lootFloor" lootmax="6">\r\n            <category name="food" />',
                      files[PROTO])
        self.assertIn(b'<container name="lootShelves" lootmax="6">', files[PROTO])
        self.assertEqual([item.name for item in result.kept], ["PlannerRich_00001"])
        self.assertEqual(result.clones, ())

    def test_unchanged_ceilings_keep_their_bytes(self) -> None:
        """A ceiling whose realized value equals its baseline keeps its spelling or its absence."""
        spelled = PROTOTYPE.replace(b'<container name="a" lootmax="1">', b'<container name="a" lootmax="01">')
        files = contents(build(plan(25), dict(BASELINE, **{PROTO: spelled})))
        # Group 3 becomes 4; each container 1 stays 1 at 5/4
        self.assertIn(b'<group name="Land_Shed" lootmax="4">', files[PROTO])
        self.assertIn(b'<container name="a" lootmax="01">', files[PROTO])
        self.assertIn(b'<container name="b" lootmax="1">', files[PROTO])

    def test_zone_detaches_inside_placements_to_generated_groups(self) -> None:
        """A Rich zone moves the inside placements to Dirt; ordinary groups and other placements stay."""
        result = build(plan(0, zone("rich")))
        files = contents(result)
        proto, pos, dirt = files[PROTO], files[POS], files[DIRT]
        # Every original byte stays before the generated groups, which go before the root end tag
        self.assertTrue(proto.startswith(PROTOTYPE[:-len(b"</prototype>\r\n")]))
        self.assertTrue(proto.endswith(b"    </group>\r\n</prototype>\r\n"))
        self.assertEqual(pos, POSITIONS.replace(INSIDE_HOSPITAL, b"").replace(INSIDE_SHED, b""))
        names = [clone.name for clone in result.clones]
        expected = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\r\n<map>\r\n'
                    + INSIDE_HOSPITAL.replace(b"Land_City_Hospital", names[0].encode())
                    + INSIDE_SHED.replace(b"Land_Shed", names[1].encode()) + b"</map>")
        self.assertEqual(dirt, expected)
        shed = next(clone for clone in result.clones if clone.source == (PROTO, "Land_Shed"))
        self.assertEqual((shed.group_ceiling, shed.container_ceilings), ("5", ("2", "2", "2")))
        clone_bytes = proto[proto.index(f'<group name="{shed.name}"'.encode()):]
        for kept in (b'<usage name="Farm" />', b'<dispatch name="fixed" />', b'<point pos="0 0 2" />'):
            self.assertIn(kept, clone_bytes)
        self.assertEqual(len(scan_document(proto).children), len(scan_document(PROTOTYPE).children) + 2)

    def test_reapply_is_identical_and_never_compounds(self) -> None:
        """Equal inputs give equal bytes; a candidate offered as baseline is a D8 conflict, not a base."""
        value = plan(50, zone("very_rich"))
        first, second = build(value), build(value)
        self.assertEqual(contents(first), contents(second))
        self.assertEqual(first.clones, second.clones)
        again = build(value, contents(first))
        self.assertEqual(reasons(again), ["reserved_conflict", "reserved_conflict"])
        self.assertEqual(again.files, ())

    def test_zone_removal_and_global_zero_restore_the_baseline(self) -> None:
        """Removing the zone leaves the global candidate; global 0 then gives the baseline bytes back."""
        global_only = contents(build(plan(25)))
        self.assertEqual(contents(build(plan(25))), global_only)
        self.assertNotEqual(contents(build(plan(25, zone("rich")))), global_only)
        self.assertEqual(contents(build(plan(0))), BASELINE)

    def test_global_and_zone_that_cancel_detach_to_the_baseline_vector(self) -> None:
        """Global -50 with a Rich zone keeps the original vector inside; the ordinary group takes the global one."""
        result = build(plan(-50, zone("rich")))
        files = contents(result)
        self.assertIn(b'<group name="Land_Shed" lootmax="2">', files[PROTO])
        shed = next(clone for clone in result.clones if clone.source == (PROTO, "Land_Shed"))
        self.assertEqual((shed.group_ceiling, shed.container_ceilings), ("3", ("1", "1", "1")))

    def test_identical_placements_keep_their_multiplicity(self) -> None:
        """Two equal bindings move together and the realization counts them."""
        baseline = dict(BASELINE, **{POS: POSITIONS.replace(INSIDE_SHED, INSIDE_SHED * 2)})
        result = build(plan(0, zone("rich")), baseline)
        shed = next(clone for clone in result.clones if clone.source == (PROTO, "Land_Shed"))
        self.assertEqual([item.multiplicity for item in shed.detached], [2])
        self.assertEqual(contents(result)[DIRT].count(shed.name.encode()), 2)

    def test_absent_dirt_file_stays_absent_or_refuses_a_detachment(self) -> None:
        """Without Dirt, a global change succeeds and keeps it absent; a detachment refuses."""
        baseline = dict(BASELINE, **{DIRT: None})
        self.assertIsNone(contents(build(plan(50), baseline))[DIRT])
        self.assertEqual(reasons(build(plan(0, zone("rich")), baseline)), ["dirt_file_absent"])


class CandidateRefusalTests(unittest.TestCase):
    """Ambiguity refuses the complete candidate with its reason; nothing is guessed or left out silently."""

    def test_duplicate_definitions_without_case_refuse_when_affected(self) -> None:
        """Two definitions whose names match without case refuse affected placements."""
        duplicate = HOUSE.replace(b"Land_House", b"LAND_HOUSE")
        baseline = dict(BASELINE, **{PROTO: PROTOTYPE.replace(HOUSE, HOUSE + duplicate)})
        self.assertEqual(reasons(build(plan(25), baseline)), ["source_ambiguous"])
        self.assertEqual(build(plan(25), baseline).refusals[0].placements, ((POS, 3, "Land_House"),))
        # The duplicate placement is outside the zone, so a zone-only plan is not affected by it
        self.assertFalse(build(plan(0, zone("rich")), baseline).refused)
        # A placement spelled in another case is ambiguous between the two definitions as well
        spelled = dict(baseline, **{POS: POSITIONS.replace(OUTSIDE_HOUSE, OUTSIDE_HOUSE.replace(b"Land_House",
                                                                                              b"land_house"))})
        self.assertEqual(reasons(build(plan(0, zone("rich", 100, 100, 5.0)), spelled)), ["source_ambiguous"])
        self.assertEqual(build(plan(25), spelled).refusals[0].placements, ((POS, 3, "land_house"),))

    def test_case_only_match_resolves_to_its_one_group(self) -> None:
        """A placement that matches one group only without case takes its ceilings and detaches like any other."""
        alias_house = OUTSIDE_HOUSE.replace(b"Land_House", b"land_house")
        alias = dict(BASELINE, **{POS: POSITIONS.replace(OUTSIDE_HOUSE, alias_house)})
        global_only = build(plan(25), alias)
        self.assertFalse(global_only.refused)
        self.assertIn(b'<group name="Land_House" lootmax="8">', contents(global_only)[PROTO])
        self.assertEqual(contents(global_only)[POS], alias[POS])
        result = build(plan(0, zone("rich", 100, 100, 5.0)), alias)
        self.assertFalse(result.refused)
        [house] = result.clones
        self.assertEqual((house.source, house.detached[0].name), ((PROTO, "Land_House"), "land_house"))
        files = contents(result)
        self.assertEqual(files[POS], POSITIONS.replace(OUTSIDE_HOUSE, b""))
        self.assertIn(alias_house.replace(b"land_house", house.name.encode()), files[DIRT])
        self.assertIn(f'<group name="{house.name}" lootmax="9">'.encode(), files[PROTO])

    def test_missing_source_and_unresolved_default_refuse_when_affected(self) -> None:
        """A placement without a group and a missing default refuse only a change that needs them."""
        missing = dict(BASELINE, **{POS: POSITIONS.replace(OUTSIDE_HOUSE, OUTSIDE_HOUSE.replace(b"Land_House",
                                                                                              b"Land_Gone"))})
        self.assertEqual(reasons(build(plan(25), missing)), ["source_missing"])
        self.assertFalse(build(plan(0, zone("rich")), missing).refused)
        no_default = dict(BASELINE, **{PROTO: PROTOTYPE.replace(b'lootmax="6" ', b"")})
        refused = build(plan(25), no_default)
        self.assertEqual(reasons(refused), ["default_missing"])
        self.assertEqual(refused.refusals[0].source, "Land_House")

    def test_only_decimal_digit_ceilings_are_supported(self) -> None:
        """A fractional ceiling refuses an affected source; leading zeros are a supported integer."""
        shed = b'"Land_Shed" lootmax="3"'
        fraction = dict(BASELINE, **{PROTO: PROTOTYPE.replace(shed, b'"Land_Shed" lootmax="2.5"')})
        self.assertEqual(reasons(build(plan(50), fraction)), ["ceiling_lexical"])
        # A zone around the house alone does not need the shed ceiling
        self.assertFalse(build(plan(0, zone("rich", 100, 100, 5.0)), fraction).refused)
        zeros = dict(BASELINE, **{PROTO: PROTOTYPE.replace(shed, b'"Land_Shed" lootmax="03"')})
        self.assertIn(b'<group name="Land_Shed" lootmax="5">', contents(build(plan(50), zeros))[PROTO])

    def test_dirt_placement_that_needs_another_vector_refuses(self) -> None:
        """An existing Dirt binding is never moved or renamed; inside a zone it refuses."""
        dirt = DIRTS.replace(b"</map>", INSIDE_SHED + b"</map>")
        result = build(plan(0, zone("rich")), dict(BASELINE, **{DIRT: dirt}))
        self.assertEqual(reasons(result), ["dirt_placement_not_movable"])
        self.assertEqual(result.refusals[0].placements, ((DIRT, 0, "Land_Shed"),))
        self.assertEqual(contents(build(plan(25), dict(BASELINE, **{DIRT: dirt})))[DIRT], dirt)

    def test_reserved_names_in_the_baseline_are_a_d8_conflict(self) -> None:
        """A reserved group or binding in the working baseline refuses that file, also for a neutral plan."""
        reserved = SHED.replace(b"Land_Shed", b"servermanmap_G_0123456789ab")
        result = build(plan(0), dict(BASELINE, **{PROTO: PROTOTYPE.replace(UNPLACED, reserved)}))
        self.assertEqual([(item.reason, item.file) for item in result.refusals], [("reserved_conflict", PROTO)])
        binding = OUTSIDE_SHED.replace(b"Land_Shed", b"ServerManMap_G_0123456789ab")
        result = build(plan(25), dict(BASELINE, **{DIRT: DIRTS.replace(b"</map>", binding + b"</map>")}))
        self.assertEqual([(item.reason, item.file) for item in result.refusals], [("reserved_conflict", DIRT)])

    def test_invalid_geometry_refuses_a_capacity_change(self) -> None:
        """A placement without a finite position refuses a change, but not a neutral plan."""
        broken = dict(BASELINE, **{POS: POSITIONS.replace(b'pos="900 3 900"', b'pos="nan 3 900"')})
        self.assertEqual(reasons(build(plan(0, zone("rich")), broken)), ["placement_geometry"])
        self.assertFalse(build(plan(0), broken).refused)

    def test_truncated_name_collision_refuses_without_a_suffix(self) -> None:
        """Two unequal keys with one name refuse the complete candidate (D8)."""
        with mock.patch.object(mission_map_mapgroup_routing, "generated_name", lambda key: "ServerManMap_G_same"):
            result = build(plan(0, zone("rich")))
        self.assertIn("generated_name_collision", reasons(result))
        self.assertEqual(result.files, ())

    def test_planner_content_stays_and_is_reported(self) -> None:
        """Planner groups and bindings keep every byte; their affected placements are reported as kept."""
        result = build(plan(100, zone("rich")))
        files = contents(result)
        self.assertIn(PLANNER, files[PROTO])
        self.assertIn(PLANNER_POS, files[POS])
        self.assertEqual([(item.name, item.adjustment, item.reason) for item in result.kept],
                         [("PlannerRich_00001", 150, "planner_content")])

    def test_native_limit_refuses_a_value_above_it(self) -> None:
        """A proved native limit refuses a realized ceiling above it; a value equal to it passes."""
        result = build(plan(100), maximum=8)
        # The hospital reaches 8 and passes; the default house ceiling 6 becomes 12
        self.assertEqual([item.source for item in result.refusals], ["Land_House"])
        self.assertEqual(set(reasons(result)), {"ceiling_unsupported"})


if __name__ == "__main__":
    unittest.main()
