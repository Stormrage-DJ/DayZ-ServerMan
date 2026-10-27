"""Cover the 6.6 areaflags.map tier candidate: covered value cells only, the D9 cell binding and refusals."""

from __future__ import annotations

import hashlib
import random
import unittest

from dayz_serverman.domain.mission_map_areaflags_candidate import build_areaflags_candidate, parse_areaflags
from dayz_serverman.domain.mission_map_evaluation import evaluate_location
from dayz_serverman.domain.mission_map_plan import PlanForm, parse_plan
from dayz_serverman.domain.mission_map_terrain import (
    BOUNDS_DIFFER_FROM_HEADER, BOUNDS_NOT_CONFIRMED, LIMITS_INVALID, TIER_HEADER_INVALID, TIER_NOT_HOLDABLE,
    TerrainCapabilities, TerrainDataError, ValueRows, tiers_of,
)
from tests.test_mission_map_plan import circle, encounter, square, stored_plan
from tests.test_mission_map_terrain import areaflags_bytes


# Eight value flags, so a 4-bit layer holds Tier1 to Tier4 and an 8-bit layer holds all of them
FLAGS = tuple(f"Tier{index}" for index in range(1, 9))


def baseline(bits: int, columns: int = 7, rows: int = 6, seed: int = 66, fill: int | None = None) -> bytes:
    """Return a 1000 m file with asymmetric value cells and a random usage layer that the builder must keep."""
    rng = random.Random(seed)
    cells = {(i, j): fill if fill is not None else rng.randrange(1 << bits) for j in range(rows) for i in range(columns)}
    data = areaflags_bytes(columns, rows, 1000, 1000, 16, bits, cells)
    usage = bytes(rng.randrange(256) for _ in range(columns * rows * 2))
    return data[:20] + usage + data[20 + len(usage):]


def plan_of(objects: list[dict], **terrain) -> dict:
    """Return a validated stored plan on the 1000 m terrain; keyword arguments change its terrain fields."""
    raw = stored_plan(objects)
    raw["terrain"].update(terrain)
    return parse_plan(raw, PlanForm.STORED)


def tiered(item: dict, tier: str | None) -> dict:
    """Return a loot object, or an encounter with enabled linked loot, with the given tier."""
    if item["kind"] == "encounter":
        return {**item, "linked_loot": {**item["linked_loot"], "enabled": True, "tier": tier}}
    return {**item, "tier": tier}


def rows_of(content: bytes) -> ValueRows:
    """Return every value-layer row of exact file bytes, as the evaluation reads them."""
    header = parse_areaflags(content)
    return ValueRows(header, 0, header.rows, content[header.value_offset:])


def assert_matches_evaluation(test: unittest.TestCase, data: bytes, plan: dict) -> None:
    """Compare every candidate cell with the effective tier that the 4.1 evaluation gives over the baseline."""
    candidate = build_areaflags_candidate(data, plan, FLAGS)
    before, after = rows_of(data), rows_of(candidate.content)
    capabilities = TerrainCapabilities("test", before.header, FLAGS)
    grid = before.header.cell_grid()
    for j in range(before.header.rows):
        for i in range(before.header.columns):
            answer = evaluate_location(plan, capabilities, before, *grid.centre(i, j))
            expected = before.value(i, j)
            if answer["tier_source"] == "zone":
                expected = 1 << FLAGS.index(answer["effective_tier"][0])
            test.assertEqual(after.value(i, j), expected, (i, j))
            test.assertEqual(list(tiers_of(after.value(i, j), FLAGS)), answer["effective_tier"])
    # Header, usage layer and value-bit count stay byte-equal; only the value layer can change
    offset = before.header.value_offset
    test.assertEqual(candidate.content[:offset], data[:offset])
    test.assertEqual(len(candidate.content), len(data))


class CellBindingTests(unittest.TestCase):
    """The bits that the builder sets are the bits that the 4.1 evaluation reads at each cell centre."""

    def test_seeded_overlaps_for_four_and_eight_bits(self) -> None:
        """Random circles, squares and linked loot with explicit and inherit tiers, odd and even columns."""
        rng = random.Random(606)
        for bits in (4, 8):
            for columns in (7, 8):
                for case in range(30):
                    objects = []
                    for index in range(rng.randrange(1, 5)):
                        tier = rng.choice((None, "Tier1", "Tier2", "Tier3", "Tier4"))
                        shape = rng.choice((
                            circle(f"c{index}", rng.uniform(0, 1000), rng.uniform(0, 1000), rng.uniform(1, 500)),
                            square(f"p{index}", rng.uniform(0, 700), rng.uniform(0, 700), rng.uniform(50, 300)),
                            {**encounter(f"e{index}"), "centre": {"x": rng.uniform(0, 1000), "z": rng.uniform(0, 1000)},
                             "radius": rng.uniform(10, 400)}))
                        objects.append(tiered(shape, tier))
                    data = baseline(bits, columns, seed=case)
                    with self.subTest(bits=bits, columns=columns, case=case):
                        assert_matches_evaluation(self, data, plan_of(objects))

    def test_odd_column_row_starts_in_a_high_nibble(self) -> None:
        """With C = 7, cell (0, 1) is index 7: the high nibble of value byte 3; its low-nibble partner stays."""
        data = baseline(4, fill=0b1011)
        x, z = rows_of(data).header.cell_grid().centre(0, 1)
        candidate = build_areaflags_candidate(data, plan_of([tiered(circle("c", x, z, 1.0), "Tier1")]), FLAGS)
        offset = rows_of(data).header.value_offset
        changed = [index for index in range(len(data)) if candidate.content[index] != data[index]]
        self.assertEqual(changed, [offset + 3])
        self.assertEqual(candidate.content[offset + 3], 0x1B)

    def test_lowest_z_cell_is_the_first_value_byte(self) -> None:
        """An 8-bit write at the cell of the lowest X and Z changes the first value byte only (row j = 0 first)."""
        data = baseline(8, columns=8, fill=0)
        x, z = rows_of(data).header.cell_grid().centre(0, 0)
        candidate = build_areaflags_candidate(data, plan_of([tiered(circle("c", x, z, 1.0), "Tier8")]), FLAGS)
        offset = rows_of(data).header.value_offset
        self.assertEqual(candidate.content[offset], 128)
        self.assertEqual(candidate.content[offset + 1:], data[offset + 1:])


class CandidateRuleTests(unittest.TestCase):
    """Complete masks, idempotent reapply, removal, unchanged non-value bytes and digests."""

    def test_complete_mask_replaces_baseline_bits(self) -> None:
        """A covered cell gets exactly the tier bit, never an OR with its baseline bits."""
        for bits, fill in ((4, 0b1011), (8, 0b10110101)):
            data = baseline(bits, fill=fill)
            candidate = build_areaflags_candidate(data, plan_of([tiered(square("p", 0, 0, 1000), "Tier1")]), FLAGS)
            values = rows_of(candidate.content)
            with self.subTest(bits=bits):
                self.assertEqual({values.value(i, j) for i in range(7) for j in range(6)}, {1})

    def test_higher_zone_over_full_rows_keeps_its_cells(self) -> None:
        """A higher zone that claims whole rows leaves no cell of those rows to a lower zone."""
        data = baseline(8)
        plan = plan_of([tiered(square("p", 0, 0, 1000), "Tier2"), tiered(circle("c", 500, 500, 400), "Tier7")])
        values = rows_of(build_areaflags_candidate(data, plan, FLAGS).content)
        self.assertEqual({values.value(i, j) for i in range(7) for j in range(6)}, {2})

    def test_reapply_is_idempotent(self) -> None:
        """The same plan gives the same bytes from the baseline, and again from its own candidate."""
        for bits in (4, 8):
            data = baseline(bits)
            plan = plan_of([tiered(circle("c", 400, 600, 300), "Tier2"), tiered(square("p", 100, 100, 500), "Tier3")])
            first = build_areaflags_candidate(data, plan, FLAGS)
            with self.subTest(bits=bits):
                self.assertNotEqual(first.content, data)
                self.assertEqual(build_areaflags_candidate(data, plan, FLAGS), first)
                self.assertEqual(build_areaflags_candidate(first.content, plan, FLAGS), first)

    def test_removal_restores_the_baseline_bits(self) -> None:
        """Removing a zone restores the lower zone or the baseline; removing all zones gives the baseline bytes."""
        for bits in (4, 8):
            data = baseline(bits)
            upper, lower = tiered(circle("c", 500, 500, 300), "Tier2"), tiered(square("p", 0, 0, 600), "Tier4")
            both = build_areaflags_candidate(data, plan_of([upper, lower]), FLAGS)
            with self.subTest(bits=bits):
                # The rebuild starts from the baseline: cells of the removed zone show the lower zone or baseline bits
                after = rows_of(build_areaflags_candidate(data, plan_of([lower]), FLAGS).content)
                before, upper_only = rows_of(data), rows_of(both.content)
                grid = before.header.cell_grid()
                restored = 0
                for j in range(6):
                    for i in range(7):
                        x, z = grid.centre(i, j)
                        in_lower = x <= 600 and z <= 600
                        self.assertEqual(after.value(i, j), 8 if in_lower else before.value(i, j), (i, j))
                        restored += not in_lower and upper_only.value(i, j) != before.value(i, j)
                self.assertGreater(restored, 0)
                # No zone, an inherit zone and disabled linked loot with a tier all write nothing. The 200 m
                # encounter area covers cell centres, so a write of disabled linked loot would change the bytes.
                disabled = {**encounter("e"), "radius": 200.0,
                            "linked_loot": {"enabled": False, "tier": "Tier2", "abundance": "rich", "radius": None}}
                for removed in ([], [circle("c")], [disabled]):
                    self.assertEqual(build_areaflags_candidate(data, plan_of(removed), FLAGS).content, data)

    def test_whole_layer_write_keeps_header_and_usage_layer(self) -> None:
        """A zone over every cell changes every value byte and nothing before the value layer."""
        for bits in (4, 8):
            data = baseline(bits, columns=8, fill=0)
            candidate = build_areaflags_candidate(data, plan_of([tiered(square("p", 0, 0, 1000), "Tier3")]), FLAGS)
            offset = rows_of(data).header.value_offset
            with self.subTest(bits=bits):
                self.assertEqual(candidate.content[:offset], data[:offset])
                self.assertEqual(set(candidate.content[offset:]), {0x44 if bits == 4 else 4})

    def test_digest_is_the_whole_file(self) -> None:
        """Both digests are SHA-256 of the complete candidate bytes (D3)."""
        data = baseline(4)
        candidate = build_areaflags_candidate(data, plan_of([tiered(circle("c"), "Tier1")]), FLAGS)
        expected = hashlib.sha256(candidate.content).hexdigest()
        self.assertEqual((candidate.sha256, candidate.owned_value_sha256), (expected, expected))


class RefusalTests(unittest.TestCase):
    """An unsupported file or plan refuses the complete candidate with a named reason."""

    def reason(self, data: bytes, plan: dict, flags=FLAGS) -> str:
        """Return the reason code of a refused candidate."""
        with self.assertRaises(TerrainDataError) as caught:
            build_areaflags_candidate(data, plan, flags)
        return caught.exception.reason

    def test_named_reasons(self) -> None:
        """Header, bounds, value flags and holdable tiers each give their closed reason code."""
        zone = [tiered(circle("c"), "Tier2")]
        data = baseline(4)
        self.assertEqual(self.reason(data + b"\0", plan_of(zone)), TIER_HEADER_INVALID)
        self.assertEqual(self.reason(areaflags_bytes(7, 6, 1000, 1000, 16, 16), plan_of(zone)), TIER_HEADER_INVALID)
        self.assertEqual(self.reason(data, plan_of(zone, bounds_confirmed=False)), BOUNDS_NOT_CONFIRMED)
        wide = {"x_min": 0.0, "z_min": 0.0, "width": 2000.0, "height": 1000.0}
        self.assertEqual(self.reason(data, plan_of(zone, bounds=wide)), BOUNDS_DIFFER_FROM_HEADER)
        self.assertEqual(self.reason(data, plan_of(zone), ("Tier1", "TIER1")), LIMITS_INVALID)
        # A 4-bit layer holds Tier1 to Tier4 only; one unholdable override refuses the whole candidate
        self.assertEqual(self.reason(data, plan_of(zone + [tiered(square("p"), "Tier5")])), TIER_NOT_HOLDABLE)
        # An 8-bit layer holds Tier5 as value bit 5
        held = build_areaflags_candidate(baseline(8, fill=0), plan_of([tiered(square("p", 0, 0, 200), "Tier5")]), FLAGS)
        self.assertEqual(rows_of(held.content).value(0, 0), 16)

    def test_capacity_only_plan_never_parses_the_file(self) -> None:
        """Without an enabled tier override the baseline returns unchanged, even when it is unsupported."""
        damaged = baseline(4) + b"\0"
        candidate = build_areaflags_candidate(damaged, plan_of([circle("c"), encounter("e")]), FLAGS)
        self.assertEqual(candidate.content, damaged)


if __name__ == "__main__":
    unittest.main()
