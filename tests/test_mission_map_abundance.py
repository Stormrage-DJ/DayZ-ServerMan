"""Backend evaluation of capacity presets and global steps through load and save, and window parity (5.1)."""

from __future__ import annotations

import json
import re
import unittest

from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.domain.mission_map_loot_report_texts import CAPACITY_EXPLANATION
from dayz_serverman.domain.mission_map_values import ABUNDANCE_PERCENT, GLOBAL_ABUNDANCE_STEPS
from tests.test_mission_map_editor_load_save import CoordinatorFixture
from tests.test_mission_map_plan import circle, square
from tests.ui_harness_support import FRONTEND


def window_source(name: str) -> str:
    """Return the text of one editor module."""
    return (FRONTEND / name).read_text(encoding="utf-8")


class AbundanceEvaluationTests(CoordinatorFixture):
    """The window shows `evaluation` of load and save; these tests pin what the backend sends (D9)."""

    def test_every_preset_and_global_step_round_trips_with_its_evaluation(self) -> None:
        """Each of the 28 pairs: the save answer and the next load give global plus the winning preset, once."""
        revision = None
        for global_value in GLOBAL_ABUNDANCE_STEPS:
            for preset, zone in ABUNDANCE_PERCENT.items():
                with self.subTest(global_value=global_value, preset=preset):
                    plan = self.draft(objects=[dict(circle("zone"), abundance=preset), square("plain")])
                    plan["global_abundance"] = global_value
                    answer = self.save(plan, revision)
                    revision = answer["revision"]
                    for evaluation in (answer["evaluation"], self.load()["evaluation"]):
                        self.assertEqual((evaluation["revision"], evaluation["global"]), (revision, global_value))
                        entry = evaluation["objects"][0]
                        self.assertEqual((entry["id"], entry["zone_adjustment"], entry["effective_adjustment"]),
                                         ("zone", zone, global_value + zone))
                        # The second zone keeps its own preset; nothing stacks from another zone
                        self.assertEqual(evaluation["objects"][1]["effective_adjustment"], global_value - 25)

    def test_published_r08_rows(self) -> None:
        """The four rows of the R08 table and the 5.1 example, as the window receives them."""
        revision = None
        for global_value, preset, expected in ((0, "rich", 50), (100, "very_rich", 200), (-50, "rich", 0),
                                               (-50, "sparse", -75), (100, "rich", 150)):
            with self.subTest(global_value=global_value, preset=preset):
                plan = self.draft(objects=[dict(circle("zone"), abundance=preset)])
                plan["global_abundance"] = global_value
                answer = self.save(plan, revision)
                revision = answer["revision"]
                self.assertEqual(answer["evaluation"]["objects"][0]["effective_adjustment"], expected)

    def test_a_global_value_between_steps_is_refused_and_keeps_the_record(self) -> None:
        """Only the seven R08 steps save; a refused value leaves the saved plan and its evaluation unchanged."""
        plan = self.draft(objects=[circle("zone")])
        self.save(dict(plan, global_abundance=25))
        for invalid in (10, -75, 125, 25.5, True, "25", None):
            with self.subTest(value=invalid):
                self.refused(lambda: self.save(dict(plan, global_abundance=invalid), 0),
                             ErrorCode.INVALID_REQUEST, "invalid_document")
        loaded = self.load()
        self.assertEqual((loaded["revision"], loaded["plan"]["global_abundance"], loaded["evaluation"]["global"]),
                         (0, 25, 25))

    def test_a_new_plan_starts_at_global_zero(self) -> None:
        """Detected bounds give a new plan at 0%, and its evaluation says so."""
        self.add_header()
        loaded = self.load()
        self.assertEqual((loaded["plan"]["global_abundance"], loaded["evaluation"]["global"]), (0, 0))


class AbundanceWindowParityTests(unittest.TestCase):
    """The window copies of the presets, steps and explanation equal the backend values."""

    def test_explanation_steps_and_presets_match_the_backend(self) -> None:
        """A change on one side fails here."""
        source = window_source("map_editor_abundance.js")
        explanation = re.findall(r'^  explanation: ("(?:[^"\\]|\\.)*"),$', source, re.MULTILINE)
        self.assertEqual([json.loads(text) for text in explanation], [CAPACITY_EXPLANATION])
        steps = re.findall(r"const mapAbundanceSteps = Object\.freeze\(\[([^\]]*)\]\);", source)
        self.assertEqual([[int(value) for value in found.split(",")] for found in steps], [list(GLOBAL_ABUNDANCE_STEPS)])
        presets = re.findall(r"const mapAbundancePresets = Object\.freeze\(\[([^\]]*)\]\);", source)
        self.assertEqual([re.findall(r'"(\w+)"', found) for found in presets], [list(ABUNDANCE_PERCENT)])

    def test_preset_labels_name_the_backend_percent(self) -> None:
        """Each catalogue label ends with the signed percent of its preset (R07)."""
        found = re.findall(r"capacityPresets: Object\.freeze\(\{([^}]*)\}\)", window_source("map_editor_wording.js"))
        self.assertEqual(len(found), 1)
        labels = dict(re.findall(r'(\w+): "([^"]*)"', found[0]))
        self.assertEqual(set(labels), set(ABUNDANCE_PERCENT))
        for preset, percent in ABUNDANCE_PERCENT.items():
            self.assertTrue(labels[preset].endswith(f" {'+' if percent > 0 else ''}{percent}%"), labels[preset])


if __name__ == "__main__":
    unittest.main()
