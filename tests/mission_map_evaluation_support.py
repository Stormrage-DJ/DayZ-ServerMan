"""Small validated-plan and immutable-layer builders for evaluation tests."""

from __future__ import annotations

from typing import Any

from dayz_serverman.application.mission_map_evaluation import ObjectTierRead
from dayz_serverman.domain.mission_map_plan import PlanForm, parse_plan
from dayz_serverman.domain.mission_map_terrain import TerrainCapabilities, ValueRows
from tests.test_mission_map_plan import stored_plan
from tests.test_mission_map_terrain import areaflags_bytes, header_of


FLAGS = tuple(f"Tier{index}" for index in range(1, 9))


def fixture(objects: list[dict[str, Any]], *, bits: int = 4, columns: int = 8, rows: int = 6):
    """Build a 1000 m D1 plan and repeating asymmetric cell values."""
    plan = parse_plan(stored_plan(objects), PlanForm.STORED)
    cells = {(i, j): (j * columns + i) % (1 << bits) for j in range(rows) for i in range(columns)}
    data = areaflags_bytes(columns, rows, 1000, 1000, 16, bits, cells)
    header = header_of(data)
    values = ValueRows(header, 0, rows, data[header.value_offset:])
    return plan, TerrainCapabilities("test", header, FLAGS), values


def finish(plan, capabilities, values, object_id: str, freshness=lambda: True, **kwargs):
    """Drive bounded calls to completion and return the final shape."""
    read = ObjectTierRead(plan, object_id, capabilities, values, {"saved": 3}, freshness, **kwargs)
    for _ in range(10000):
        answer = read.advance()
        if answer["status"] != "running":
            return answer
    raise AssertionError("the bounded test read did not finish")
