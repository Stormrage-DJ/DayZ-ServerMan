"""Verify cluster placements in the 6.8 candidates (Architect rulings F3, F9 and F11)."""

from __future__ import annotations

import unittest

from dayz_serverman.repositories.mission_map_mapgroup_read import DIRT, POS, PROTO
from tests.mission_map_mapgroup_support import (
    BASELINE, OUTSIDE_SHED, POSITIONS, PROTOTYPE, build, contents, plan, zone,
)

# The cluster file name of every test
CLUSTER_FILE = "mapgroupcluster01.xml"


def kept(result, reason: str) -> list[tuple[str, int, str, int]]:
    """Return the kept placements with one reason as file, index, name and adjustment."""
    return [(item.file, item.index, item.name, item.adjustment) for item in result.kept if item.reason == reason]


def cluster_file(*placements: tuple[str, str]) -> bytes:
    """Return a cluster file with the named placements at the given `pos` text."""
    lines = b"".join(f'    <group name="{name}" pos="{pos}" a="0" />\r\n'.encode() for name, pos in placements)
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\r\n<map>\r\n' + lines + b"</map>\r\n"


class ClusterPlacementTests(unittest.TestCase):
    """A cluster placement of a placed group follows it, or is kept when its vector differs (F9)."""

    CLUSTER = cluster_file(("PearTree1", "500 0 500"), ("land_shed", "1 2 3"), ("Land_House", "4 5 6"),
                           ("Land_Shed", "505 0 505"), ("Land_House", "495 0 495"))

    def test_cluster_placement_in_a_zone_is_kept(self) -> None:
        """Kept with a generated group (shed) and without one (house); the index counts every group element."""
        result = build(plan(0, zone("rich")), clusters={CLUSTER_FILE: self.CLUSTER})
        self.assertFalse(result.refused)
        self.assertEqual(kept(result, "cluster_placement"), [(CLUSTER_FILE, 3, "Land_Shed", 50),
                                                             (CLUSTER_FILE, 4, "Land_House", 50)])
        self.assertEqual(contents(result), contents(build(plan(0, zone("rich")))))

    def test_global_value_alone_keeps_nothing_of_a_placed_group(self) -> None:
        """Cluster placements of placed groups follow them under the global value."""
        result = build(plan(50), clusters={CLUSTER_FILE: self.CLUSTER})
        self.assertFalse(result.refused)
        self.assertEqual([item.reason for item in result.kept], ["planner_content"])
        self.assertEqual(contents(result), contents(build(plan(50))))

    def test_invalid_cluster_position_or_file_refuses(self) -> None:
        """A matching cluster placement without a finite position, or a cluster file that is not XML, refuses."""
        broken = self.CLUSTER.replace(b'pos="4 5 6"', b'pos="nan 5 6"')
        for value in (plan(0, zone("rich")), plan(50)):
            result = build(value, clusters={CLUSTER_FILE: broken})
            self.assertEqual([(item.reason, item.file) for item in result.refusals],
                             [("placement_geometry", CLUSTER_FILE)])
        for data in (b"<map", self.CLUSTER.decode().encode("utf-16")):
            result = build(plan(0, zone("rich")), clusters={"mapgroupcluster02.xml": data})
            self.assertEqual([(item.reason, item.file) for item in result.refusals],
                             [("file_invalid", "mapgroupcluster02.xml")])
        self.assertFalse(build(plan(0), clusters={CLUSTER_FILE: broken}).refused)

    def test_cancelling_zone_keeps_the_cluster_placement_of_a_moved_group(self) -> None:
        """Every placement of the shed sits where global -50 and Rich cancel, a cluster one included (R7)."""
        cluster = b'<map><group name="Land_Shed" pos="505 0 505" a="0"/></map>'
        result = build(plan(-50, zone("rich")), dict(BASELINE, **{POS: POSITIONS.replace(OUTSIDE_SHED, b"")}),
                       clusters={CLUSTER_FILE: cluster})
        self.assertIn((CLUSTER_FILE, 0, "Land_Shed", 0), kept(result, "cluster_placement"))
        self.assertIn(b'<group name="Land_Shed" lootmax="2">', contents(result)[PROTO])
        shed = next(item for item in result.clones if item.source == (PROTO, "Land_Shed"))
        self.assertEqual((shed.group_ceiling, shed.container_ceilings), ("3", ("1", "1", "1")))


class ClusterOnlyGroupTests(unittest.TestCase):
    """A group that only cluster files place takes no change; affected placements are kept (F11 to F13)."""

    CLUSTER = cluster_file(("Land_Unplaced", "505 0 505"), ("Land_Unplaced", "900 0 900"))

    def check(self, value, expected: list[tuple[str, int, str, int]], proto: bytes | None = None) -> None:
        """Build with the cluster file and check the kept placements, the group bytes and the targets."""
        baseline = dict(BASELINE, **({} if proto is None else {PROTO: proto}))
        result = build(value, baseline, clusters={CLUSTER_FILE: self.CLUSTER})
        self.assertFalse(result.refused)
        self.assertEqual(kept(result, "cluster_only_group"), expected)
        files = contents(result)
        self.assertEqual(files[PROTO].count(b'<group name="Land_Unplaced"'), 1)
        start = baseline[PROTO].index(b'<group name="Land_Unplaced"')
        group = baseline[PROTO][start:baseline[PROTO].index(b"</group>", start)]
        self.assertIn(group, files[PROTO])
        # Targets name only groups whose ceilings the candidate writes
        self.assertNotIn((PROTO, "Land_Unplaced"), {target.source for target in result.targets})
        self.assertEqual(files[DIRT], contents(build(value, baseline))[DIRT])

    def test_global_value_alone(self) -> None:
        """Both placements are affected by the global value and kept; the group keeps lootmax 7."""
        self.check(plan(25), [(CLUSTER_FILE, 0, "Land_Unplaced", 25), (CLUSTER_FILE, 1, "Land_Unplaced", 25)])

    def test_zone_alone(self) -> None:
        """Only the placement inside the zone is affected."""
        self.check(plan(0, zone("rich")), [(CLUSTER_FILE, 0, "Land_Unplaced", 50)])

    def test_global_value_with_a_zone(self) -> None:
        """Inside the zone the adjustment adds up; outside it is the global value."""
        self.check(plan(25, zone("rich")), [(CLUSTER_FILE, 0, "Land_Unplaced", 75),
                                            (CLUSTER_FILE, 1, "Land_Unplaced", 25)])

    def test_unresolved_ceiling_does_not_refuse(self) -> None:
        """No ceiling of the group is read, so a ceiling text that is not supported refuses nothing."""
        proto = PROTOTYPE.replace(b'name="Land_Unplaced" lootmax="7"', b'name="Land_Unplaced" lootmax="x"')
        self.check(plan(25, zone("rich")), [(CLUSTER_FILE, 0, "Land_Unplaced", 75),
                                            (CLUSTER_FILE, 1, "Land_Unplaced", 25)], proto)

    def test_targets_equal_the_written_ceilings(self) -> None:
        """Each target with a changed ceiling belongs to a group whose bytes change in the candidate."""
        result = build(plan(25, zone("rich")), clusters={CLUSTER_FILE: self.CLUSTER})
        proto = contents(result)[PROTO]
        for target in result.targets:
            if target.adjustment == 25 and target.changed:
                self.assertIn(f'<group name="{target.source[1]}" lootmax="{target.group.realized}"'.encode(), proto)
        self.assertEqual(sorted({target.source[1] for target in result.targets}),
                         ["Land_City_Hospital", "Land_House", "Land_Shed"])


if __name__ == "__main__":
    unittest.main()
