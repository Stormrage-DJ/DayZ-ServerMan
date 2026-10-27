"""Leaf 10.4, PO-6: the Python copies of the window texts of `map show` and `map inspect` equal the window files.

The CLI lines (a text that names a command) are exempt. Since leaf 10.16 the window has the adoption texts
ADOPT-4 to ADOPT-6, the headings ADOPT-10, ADOPT-11, ADOPT-14 and ADOPT-17, and the list label of ADOPT-15. Tests
pin them against the text of the Product Owner (evidence 10.1) and against the window files.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.cli import map_inspect_wording as inspect_words  # noqa: E402
from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli import map_adopt_render as adopt_render  # noqa: E402
from dayz_serverman.cli.map_inspect_render import inspect_blocks  # noqa: E402
from dayz_serverman.cli.output import line_text  # noqa: E402
from dayz_serverman.domain.mission_map_loot_report_texts import KEPT_TEXTS  # noqa: E402
from dayz_serverman.domain.mission_map_providers import NO_VERIFIED_HEIGHT_REASON, WOLF_BEAR_REASON  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
STRING = r'"((?:[^"\\]|\\.)*)"'


def source(name: str) -> str:
    """Return one window file."""
    return (FRONTEND / name).read_text(encoding="utf-8")


def table(text: str, name: str) -> dict[str, str]:
    """Return the string pairs of `const name = Object.freeze({...})`, in one level."""
    body = text.split(f"const {name} = Object.freeze({{", 1)[1].split("\n});", 1)[0]
    return {key: json.loads(f'"{value}"') for key, value in re.findall(rf"(\w+):\s*{STRING}", body)}


def literal(text: str, key: str) -> str:
    """Return the string literal of one `key: "..."` entry of the public wording object."""
    found = re.findall(rf"\b{key}:\s*{STRING}", text)
    if len(found) != 1:
        raise AssertionError(f"{key}: expected one literal, found {len(found)}")
    return json.loads(f'"{found[0]}"')


class WindowCopyTests(unittest.TestCase):
    """Every copied text is the same string in the window file that the table names."""

    @classmethod
    def setUpClass(cls) -> None:
        """Read the three window files."""
        cls.editor = source("map_editor_wording.js")
        cls.encounters = source("map_editor_encounter_wording.js")
        cls.inspect = source("map_editor_inspect_wording.js")
        cls.preview = source("map_editor_preview_words.js")

    def test_show_labels_and_states(self) -> None:
        """Terrain, bounds, objects, presets and the plan state of another association."""
        self.assertEqual(literal(self.editor, "unknownTerrain"), words.UNKNOWN_TERRAIN)
        self.assertEqual(literal(self.editor, "boundsConfirmed"), words.BOUNDS_TEXTS[True])
        self.assertEqual(literal(self.editor, "boundsUnconfirmed"), words.BOUNDS_TEXTS[False])
        self.assertEqual(literal(self.editor, "objectList"), words.OBJECTS_HEADING)
        self.assertEqual(literal(self.editor, "noObjects"), words.NO_OBJECTS)
        self.assertEqual(literal(self.editor, "inherit"), words.INHERIT)
        self.assertEqual(literal(self.editor, "unavailable"), words.NOT_AVAILABLE)
        self.assertEqual(table(self.editor, "mapPlanDetails")["OTHER_ASSOCIATION"],
                         words.PLAN_STATES["OTHER_ASSOCIATION"])
        for window, copy in (("objectTypes", words.OBJECT_KINDS), ("capacityPresets", words.ZONE_PRESETS)):
            with self.subTest(table=window):
                found = re.search(rf"{window}: Object\.freeze\(\{{([^}}]*)\}}\)", self.editor)
                self.assertEqual(dict(re.findall(rf"(\w+):\s*{STRING}", found.group(1))), copy)

    def test_read_only_texts(self) -> None:
        """The window texts of the two reasons that stay as they are, and the fallback."""
        window = table(self.editor, "mapReadOnlyReasons")
        for reason in ("invalid_saved_data", "association_mismatch", "mission_path_changed",
                       "returned_to_earlier_path", "adoption_required"):
            with self.subTest(reason=reason):
                self.assertEqual(window[reason], words.READ_ONLY_TEXTS[reason])
        self.assertIn(f'|| "{words.READ_ONLY_FALLBACK}"', self.editor)

    def test_adoption_texts_are_the_product_owner_texts(self) -> None:
        """ADOPT-4, ADOPT-5 and ADOPT-6 of evidence 10.1 (Product Owner 14:26:38 and 14:35:24), word for word."""
        self.assertEqual(words.READ_ONLY_TEXTS["mission_path_changed"],
                         "Read only: the mission path changed. The map plan shown belongs to an earlier path. Adopt "
                         "the mission to use its map editor records for the new path.")
        self.assertEqual(words.READ_ONLY_TEXTS["returned_to_earlier_path"],
                         "Read only: this mission returned to an earlier path. Adopt the mission to use its map "
                         "editor records again.")
        self.assertEqual(words.READ_ONLY_TEXTS["adoption_required"],
                         "Apply is not possible: a restore removed the last Apply record of this profile for this "
                         "mission. Adopt the mission to continue.")

    def test_adoption_headings_and_label_are_in_the_window(self) -> None:
        """ADOPT-10, ADOPT-11, ADOPT-14, ADOPT-17 and the ADOPT-15 list label equal the Python texts of `map adopt`."""
        window = {key: json.loads(f'"{value}"') for key, value in re.findall(rf"(adopt\w+):\s*{STRING}", self.preview)}
        self.assertEqual(window["adoptRefusals"], adopt_render.REFUSALS_HEADING)
        self.assertEqual(window["adoptHappened"], adopt_render.HAPPENED_HEADING)
        self.assertEqual(window["adoptWrites"], adopt_render.WRITES_HEADING)
        self.assertEqual(window["adoptUnchanged"], adopt_render.UNCHANGED_HEADING)
        self.assertEqual(window["adoptRecords"], adopt_render.RECORDS_LABEL)

    def test_tier_reasons(self) -> None:
        """Original tiers and Detecting bounds print the reasons of the tier layer."""
        self.assertEqual(table(self.editor, "mapTierReasons"), words.TIER_REASONS)

    def test_provider_texts(self) -> None:
        """Provider names, the infected reasons, the bandit text and the wolf and bear text."""
        found = re.search(r"mapEncounterWordsNames = Object\.freeze\(\{([^}]*)\}\)", self.encounters)
        self.assertEqual(dict(re.findall(rf"(\w+):\s*{STRING}", found.group(1))), words.PROVIDER_NAMES)
        files = re.search(rf"mapEncounterWordsFiles = {STRING};", self.encounters).group(1)
        self.assertEqual(files, words.INFECTED_FILES)
        infected = table(self.encounters, "mapEncounterWordsInfected")
        templated = dict(re.findall(r"(\w+): `([^`]*)`", self.encounters))
        window = {**infected, **{key: json.loads(f'"{value}"').replace("${mapEncounterWordsFiles}", files)
                                 for key, value in templated.items() if key.startswith("provider_")}}
        self.assertEqual(window, words.INFECTED_REASONS)
        self.assertIn(f'"{words.INFECTED_PREFIX}" +', self.encounters)
        self.assertIn("the check gave the unknown result ${code}.", self.encounters)
        self.assertEqual(literal(self.encounters, "wolfBearReason"), WOLF_BEAR_REASON)
        self.assertEqual(literal(self.encounters, "banditReason"), NO_VERIFIED_HEIGHT_REASON)

    def test_capacity_reasons_and_sentences(self) -> None:
        """The twelve reasons of a building group without capacity and the fixed sentences of the answers."""
        self.assertEqual(table(self.inspect, "mapInspectCapacityReasons"), inspect_words.CAPACITY_REASONS)
        self.assertIn(f'?? "{inspect_words.CAPACITY_REASON_FALLBACK}"', self.inspect)
        self.assertEqual(literal(self.inspect, "plannerContent"), KEPT_TEXTS["planner_content"])
        self.assertEqual(literal(self.inspect, "capacityOnly"), inspect_words.CAPACITY_ONLY)
        self.assertEqual(literal(self.inspect, "capacityNone"), inspect_words.CAPACITY_NONE)
        self.assertEqual(literal(self.inspect, "noZone"), inspect_words.NO_ZONE)
        self.assertEqual(literal(self.inspect, "none"), inspect_words.NONE_TEXT)
        self.assertEqual(literal(self.inspect, "unknown"), inspect_words.UNKNOWN_TEXT)
        self.assertEqual(literal(self.inspect, "pointStale"), inspect_words.STALE_RESTART)
        self.assertEqual(literal(self.inspect, "pointReading"), inspect_words.READING)
        self.assertEqual(literal(self.inspect, "capacityTitle"), inspect_words.CAPACITY_LABEL)
        self.assertIn(f'return "{inspect_words.WRITES_CAUSE}"', self.inspect)
        self.assertIn(f'"{inspect_words.READ_STOPPED}"', self.inspect)

    def test_sentence_templates(self) -> None:
        """Each template of the window function is in the window file, and the Python function builds the same text."""
        for template in (
                "Group capacity: ${group.baseline} now, ${group.realized} with this plan.",
                "${item.name} ${item.baseline} to ${item.realized}", "Containers: ${shown}",
                ", and ${containers.length - 4} more.",
                "The spawn points of ${item.name} allow ${item.point_bound} at most.",
                "The spawn points allow ${capacity.achievable} at most.",
                "Apply writes them to group ${capacity.generated_group}.", "The capacity stays unchanged.",
                "${entry.source ?? \"Buildings\"}: ${count} ${count === 1 ? \"building\" : \"buildings\"}"
                "${adjustment}.", " at ${mapInspectSigned(entry.adjustment)}",
                "Their group has more than one container named ${entry.field}, so their capacity cannot be changed.",
                "${value > 0 ? \"+\" : \"\"}${value}%", "${tiers} from ${name}", "${tiers} (original)",
                "Column ${cell.i}, row ${cell.j}", "Outside the tier map",
                "${name} sets ${tier}, but tiers cannot be written in this mission.",
                "Global ${global}, ${zone}: ${effective}", "zone ${label}", "${hidden ? \" (hidden on the map)\" : \"\"}"):
            with self.subTest(template=template):
                self.assertIn(template, self.inspect)
        self.assertIn("(hidden on the map)", self.inspect)
        self.assertEqual(inspect_words.OUTSIDE_CELL, "Outside the tier map")

    def test_the_python_builders_follow_the_window_order(self) -> None:
        """A group with capacity: head, group capacity, containers, point limits, bound, bound text, generated group."""
        group = {"baseline": 10, "realized": 15}
        containers = [{"name": "Box", "baseline": 4, "realized": 6, "point_limited": True, "point_bound": 5},
                      {"name": "Bag", "baseline": 2, "realized": 2}]
        entry = {"source": "Land_A", "adjustment": 50, "placements": 3, "kept": None, "reason": None, "capacity": {
            "group": group, "containers": containers, "achievable": 12, "bound_text": "Upper bound.",
            "generated_group": "ServerManMap_G_1"}}
        self.assertEqual(inspect_words.capacity_entry_text(entry),
                         "Land_A: 3 buildings at +50%. Group capacity: 10 now, 15 with this plan. "
                         "Containers: Box 4 to 6, Bag 2. The spawn points of Box allow 5 at most. "
                         "The spawn points allow 12 at most. Upper bound. Apply writes them to group ServerManMap_G_1.")
        many = [{"name": f"C{index}", "baseline": 1, "realized": 1} for index in range(6)]
        self.assertEqual(inspect_words.containers_text(many), "Containers: C0 1, C1 1, C2 1, C3 1, and 2 more.")
        kept = {"source": None, "adjustment": None, "placements": 1, "kept": "planner_content", "reason": None,
                "capacity": None}
        self.assertEqual(inspect_words.capacity_entry_text(kept), f"Buildings: 1 building. {KEPT_TEXTS['planner_content']}")
        self.assertEqual(inspect_words.capacity_entry_text({**kept, "kept": "other"}),
                         "Buildings: 1 building. The capacity stays unchanged.")
        lacking = {**kept, "kept": None, "adjustment": -25, "reason": "container_ambiguous", "field": "Cargo"}
        self.assertEqual(inspect_words.capacity_entry_text(lacking), "Buildings: 1 building at -25%. Their group has "
                         "more than one container named Cargo, so their capacity cannot be changed.")
        self.assertEqual(inspect_words.capacity_entry_text({**lacking, "reason": "unheard", "field": None}),
                         "Buildings: 1 building at -25%. The capacity of these buildings cannot be shown.")
        self.assertEqual([inspect_words.signed(value) for value in (50, 0, -25)], ["+50%", "0%", "-25%"])
        self.assertEqual(inspect_words.tier_text([]), "None")


class CliLineTests(unittest.TestCase):
    """The CLI lines name a command as text to type; they are not window texts."""

    def test_way_outs(self) -> None:
        """The CLI line after each read-only reason of the Product Owner table."""
        lines = {reason: words.read_only_way_out(reason) for reason in (
            "invalid_saved_data", "mission_path_changed", "returned_to_earlier_path", "association_mismatch",
            "adoption_required", "other")}
        self.assertEqual({reason: line and line_text(line) for reason, line in lines.items()}, {
            "invalid_saved_data": "Run map set-aside.", "mission_path_changed": "Run map adopt.",
            "returned_to_earlier_path": "Run map adopt.", "adoption_required": "Run map adopt.",
            "association_mismatch": "Run profile edit to correct the profile, then run map show.", "other": None})

    def test_operation_names(self) -> None:
        """The operation names of the not-available rows, and a tier the value layer cannot hold."""
        self.assertEqual([words.operation_name(name) for name in (
            "terrain_identity", "detect_bounds", "tier_layer", "tier_writes", "tier:Tier7")],
            ["Terrain identity", "Detecting bounds", "Original tiers", "Tier writes", "Tier Tier7"])
        self.assertEqual(words.unsupported_reason("tier_layer", "nothing"), words.NO_REASON_TEXT)



class ProductOwnerTextTests(unittest.TestCase):
    """The texts that the Product Owner replaced at 2026-10-16 15:28:30 (evidence 10.4), word for word."""

    def lines(self, result: dict) -> list[str]:
        """Return the text lines of an inspect answer."""
        return [line_text(block) for block in inspect_blocks({"display_name": "A"}, result)
                if hasattr(block, "parts")]

    def test_infected_row_has_no_repeated_prefix(self) -> None:
        """The row under the heading says "Infected areas"; the unknown result has its own form."""
        self.assertEqual(words.provider_row("native_infected", {"reason": "territory_not_registered"}),
                         "Infected areas: cfgenvironment.xml does not register env/zombie_territories.xml.")
        self.assertEqual(words.provider_row("native_infected", {"reason": "odd"}),
                         "Infected areas: the check gave the unknown result odd.")
        self.assertEqual(words.provider_row("native_bear", {"reason": WOLF_BEAR_REASON}), f"Bears: {WOLF_BEAR_REASON}")
        self.assertTrue(words.provider_reason("native_infected", {"reason": "odd"}).startswith(words.INFECTED_PREFIX))

    def test_zone_that_cannot_be_written(self) -> None:
        """No second "Effective tier" label; the sentence ends with the original-tier rule."""
        result = {"x": 1.0, "z": 1.0, "grid": {}, "original_tier": ["T1"], "effective_tier": ["T1"],
                  "tier_source": "original", "not_writable": {"name": "Zone A", "tier": "T3"}, "winner": None,
                  "abundance": {}, "encounters": []}
        self.assertIn("Zone A sets T3, but tiers cannot be written in this mission. The effective tier is the "
                      "original tier.", self.lines(result))

    def test_object_answer_basis_and_reason_line(self) -> None:
        """The cell basis is plain words, and a non-writable object points to map show."""
        answer = {"object_id": "o1", "effective_tiers": ["T1"], "original_tiers": ["T1"], "cell_count": 4,
                  "cells": "header", "tier_writable": False, "capacity": []}
        lines = self.lines(answer)
        self.assertIn("Cells: 4 (counted on the grid of the tier map)", lines)
        self.assertEqual(lines[lines.index("Tiers cannot be written in this mission. The effective tier is the "
                                           "original tier.") + 1], "Run map show for the reason.")
        self.assertIn("Cells: 4 (a cell counts when its centre is inside the object)",
                      self.lines({**answer, "cells": "d9", "tier_writable": True}))

    def test_capacity_change_line(self) -> None:
        """With and without a zone."""
        base = {"x": 1.0, "z": 1.0, "grid": {}, "original_tier": None, "effective_tier": None, "winner": None,
                "encounters": []}
        zone = {**base, "abundance": {"global": 10, "zone": 50, "zone_preset": "rich", "effective": 65}}
        self.assertIn("Capacity change: global +10%, zone Rich +50%, together +65%.", self.lines(zone))
        none = {**base, "abundance": {"global": 10, "zone": 0, "zone_preset": None, "effective": 10}}
        self.assertIn("Capacity change: global +10%, no zone, together +10%.", self.lines(none))


if __name__ == "__main__":
    unittest.main()
