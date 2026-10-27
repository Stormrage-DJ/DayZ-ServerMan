"""Verify the 6.8 rules that the QA mutation probe found untested, clusters and placement layouts (F4 to F10)."""

from __future__ import annotations

import unittest
from unittest import mock

from dayz_serverman.repositories import mission_map_mapgroup_candidate
from dayz_serverman.repositories.medical_zone_fragments import patch_zones
from dayz_serverman.repositories.mission_map_mapgroup_projection import owned_digest
from dayz_serverman.repositories.mission_map_mapgroup_read import DIRT, POS, PROTO
from dayz_serverman.repositories.mission_map_mapgroup_splice import LayoutError, apply_edits
from tests.mission_map_mapgroup_support import (
    BASELINE, DEFAULTS, INSIDE_HOSPITAL, INSIDE_SHED, OUTSIDE_HOUSE, OUTSIDE_SHED, PLANNER, PLANNER_POS,
    POSITIONS, PROTOTYPE, build, contents, plan, zone,
)

# A second medical group: Medic and Town usages, so its restricted form differs from its original
TENT = (b'    <group name="Land_Medic_Tent" lootmax="2">\r\n        <usage name="Medic" />\r\n'
        b'        <usage name="Town" />\r\n        <container name="c" lootmax="2">\r\n'
        b'            <category name="tools" />\r\n            <category name="food" />\r\n'
        b'            <point pos="0 0 0" />\r\n        </container>\r\n    </group>\r\n')


def reasons(result) -> list[str]:
    """Return the refusal reasons of a result."""
    return [item.reason for item in result.refusals]


def with_files(**files: bytes | None) -> dict[str, bytes | None]:
    """Return the test baseline with some files replaced, named proto, pos or dirt."""
    names = {"proto": PROTO, "pos": POS, "dirt": DIRT}
    return dict(BASELINE, **{names[key]: value for key, value in files.items()})


class ProjectionIntegrityTests(unittest.TestCase):
    """The owned projections follow every owned value that 8.6 and 8.7 compare (P3, P5)."""

    def test_reserved_projection_follows_ceilings_and_medical_children(self) -> None:
        """A generated group with the same name but another ceiling or other children projects differently."""
        result = build(plan(0, zone("rich")))
        proto = contents(result)[PROTO]
        shed = next(item for item in result.clones if item.source == (PROTO, "Land_Shed"))
        hospital = next(item for item in result.clones if item.source == (PROTO, "Land_City_Hospital"))
        start = f'<group name="{shed.name}" lootmax="5">'.encode()
        self.assertNotEqual(owned_digest(PROTO, proto.replace(start, start.replace(b'"5"', b'"6"'))),
                            owned_digest(PROTO, proto))
        clone = proto[proto.index(f'<group name="{hospital.name}"'.encode()):]
        changed = clone.replace(b'        <usage name="Town" />\r\n', b"", 1)
        self.assertNotEqual(owned_digest(PROTO, proto.replace(clone, changed)), owned_digest(PROTO, proto))

    def test_ordinary_projection_follows_container_ceilings(self) -> None:
        """A changed container ceiling, and an explicit value in place of a default, project differently."""
        base = owned_digest(PROTO, PROTOTYPE)
        raised = PROTOTYPE.replace(b'<container name="a" lootmax="1">', b'<container name="a" lootmax="2">')
        explicit = PROTOTYPE.replace(b'<container name="lootShelves">', b'<container name="lootShelves" lootmax="4">')
        self.assertNotEqual(owned_digest(PROTO, raised), base)
        self.assertNotEqual(owned_digest(PROTO, explicit), base)


class RoutingIntegrityTests(unittest.TestCase):
    """Affected placements, container-only differences, medical state and lexical forms (R1, R3, C6, D1, D3, D4, D6)."""

    def test_global_value_makes_a_neutral_placement_affected(self) -> None:
        """Global -50 with a Rich zone gives adjustment 0, yet the placement's source changes, so it counts."""
        missing = POSITIONS.replace(OUTSIDE_HOUSE, OUTSIDE_HOUSE.replace(b"Land_House", b"Land_Gone"))
        result = build(plan(-50, zone("rich", 100, 100, 5.0)), with_files(pos=missing))
        self.assertEqual(reasons(result), ["source_missing"])
        kept = build(plan(-50, zone("rich")))
        self.assertEqual([(item.name, item.adjustment) for item in kept.kept], [("PlannerRich_00001", 0)])

    def test_vector_that_differs_only_in_a_container_detaches(self) -> None:
        """Group 2 stays 2 at 3/4 while container 3 becomes 2, so the placement moves to its own group."""
        proto = PROTOTYPE.replace(b'"Land_Shed" lootmax="3"', b'"Land_Shed" lootmax="2"').replace(
            b'<container name="a" lootmax="1">', b'<container name="a" lootmax="3">')
        result = build(plan(0, zone("sparse")), with_files(proto=proto))
        shed = next(item for item in result.clones if item.source == (PROTO, "Land_Shed"))
        self.assertEqual((shed.group_ceiling, shed.container_ceilings), ("2", ("2", "1", "1")))

    def test_mixed_medical_state_refuses_a_group_that_matches_its_own_form(self) -> None:
        """The state reads against the medical baseline: one group on and one off refuses any medical copy."""
        medical = PROTOTYPE.replace(b"</prototype>", TENT + b"</prototype>")
        enabled = patch_zones(medical, medical, True)
        mixed = enabled[:enabled.index(b'    <group name="Land_Medic_Tent"')] + TENT + b"</prototype>\r\n"
        tent_pos = POSITIONS.replace(OUTSIDE_SHED, OUTSIDE_SHED.replace(b"Land_Shed", b"Land_Medic_Tent"))
        result = build(plan(0, zone("rich", 900, 900, 5.0)), with_files(proto=mixed, pos=tent_pos),
                       medical_baseline=medical)
        self.assertEqual([(item.reason, item.source) for item in result.refusals],
                         [("medical_state", "Land_Medic_Tent")])

    def test_ceiling_and_default_forms_refuse_when_needed(self) -> None:
        """White space in a ceiling and a repeated default refuse the sources that need them."""
        spaced = PROTOTYPE.replace(b'"Land_Shed" lootmax="3"', b'"Land_Shed" lootmax=" 3"')
        self.assertEqual(reasons(build(plan(50), with_files(proto=spaced))), ["ceiling_lexical"])
        repeated = PROTOTYPE.replace(DEFAULTS, DEFAULTS.replace(
            b'        <default name="container"', b'        <default name="group" lootmax="5" />\r\n'
                                                   b'        <default name="container"'))
        result = build(plan(25), with_files(proto=repeated))
        self.assertEqual(sorted({(item.reason, item.source) for item in result.refusals}),
                         [("default_duplicate", "Land_House")])

    def test_non_finite_rotation_or_angle_refuses(self) -> None:
        """A rotation or angle that is not a finite number refuses a capacity change."""
        for old, new in ((b'rpy="0 0 0" a="0" />\r\n    <group name="Land_House"',
                          b'rpy="nan 0 0" a="0" />\r\n    <group name="Land_House"'),
                         (b'pos="900 3 900" rpy="0 0 0" a="0"', b'pos="900 3 900" rpy="0 0 0" a="inf"')):
            self.assertEqual(reasons(build(plan(0, zone("rich")), with_files(pos=POSITIONS.replace(old, new)))),
                             ["placement_geometry"])

    def test_planner_placement_in_another_case_is_kept(self) -> None:
        """A planner binding spelled in another case is planner content and stays unchanged."""
        lower = POSITIONS.replace(PLANNER_POS, PLANNER_POS.replace(b"PlannerRich_", b"plannerrich_"))
        result = build(plan(100), with_files(pos=lower))
        self.assertEqual([item.name for item in result.kept], ["plannerrich_00001"])
        self.assertIn(PLANNER, contents(result)[PROTO])


class CanonicalFormTests(unittest.TestCase):
    """Lower-weight forms of the QA probe: spelling, reporting, attribute order, escaping and indentation."""

    def test_unchanged_group_ceiling_keeps_its_spelling(self) -> None:
        """Group 1 stays 1 at 5/4 while container 3 becomes 4, so 01 stays; unaffected planners are not kept."""
        spelled = PROTOTYPE.replace(b'<group name="Land_Shed" lootmax="3">', b'<group name="Land_Shed" lootmax="01">'
                                    ).replace(b'<container name="a" lootmax="1">', b'<container name="a" lootmax="3">')
        files = contents(build(plan(25), with_files(proto=spelled)))
        self.assertIn(b'<group name="Land_Shed" lootmax="01">', files[PROTO])
        self.assertIn(b'<container name="a" lootmax="4">', files[PROTO])
        self.assertEqual(build(plan(0, zone("rich", 100, 100, 5.0))).kept, ())

    def test_generated_group_has_canonical_order_escaping_and_file_indentation(self) -> None:
        """name and lootmax come first, special characters stay escaped and the file's tab indentation is used."""
        reordered = b'<group lootmax="3" note="a&amp;b &lt;c&gt;" name="Land_Shed">'
        proto = PROTOTYPE.replace(b'<group name="Land_Shed" lootmax="3">', reordered).replace(b"    ", b"\t")
        result = build(plan(0, zone("rich")), with_files(proto=proto))
        shed = next(item for item in result.clones if item.source == (PROTO, "Land_Shed"))
        self.assertIn(f'\r\n\t<group name="{shed.name}" lootmax="5" note="a&amp;b &lt;c>">\r\n'
                      f'\t\t<usage name="Farm" />'.encode(), contents(result)[PROTO])

    def test_container_without_category_gets_the_medical_pair(self) -> None:
        """With medical on, a points-only container of the source holds the added pair, and so does its copy."""
        points = (b'        <container name="pointsOnly">\r\n            <point pos="4 5 6" />\r\n'
                  b'        </container>\r\n')
        end = b'    </group>\r\n    <group name="Land_House">'
        medical = PROTOTYPE.replace(end, points + end)
        result = build(plan(0, zone("rich")), with_files(proto=patch_zones(medical, medical, True)),
                       medical_baseline=medical)
        hospital = next(item for item in result.clones if item.source == (PROTO, "Land_City_Hospital"))
        clone = contents(result)[PROTO].split(hospital.name.encode())[1]
        self.assertIn(b'<container name="pointsOnly" lootmax="6">\r\n            <category name="tools" />\r\n'
                      b'            <category name="containers" />\r\n            <point pos="4 5 6" />', clone)


class PlacementLayoutTests(unittest.TestCase):
    """A removed placement leaves no blank line, and a copied placement takes the Dirt line endings (F5)."""

    def test_removal_after_a_comment_takes_the_whole_line(self) -> None:
        """The comment stays, and no line with only white space remains."""
        commented = POSITIONS.replace(INSIDE_SHED, b"    <!-- shed -->\r\n" + INSIDE_SHED)
        pos = contents(build(plan(0, zone("rich")), with_files(pos=commented)))[POS]
        self.assertEqual(pos, commented.replace(INSIDE_SHED, b"").replace(INSIDE_HOSPITAL, b""))

    def test_removal_of_a_placement_that_shares_its_line(self) -> None:
        """A placement after another on the same line goes with the white space before it."""
        line = OUTSIDE_SHED[:-2] + b" " + INSIDE_SHED.lstrip(b" ")
        shared = POSITIONS.replace(OUTSIDE_SHED, b"").replace(INSIDE_SHED, line)
        pos = contents(build(plan(0, zone("rich", 510.25, 495.5, 1.0)), with_files(pos=shared)))[POS]
        self.assertEqual(pos, shared.replace(line, OUTSIDE_SHED))
        # Two moved placements on one line join into one removal, which then takes the whole line
        for gap in (b" ", b""):
            line = INSIDE_HOSPITAL[:-2] + gap + INSIDE_SHED.lstrip(b" ")
            both = POSITIONS.replace(INSIDE_HOSPITAL + INSIDE_SHED, line)
            pos = contents(build(plan(0, zone("rich")), with_files(pos=both)))[POS]
            self.assertEqual(pos, POSITIONS.replace(INSIDE_HOSPITAL, b"").replace(INSIDE_SHED, b""), gap)

    def test_multi_line_placement_takes_the_dirt_line_endings(self) -> None:
        """A placement that spans lines in an LF mapgrouppos.xml enters a CRLF Dirt file with CRLF only."""
        spread = INSIDE_SHED.replace(b' rpy="', b'\r\n        rpy="')
        pos = POSITIONS.replace(INSIDE_SHED, spread).replace(b"\r\n", b"\n")
        dirt = contents(build(plan(0, zone("rich")), with_files(pos=pos)))[DIRT]
        self.assertEqual(dirt.count(b"\n"), dirt.count(b"\r\n"))
        self.assertIn(b'pos="510.25 3 495.5"\r\n        rpy="0.000000 0.000000 12.5"', dirt)


    def test_whole_line_removal_before_a_shared_line_keeps_the_other_content(self) -> None:
        """A placement alone on its line, then one that shares its line: unrelated bytes stay exactly (F8)."""
        head = b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\r\n<map>\r\n'
        hospital, shed = INSIDE_HOSPITAL.rstrip(b"\r\n"), INSIDE_SHED.rstrip(b"\r\n")
        for tail in (b'<note text="keep me"/>', b"<!-- keep me -->", b" <!-- keep me -->"):
            pos = head + hospital + b"\r\n" + shed + tail + b"\r\n</map>\r\n"
            result = build(plan(0, zone("rich")), with_files(pos=pos))
            self.assertFalse(result.refused, tail)
            self.assertEqual(contents(result)[POS], head + b"    " + tail.lstrip(b" ") + b"\r\n</map>\r\n")
            self.assertEqual(contents(result)[DIRT].count(b"ServerManMap_G_"), 2)

    def test_overlap_in_the_candidate_is_a_file_layout_refusal(self) -> None:
        """If removal spans ever overlap, the candidate refuses with file_layout and writes nothing (Q8)."""
        with mock.patch.object(mission_map_mapgroup_candidate, "merge_removals", lambda data, parent, edits: edits * 2):
            result = build(plan(0, zone("rich")))
        self.assertEqual([(item.reason, item.file) for item in result.refusals], [("file_layout", POS)])
        self.assertEqual(result.files, ())

    def test_overlapping_edits_refuse(self) -> None:
        """Edits that overlap raise; touching edits apply."""
        with self.assertRaises(LayoutError):
            apply_edits(b"abcdef", [(0, 3, b""), (2, 4, b"x")])
        self.assertEqual(apply_edits(b"abcdef", [(0, 2, b""), (2, 4, b"x")]), b"xef")


if __name__ == "__main__":
    unittest.main()
