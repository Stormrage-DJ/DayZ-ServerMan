"""Leaf 10.5: `map preview` and `map apply` through the real command line, owner sessions and compositions.

The fixture mission is `dayzOffline.enoch` in a temporary root. enoch passed 6.12, so these tests take it out of
the verified list while they run (`keep_fixture_family_unverified`); it is then a family that is not verified
(6.11). So
`map apply` refuses with exit 3 before the question, and no mission file changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import saved_state  # noqa: E402
from map_cli_fixtures import PROFILE_ID, MapFixture, keep_fixture_family_unverified  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402

from dayz_serverman.cli import map_publish_wording as words  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.domain.mission_map_families import NOT_VERIFIED_TEXT  # noqa: E402

PREVIEW = ["map", "preview", "--profile", PROFILE_ID]
APPLY = ["map", "apply", "--profile", PROFILE_ID]
ENOCH_TEXT = NOT_VERIFIED_TEXT.format(family="enoch")


class RunnerTests(MapFixture):
    """The parser, the help page, the owner session and the 6.11 refusal of the fixture mission."""

    def setUp(self) -> None:
        """Keep the fixture mission enoch unverified, as the 6.11 refusal path needs."""
        super().setUp()
        keep_fixture_family_unverified(self)

    def test_preview_prints_the_refused_review_and_changes_nothing(self) -> None:
        """The refused review names the family; the preview exits 0 and writes no saved data."""
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli(*PREVIEW)
        self.assertEqual(code, 0, stderr)
        lines = stdout.splitlines()
        self.assertEqual(lines[1:3], [words.REFUSALS_HEADING, "  " + ENOCH_TEXT])
        self.assertEqual(lines[-1], words.STOP_SERVER)
        code, stdout, _stderr = self.cli(*PREVIEW, "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["refused"], value["capabilities"]["family"]["verified"]), (0, True, False))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_apply_refuses_the_family_with_exit_3_before_the_question(self) -> None:
        """Text and JSON give the 6.11 gate text unchanged; `--yes` does not change it."""
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli(*APPLY, "--yes")
        self.assertEqual((code, stdout, stderr.strip()), (3, "", ENOCH_TEXT))
        code, stdout, _stderr = self.cli(*APPLY, "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]), (3, "UNSUPPORTED_SNAPSHOT_CONTENT", {
            "reason": "family_not_verified", "operation": "apply", "terrain_id": "enoch"}))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_command_line_and_help(self) -> None:
        """Apply needs a profile (exit 2); an option needs its value; the help page lists the three options."""
        self.assertEqual(self.cli("map", "apply")[0], 2)
        self.assertEqual(self.cli(*APPLY, "--keep-change")[0], 2)
        code, stdout, _stderr = self.cli("help", "map", "apply")
        self.assertEqual(code, 0)
        for flag in ("--keep-change PATH", "--replace-file PATH", "--exclude-encounter ID"):
            self.assertIn(flag, stdout)
        self.assertIn("Show what Apply would change in the mission files. Nothing is changed.",
                      self.cli("help", "map", "preview")[1])

    def test_output_follows_the_input_name_rules(self) -> None:
        """11.3: names, paths and backend texts are data; options are text to type; prose has no identifier."""
        RecordingOutput.made.clear()
        original = runner.Output
        runner.Output = RecordingOutput  # type: ignore[misc]
        try:
            self.cli(*PREVIEW)
            self.cli(*APPLY, "--yes")
            self.cli(*APPLY, "--exclude-encounter", "zone")
        finally:
            runner.Output = original  # type: ignore[misc]
        checker = InputNameChecker(self, flags() | {PROFILE_ID}, [*APPLY, "--exclude-encounter", "zone"])
        for output in RecordingOutput.made:
            checker.blocks(output.written)
