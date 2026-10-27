"""Synthetic mapgroup files and plans for the 6.8 capacity candidate tests."""

from __future__ import annotations

import re
from typing import Any

from dayz_serverman.repositories.mission_map_mapgroup_candidate import build_mapgroup_candidates
from dayz_serverman.repositories.mission_map_mapgroup_read import DIRT, POS, PROTO
from tests.mission_map_evaluation_support import fixture
from tests.test_mission_map_plan import circle

# Ordinary groups that no capacity change may touch: a comment, a planner group and an unplaced group
COMMENT = b"    <!-- keep this comment -->\r\n"
PLANNER = (b'    <group name="PlannerRich_00001" lootmax="9">\r\n        <usage name="Town" />\r\n'
           b'        <container name="lootFloor" lootmax="9">\r\n            <category name="tools" />\r\n'
           b'            <point pos="0 0 0" />\r\n        </container>\r\n    </group>\r\n')
UNPLACED = b'    <group name="Land_Unplaced" lootmax="7"><usage name="Town" /></group>\r\n'
# A qualified medical group: Medic usage and a medical marker in its name
HOSPITAL = (b'    <group name="Land_City_Hospital" lootmax="4">\r\n'
            b'        <usage name="Medic" />\r\n        <usage name="Town" />\r\n'
            b'        <container name="lootFloor" lootmax="3">\r\n'
            b'            <category name="weapons" />\r\n            <category name="tools" />\r\n'
            b'            <tag name="floor" />\r\n            <point pos="1 2 3" range="0.4" height="1" />\r\n'
            b'            <point pos="2 2 3" range="0.4" height="1" />\r\n'
            b'        </container>\r\n'
            b'        <container name="lootShelves">\r\n            <category name="clothes" />\r\n'
            b'            <point pos="1 2 3" />\r\n        </container>\r\n'
            b'    </group>\r\n')
# A group with default ceilings, and a group with a fixed dispatch child
HOUSE = (b'    <group name="Land_House">\r\n        <usage name="Town" />\r\n'
         b'        <container name="lootFloor">\r\n            <category name="food" />\r\n'
         b'            <point pos="0 1 0" />\r\n        </container>\r\n    </group>\r\n')
SHED = (b'    <group name="Land_Shed" lootmax="3">\r\n        <usage name="Farm" />\r\n'
        b'        <container name="a" lootmax="1">\r\n            <point pos="0 0 0" />\r\n        </container>\r\n'
        b'        <container name="b" lootmax="1">\r\n            <point pos="0 0 1" />\r\n        </container>\r\n'
        b'        <container name="c" lootmax="1">\r\n            <point pos="0 0 2" />\r\n        </container>\r\n'
        b'        <dispatch name="fixed" />\r\n    </group>\r\n')
DEFAULTS = (b'    <defaults>\r\n        <default name="group" lootmax="6" />\r\n'
            b'        <default name="container" lootmax="4" />\r\n    </defaults>\r\n')
PROTOTYPE = (b"<?xml version='1.0' encoding='utf-8'?>\r\n<prototype>\r\n" + DEFAULTS + COMMENT + HOSPITAL + HOUSE
             + SHED + PLANNER + UNPLACED + b"</prototype>\r\n")
# Placements inside the zone around (500, 500) and outside it; one planner placement inside
INSIDE_HOSPITAL = (b'    <group name="Land_City_Hospital" pos="500.000000 10.5 500.000000" rpy="0 0 90" '
                   b'a="-0.000000" />\r\n')
INSIDE_SHED = b'    <group name="Land_Shed" pos="510.25 3 495.5" rpy="0.000000 0.000000 12.5" a="77.5" />\r\n'
OUTSIDE_SHED = b'    <group name="Land_Shed" pos="900 3 900" rpy="0 0 0" a="0" />\r\n'
OUTSIDE_HOUSE = b'    <group name="Land_House" pos="100 3 100" rpy="0 0 0" a="0" />\r\n'
PLANNER_POS = b'    <group name="PlannerRich_00001" pos="505 3 505" rpy="0 0 0" a="0" />\r\n'
POSITIONS = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\r\n<map>\r\n' + INSIDE_HOSPITAL + INSIDE_SHED
             + OUTSIDE_SHED + OUTSIDE_HOUSE + PLANNER_POS + b"</map>\r\n")
DIRTS = b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\r\n<map>\r\n</map>'
BASELINE = {PROTO: PROTOTYPE, POS: POSITIONS, DIRT: DIRTS}


def plan(global_value: int = 0, *objects: dict[str, Any]) -> dict[str, Any]:
    """Return a validated 1000 m plan with the global value and loot objects."""
    result = fixture(list(objects))[0]
    result["global_abundance"] = global_value
    return result


def zone(abundance: str = "rich", x: float = 500.0, z: float = 500.0, radius: float = 50.0) -> dict[str, Any]:
    """Return one loot circle with an abundance and no tier."""
    item = circle("zone", x, z, radius)
    item["abundance"] = abundance
    return item


def build(plan_value: dict[str, Any], baseline: dict[str, bytes | None] | None = None, **kwargs):
    """Build the candidates over the test baseline, or over the given files."""
    return build_mapgroup_candidates(plan_value, BASELINE if baseline is None else baseline, **kwargs)


def contents(result) -> dict[str, bytes | None]:
    """Return the candidate bytes by relative path."""
    return {item.path: item.content for item in result.files}


def without_ceilings(data: bytes) -> bytes:
    """Return bytes without any lootmax attribute, so ceiling edits compare equal."""
    return re.sub(rb' lootmax="[0-9]*"', b"", data)
