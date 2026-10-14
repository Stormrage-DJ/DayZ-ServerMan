"""Verify 6.8 medical clone parity, the applied-record realization and the D3 owned projections."""

from __future__ import annotations

import hashlib
import unittest

from dayz_serverman.domain.mission_map_plan import canonical_json
from dayz_serverman.repositories.medical_zone_fragments import patch_zones, zones_enabled
from dayz_serverman.repositories.medical_zone_scan import children_of, scan_document, scan_groups
from dayz_serverman.repositories.mission_map_mapgroup_clone import GENERATED_PREFIX
from dayz_serverman.repositories.mission_map_mapgroup_projection import owned_digest, owned_projection
from dayz_serverman.repositories.mission_map_mapgroup_read import DIRT, POS, PROTO
from tests.mission_map_mapgroup_support import (
    BASELINE, HOSPITAL, INSIDE_SHED, OUTSIDE_SHED, POSITIONS, PROTOTYPE, build, contents, plan, zone,
)

# Restricted medical children of the hospital, as the 6.3 rule gives them
ON_SHAPE = ((("name", "Medic"),),), (("lootFloor", ((("name", "tools"),),)),
                                     ("lootShelves", ((("name", "tools"),), (("name", "containers"),))))


def group(data: bytes, name: str):
    """Return the one group with this name."""
    return next(node for node in scan_groups(data) if node.attributes.get("name") == name)


def shapes(result, data: bytes) -> tuple[object, object]:
    """Return the medical shape of the ordinary hospital and of its generated copy in candidate bytes."""
    clone = next(item for item in result.clones if item.source == (PROTO, "Land_City_Hospital"))
    return (children_of(data, group(data, "Land_City_Hospital")).shape(),
            children_of(data, group(data, clone.name)).shape())


class MedicalCloneTests(unittest.TestCase):
    """Generated groups take their medical children from the 6.3 clone rule and match their source."""

    def test_medical_off_before_a_candidate(self) -> None:
        """Without a medical baseline the copy keeps every usage and category of its source."""
        result = build(plan(0, zone("rich")))
        ordinary, clone = shapes(result, contents(result)[PROTO])
        self.assertEqual(ordinary, clone)
        self.assertIn((("name", "Town"),), clone[0])

    def test_medical_on_before_a_candidate(self) -> None:
        """With medical on, the copy has the restricted children; ordinary medical bytes stay as they are."""
        enabled = patch_zones(PROTOTYPE, PROTOTYPE, True)
        result = build(plan(0, zone("rich")), dict(BASELINE, **{PROTO: enabled}), medical_baseline=PROTOTYPE)
        proto = contents(result)[PROTO]
        self.assertEqual(shapes(result, proto), (ON_SHAPE, ON_SHAPE))
        self.assertTrue(proto.startswith(enabled[:-len(b"</prototype>\r\n")]))
        self.assertTrue(zones_enabled(proto, PROTOTYPE))

    def test_medical_toggle_after_a_candidate(self) -> None:
        """The 6.3 patcher changes only ordinary groups; a rebuild in the new state restores parity."""
        off = contents(build(plan(25, zone("rich"))))[PROTO]
        toggled = patch_zones(off, PROTOTYPE, True)
        result = build(plan(25, zone("rich")))
        clone = next(item for item in result.clones if item.source == (PROTO, "Land_City_Hospital"))
        start = off.index(f'<group name="{clone.name}"'.encode())
        # The generated groups and every ceiling keep their bytes under the toggle
        self.assertEqual(toggled[toggled.index(f'<group name="{clone.name}"'.encode()):], off[start:])
        self.assertIn(b'<group name="Land_City_Hospital" lootmax="5">', toggled)
        self.assertEqual(patch_zones(toggled, PROTOTYPE, False), off)
        base_on = patch_zones(PROTOTYPE, PROTOTYPE, True)
        rebuilt = build(plan(25, zone("rich")), dict(BASELINE, **{PROTO: base_on}), medical_baseline=PROTOTYPE)
        self.assertEqual(shapes(rebuilt, contents(rebuilt)[PROTO]), (ON_SHAPE, ON_SHAPE))
        # Turning medical off again and rebuilding gives the first candidate exactly
        base_off = patch_zones(base_on, PROTOTYPE, False)
        self.assertEqual(contents(build(plan(25, zone("rich")), dict(BASELINE, **{PROTO: base_off}),
                                        medical_baseline=PROTOTYPE))[PROTO], off)

    def test_mixed_medical_state_refuses_only_a_qualified_copy(self) -> None:
        """A hospital that differs from both forms refuses its copy; a zone without it is not refused."""
        mixed = PROTOTYPE.replace(b'        <usage name="Town" />\r\n        <container name="lootFloor" lootmax="3">',
                                  b'        <container name="lootFloor" lootmax="3">')
        baseline = dict(BASELINE, **{PROTO: mixed})
        refused = build(plan(0, zone("rich")), baseline, medical_baseline=PROTOTYPE)
        self.assertEqual([item.reason for item in refused.refusals], ["medical_state"])
        self.assertIn("differs from both", refused.refusals[0].message)
        self.assertFalse(build(plan(0, zone("rich", 510.25, 495.5, 5.0)), baseline,
                               medical_baseline=PROTOTYPE).refused)

    def test_copy_that_would_differ_from_its_source_refuses(self) -> None:
        """A medical group with changed unowned categories refuses its copy; an unowned group copies as it is."""
        tent = (b'    <group name="Land_Medic_Tent" lootmax="2">\r\n        <usage name="Medic" />\r\n'
                b'        <container name="c" lootmax="2">\r\n            <category name="tools" />\r\n'
                b'            <point pos="0 0 0" />\r\n        </container>\r\n    </group>\r\n')
        medical = PROTOTYPE.replace(b"</prototype>", tent + b"</prototype>")
        changed = medical.replace(b'<category name="tools" />\r\n            <point pos="0 0 0" />',
                                  b'<category name="tools" /><category name="food" />\r\n'
                                  b'            <point pos="0 0 0" />')
        tent_pos = POSITIONS.replace(OUTSIDE_SHED, OUTSIDE_SHED.replace(b"Land_Shed", b"Land_Medic_Tent"))
        baseline = dict(BASELINE, **{PROTO: changed, POS: tent_pos})
        refused = build(plan(0, zone("rich", 900, 900, 5.0)), baseline, medical_baseline=medical)
        self.assertEqual([(item.reason, item.source) for item in refused.refusals],
                         [("medical_state", "Land_Medic_Tent")])
        # The shed has no medical owner, so its copy follows its current usages
        farm = changed.replace(b'<usage name="Farm" />', b'<usage name="Farm" /><usage name="Town" />')
        result = build(plan(0, zone("rich")), dict(baseline, **{PROTO: farm}), medical_baseline=medical)
        self.assertFalse(result.refused)
        shed = next(item for item in result.clones if item.source == (PROTO, "Land_Shed"))
        self.assertIn(b'<usage name="Town" />', contents(result)[PROTO].split(shed.name.encode())[1][:120])

    def test_source_text_that_a_copy_cannot_keep_refuses(self) -> None:
        """Text inside a source group refuses its copy; a comment there is not copied."""
        tag = b'            <tag name="floor" />'
        commented = PROTOTYPE.replace(tag, b'            <!-- c --><tag name="floor" />')
        result = build(plan(0, zone("rich")), dict(BASELINE, **{PROTO: commented}))
        self.assertFalse(result.refused)
        self.assertEqual(contents(result)[PROTO].count(b"<!-- c -->"), 1)
        text = PROTOTYPE.replace(tag, b'            text<tag name="floor" />')
        self.assertEqual([item.reason for item in build(plan(0, zone("rich")),
                                                        dict(BASELINE, **{PROTO: text})).refusals],
                         ["source_layout"])


class RealizationTests(unittest.TestCase):
    """Each generated group carries its name, D8 full key, source identity and detached placements."""

    def test_name_key_and_detached_placements(self) -> None:
        """The name is the prefix and 12 digits of the canonical key digest; geometry is kept exactly."""
        result = build(plan(0, zone("rich")))
        shed = next(item for item in result.clones if item.source == (PROTO, "Land_Shed"))
        key = [1, "native_capacity", [PROTO, "Land_Shed"], ["a", "b", "c"], "5", ["2", "2", "2"]]
        self.assertEqual(shed.key, key)
        self.assertEqual(shed.name, GENERATED_PREFIX + hashlib.sha256(canonical_json(key)).hexdigest()[:12])
        self.assertEqual(len(shed.name), 27)
        self.assertEqual((shed.containers, shed.group_ceiling, shed.container_ceilings),
                         (("a", "b", "c"), "5", ("2", "2", "2")))
        detached = shed.detached[0]
        self.assertEqual((detached.file, detached.name, detached.position, detached.rotation, detached.angle,
                          detached.multiplicity), (POS, "Land_Shed", (510.25, 3.0, 495.5), (0.0, 0.0, 12.5), 77.5, 1))

    def test_equal_vectors_share_one_generated_group(self) -> None:
        """Placements of one source with one realized vector use one generated group."""
        baseline = dict(BASELINE, **{POS: POSITIONS.replace(OUTSIDE_SHED, INSIDE_SHED.replace(b"510.25", b"505"))})
        result = build(plan(0, zone("rich")), baseline)
        shed = [item for item in result.clones if item.source == (PROTO, "Land_Shed")]
        self.assertEqual(len(shed), 1)
        self.assertEqual(len(shed[0].detached), 2)


class ProjectionTests(unittest.TestCase):
    """The D3 owned projections depend only on path and bytes and ignore unowned content."""

    def test_prototype_projection_follows_owned_values_only(self) -> None:
        """Medical children and comments do not change it; a ceiling or a generated group does."""
        result = build(plan(0, zone("rich")))
        proto = contents(result)[PROTO]
        self.assertEqual(result.files[0].owned_value_sha256, owned_digest(PROTO, proto))
        self.assertEqual(owned_digest(PROTO, patch_zones(proto, PROTOTYPE, True)), owned_digest(PROTO, proto))
        self.assertEqual(owned_digest(PROTO, PROTOTYPE.replace(b"keep this", b"other")), owned_digest(PROTO, PROTOTYPE))
        self.assertNotEqual(owned_digest(PROTO, proto), owned_digest(PROTO, PROTOTYPE))
        self.assertNotEqual(owned_digest(PROTO, contents(build(plan(25)))[PROTO]), owned_digest(PROTO, PROTOTYPE))
        self.assertNotEqual(owned_digest(PROTO, PROTOTYPE.replace(HOSPITAL, HOSPITAL * 2)),
                            owned_digest(PROTO, PROTOTYPE))

    def test_placement_projection_is_a_multiset_of_exact_bindings(self) -> None:
        """Order and number spelling do not count; multiplicity, absence and invalid geometry do."""
        reordered = POSITIONS.replace(INSIDE_SHED, b"").replace(OUTSIDE_SHED, OUTSIDE_SHED + INSIDE_SHED)
        respelled = POSITIONS.replace(b'pos="900 3 900"', b'pos="900.000000 3.0 9e2"').replace(b'a="-0.000000"',
                                                                                             b'a="0"')
        for variant in (reordered, respelled):
            self.assertEqual(owned_digest(POS, variant), owned_digest(POS, POSITIONS))
        self.assertNotEqual(owned_digest(POS, POSITIONS.replace(INSIDE_SHED, INSIDE_SHED * 2)),
                            owned_digest(POS, POSITIONS))
        self.assertIsNone(owned_digest(POS, None))
        self.assertIsNone(owned_digest(POS, POSITIONS.replace(b'pos="900 3 900"', b'pos="inf 3 900"')))
        self.assertIsNone(owned_digest("areaflags.map", POSITIONS))
        self.assertIsNotNone(owned_projection(DIRT, BASELINE[DIRT]))

    def test_document_scan_returns_the_root(self) -> None:
        """The 6.3 scan gives the root element with its byte spans."""
        root = scan_document(PROTOTYPE)
        self.assertEqual((root.tag, root.end), ("prototype", len(PROTOTYPE) - 2))
        self.assertEqual([node.attributes["name"] for node in scan_groups(PROTOTYPE)][:2],
                         ["Land_City_Hospital", "Land_House"])


if __name__ == "__main__":
    unittest.main()
