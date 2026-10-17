"""Leaf 10.7: `map copy --from-profile` through the runner on real compositions (isolation, terrain, pins, --yes)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from edit_cli_fixtures import ChangingTerminal, FakeTerminal  # noqa: E402
from folder_lock_fixtures import tree_hashes  # noqa: E402
from lifecycle_cli_fixtures import OTHER_ID, OTHER_NAME, saved_state  # noqa: E402
from map_cli_fixtures import PROFILE_ID, MapFixture  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402
from test_mission_map_plan import circle, encounter, stored_plan  # noqa: E402

from dayz_serverman.cli import help_wording, runner  # noqa: E402
from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli.map_publish_wording import WOLF_BEAR_REASON  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.registry import Confirm, Kind, ProfileRule  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402

COPY = ["map", "copy", "--profile", PROFILE_ID, "--from-profile", OTHER_ID]
CONFIRM_LISTED = ("This copy has items that DayZ-ServerMan does not copy, or it needs a placement review. Nothing was "
                  "changed. Check the list above, then add --confirm-listed.")


class MapCopySpecTests(unittest.TestCase):
    """The spec and the help texts of `map copy`."""

    def test_spec_is_a_reviewed_owner_command_of_phase_10(self) -> None:
        """WRITE, profile required, REVIEW, the two owner methods and the options of D11."""
        command = {command.name: command for command in COMMANDS}["map copy"]
        self.assertEqual((command.kind, command.phase, command.profile, command.confirm, command.pending),
                         (Kind.WRITE, 10, ProfileRule.WRITE_REQUIRED, Confirm.REVIEW, False))
        self.assertEqual(command.bridge_methods, {"preview_mission_map_copy", "copy_mission_map_plan"})
        self.assertEqual([(option.dest, option.flag, option.required) for option in command.options],
                         [("profile", "--profile", False), ("source_profile", "--from-profile", True),
                          ("transfer_mode", "--mode", True), ("confirm_listed", "--confirm-listed", False),
                          ("plan_revision", "--expect-revision", False)])

    def test_help_texts_are_the_product_owner_texts(self) -> None:
        """The command and the new destination use the texts of evidence 10.1."""
        self.assertEqual(help_wording.COMMAND_TEXTS["map copy"], "Replace the saved map plan with the plan of another "
                         "profile, or merge the two, after a review.")
        self.assertEqual(help_wording.OPTION_TEXTS["source_profile"], "The profile whose map plan is copied, by its ID "
                         "or its name.")


class MapCopyTests(MapFixture):
    """`map copy` into the fixture profile (one circle at revision 0) from the second profile of the fixture."""

    def save_source(self, objects: list[dict], **terrain: object) -> None:
        """Save the first plan of the second profile, with terrain changes."""
        composition = build_composition(self.manager)
        try:
            plan = stored_plan(objects)
            plan["terrain"].update(terrain)
            plan["association"] = composition.mission_map.plans.active_association(OTHER_ID)
            plan["background"] = None
            composition.mission_map.plans.save(OTHER_ID, plan, None)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def source_tree(self) -> dict:
        """Return the hashes of the map editor folders of the second profile."""
        return tree_hashes(*{path.parent for path in (self.manager / "data").rglob("plan.json")
                             if OTHER_ID in path.parts})

    def shown(self, profile: str = PROFILE_ID) -> dict:
        """Return the JSON value of `map show` for one profile."""
        code, stdout, stderr = self.cli("map", "show", "--profile", profile, "--json")
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)["value"]

    def test_merge_on_the_same_terrain_keeps_the_source_and_the_destination_identity(self) -> None:
        """Profile isolation: the source folder does not change; the destination keeps association and plan ID."""
        self.save_source([circle("src1")])
        source, before = self.source_tree(), self.shown()["plan"]
        code, stdout, stderr = self.cli(*COPY, "--mode", "merge", "--yes")
        lines = stdout.splitlines()
        self.assertEqual((code, lines[-1]), (0, "Map plan copied as revision 1."), stderr)
        self.assertIn(words.TERRAIN_SAME, lines)
        self.assertNotIn(words.PLACEMENT_REVIEW, lines)
        # The copy forms of the Product Owner entry 2026-10-17 04:37:12 in evidence 10.7
        self.assertIn("Objects in the source plan: 1", lines)
        self.assertIn("Merge: the objects of the source plan go above all objects of this plan, in their order.",
                      lines)
        self.assertFalse([line for line in lines if "the file" in line])
        self.assertIn("The plan will have 2 objects and 0 polygon vertices.", lines)
        after = self.shown()["plan"]
        self.assertEqual([item["id"] for item in after["objects"]], ["src1", "observer-loot"])
        self.assertEqual((after["association"], after["plan_id"]), (before["association"], before["plan_id"]))
        self.assertEqual(self.source_tree(), source)
        self.assertEqual([item["id"] for item in self.shown(OTHER_ID)["plan"]["objects"]], ["src1"])
        code, stdout, stderr = self.cli(*COPY, "--mode", "replace", "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["operations"], value["result"]["revision"]), (0, [], 2), stderr)
        self.assertEqual((value["review"]["destination_revision"], set(value["result"])), (1, {"revision", "report"}))

    def test_another_terrain_needs_confirm_listed_also_with_yes(self) -> None:
        """The placement review: exit 4 with the copy text after the review; with --confirm-listed the copy runs."""
        self.save_source([circle("inside"), circle("far", x=5000.0)], terrain_id="other.terrain",
                         bounds={"x_min": 0.0, "z_min": 0.0, "width": 10000.0, "height": 10000.0})
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli(*COPY, "--mode", "replace", "--yes")
        lines = stdout.splitlines()
        self.assertEqual(code, 4)
        for line in ("Terrain: other.terrain in the source profile, test.terrain in this mission",
                     "Bounds: the source plan differs by X 0, Z 0, width 9000 and height 9000 metres",
                     "Replace: the objects of the source plan replace the objects of this plan.",
                     words.PLACEMENT_REVIEW,
                     words.EXCLUDED_HEADING):
            self.assertIn(line, lines)
        self.assertIn(CONFIRM_LISTED, stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        code, stdout, stderr = self.cli(*COPY, "--mode", "replace", "--confirm-listed", "--yes")
        self.assertEqual((code, stdout.splitlines()[-1]),
                         (0, "Map plan copied as revision 1. 1 object was left out."), stderr)
        plan = self.shown()["plan"]
        self.assertEqual(([item["id"] for item in plan["objects"]], plan["terrain"]["terrain_id"]),
                         (["inside"], "test.terrain"))

    def test_the_copy_preview_uses_the_destination_availability(self) -> None:
        """A wolf encounter of the source is listed with the 7.9 text and copied without --confirm-listed."""
        self.save_source([circle("src1"), encounter("wolves", "native_wolf")])
        code, stdout, stderr = self.cli(*COPY, "--mode", "replace", "--yes")
        lines = stdout.splitlines()
        self.assertEqual(code, 0, stderr)
        start = lines.index(words.UNAVAILABLE_HEADING)
        self.assertEqual(lines[start + 1:start + 2], [f"  Encounter 1: {WOLF_BEAR_REASON}"])
        self.assertEqual([item["id"] for item in self.shown()["plan"]["objects"]], ["src1", "wolves"])

    def test_the_question_and_yes(self) -> None:
        """No terminal without --yes exits 4; "n" exits 4; "y" copies; nothing changes before that."""
        self.save_source([circle("src1")])
        before = saved_state(self.manager, self.dayz)
        for stdin, reads in ((FakeTerminal("y\n", terminal=False), 0), (FakeTerminal("n\n"), 1)):
            with self.subTest(reads=reads):
                code, _stdout, stderr = self.cli(*COPY, "--mode", "merge", stdin=stdin)
                self.assertEqual((code, stdin.reads), (4, reads), stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        stdin = FakeTerminal("y\n")
        code, stdout, stderr = self.cli(*COPY, "--mode", "merge", stdin=stdin)
        self.assertEqual((code, stdin.reads, stdout.splitlines()[-1]), (0, 1, "Map plan copied as revision 1."),
                         stderr)

    def test_stale_revision_and_pins(self) -> None:
        """A pin that differs exits 3 before the question; a save during the question exits 3 with the 10.15 text."""
        self.save_source([circle("src1")])
        code, stdout, _stderr = self.cli(*COPY, "--mode", "merge", "--yes", "--expect-revision", "4", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["expected"]), (3, "REVISION_CONFLICT", 4))
        stdin = ChangingTerminal(lambda: self.save_objects([circle("changed")]))
        code, _stdout, stderr = self.cli(*COPY, "--mode", "merge", stdin=stdin)
        self.assertEqual(code, 3, stderr)
        self.assertIn(words.STALE_PLAN, stderr)
        self.assertEqual([item["id"] for item in self.shown()["plan"]["objects"]], ["changed"])
        code, _stdout, stderr = self.cli(*COPY, "--mode", "merge", "--yes", "--expect-revision", "1")
        self.assertEqual(code, 0, stderr)

    def test_refusals_of_the_source(self) -> None:
        """The same profile and an unknown profile exit 2; a source without a valid plan exits 3; nothing changes."""
        before = saved_state(self.manager, self.dayz)
        same = ["map", "copy", "--profile", PROFILE_ID, "--from-profile", PROFILE_ID, "--mode", "merge", "--yes"]
        code, _stdout, stderr = self.cli(*same)
        self.assertEqual(code, 2)
        self.assertIn("Copy needs another profile as its source. Name it with --from-profile.", stderr)
        code, _stdout, _stderr = self.cli("map", "copy", "--profile", PROFILE_ID, "--from-profile", "nobody",
                                          "--mode", "merge", "--yes")
        self.assertEqual(code, 2)
        code, _stdout, stderr = self.cli(*COPY, "--mode", "sideways", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("Choose --mode replace or --mode merge.", stderr)
        code, _stdout, stderr = self.cli(*COPY, "--mode", "merge", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("The source profile has no valid saved map plan to copy. Nothing was changed.", stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        # The source by its name, as --profile resolves it
        self.save_source([circle("src1")])
        code, _stdout, stderr = self.cli("map", "copy", "--profile", PROFILE_ID, "--from-profile", OTHER_NAME,
                                         "--mode", "merge", "--yes")
        self.assertEqual(code, 0, stderr)

    def test_output_follows_the_input_name_rules(self) -> None:
        """11.3: review values are data, the command words are text to type, the profile names are echoes."""
        self.save_source([circle("src1")], terrain_id="other.terrain")
        RecordingOutput.made.clear()
        original = runner.Output
        runner.Output = RecordingOutput  # type: ignore[misc]
        try:
            self.cli(*COPY, "--mode", "merge", "--yes")
            self.cli("map", "copy", "--profile", PROFILE_ID, "--from-profile", PROFILE_ID, "--mode", "merge")
            self.cli(*COPY, "--mode", "merge", "--confirm-listed", "--yes")
        finally:
            runner.Output = original  # type: ignore[misc]
        checker = InputNameChecker(self, flags() | {PROFILE_ID, OTHER_ID}, [OTHER_ID, PROFILE_ID])
        for output in RecordingOutput.made:
            checker.blocks(output.written)


if __name__ == "__main__":
    unittest.main()
