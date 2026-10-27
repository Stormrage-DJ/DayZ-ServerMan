"""Synthetic missions and a report helper for the 6.9 loot candidate report tests."""

from __future__ import annotations

from typing import Any

from dayz_serverman.domain.mission_map_areaflags_candidate import build_areaflags_candidate
from dayz_serverman.domain.mission_map_terrain import TerrainCapabilities, TerrainDataError
from dayz_serverman.repositories.mission_map_loot_report import build_loot_report
from dayz_serverman.repositories.mission_map_terrain import AREAFLAGS_FILE
from tests.mission_map_evaluation_support import FLAGS
from tests.mission_map_mapgroup_support import BASELINE, build
from tests.test_mission_map_plan import circle
from tests.test_mission_map_terrain import areaflags_bytes, header_of

# A 4-bit tier map of 8 x 6 cells over the 1000 m test terrain; it holds Tier1 to Tier4
AREAFLAGS = areaflags_bytes(8, 6, 1000, 1000, 16, 4, {(i, j): 1 for j in range(6) for i in range(8)})
CAPABILITIES = TerrainCapabilities("test", header_of(AREAFLAGS), FLAGS)
MISSION = dict(BASELINE, **{AREAFLAGS_FILE: AREAFLAGS})


def loot(identifier: str, abundance: str = "rich", tier: str | None = None, x: float = 500.0, z: float = 500.0,
         radius: float = 50.0) -> dict[str, Any]:
    """Return one loot circle with an identifier, abundance and tier."""
    return dict(circle(identifier, x, z, radius), abundance=abundance, tier=tier)


def report(value: dict[str, Any], baseline: dict[str, bytes | None] | None = None, *, clusters=None,
           maximum: int | None = None, **kwargs) -> dict[str, Any]:
    """Build the 6.6 and 6.8 candidates over the test mission and return their report."""
    files = MISSION if baseline is None else baseline
    try:
        areaflags = build_areaflags_candidate(files[AREAFLAGS_FILE], value, FLAGS)
    except TerrainDataError as error:
        areaflags = error
    mapgroups = build(value, files, clusters=clusters, maximum=maximum)
    return build_loot_report(value, capabilities=CAPABILITIES, baseline=files, areaflags=areaflags,
                             mapgroups=mapgroups, clusters=clusters, maximum=maximum, **kwargs)


def entries(result: dict[str, Any], source: str) -> dict[int, dict[str, Any]]:
    """Return the capacity entries of one source group by adjustment."""
    return {entry["adjustment"]: entry for entry in result["capacity"] if entry["source"] == source}
