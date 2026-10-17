"""Leaf 10.32: `map restore` and `map recover` through the real command line, owner sessions and compositions.

The fixture mission `dayzOffline.enoch` in a temporary root has a saved plan and no Apply. So it has no original
and no previous state, and both modes refuse with exit 3 before the question. No mission file changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import saved_state  # noqa: E402
from map_cli_fixtures import PROFILE_ID, MapFixture, keep_fixture_family_unverified  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402

from dayz_serverman.cli import map_restore_wording as words  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.domain.mission_map_families import NOT_VERIFIED_TEXT  # noqa: E402
from dayz_serverman.domain.mission_map_restore_texts import (  # noqa: E402
    INVALID_MODE_TEXT, NO_ORIGINAL_TEXT, NO_PREVIOUS_STATE_TEXT,
)

RESTORE = ["map", "restore", "--profile", PROFILE_ID]
RECOVER = ["map", "recover"]


class RunnerTests(MapFixture):
    """The parser, the help page, the owner session, the 8.8 refusals and the recovery check."""

    def test_both_modes_refuse_without_a_restore_point(self) -> None:
        """No original and no previous state: exit 3 with the 8.8 text, also with `--yes`; nothing changes."""
        before = saved_state(self.manager, self.dayz)
        for mode, text in (("original", NO_ORIGINAL_TEXT), ("previous", NO_PREVIOUS_STATE_TEXT)):
            code, stdout, stderr = self.cli(*RESTORE, mode, "--yes")
            self.assertEqual((code, stdout, stderr.strip()), (3, "", text))
            code, stdout, _stderr = self.cli(*RESTORE, mode, "--json")
            error = json.loads(stdout)["error"]
            self.assertEqual((code, error["code"], error["message"]), (3, "INVALID_REQUEST", text))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_restore_original_refuses_an_unverified_family(self) -> None:
        """The 6.11 gate text unchanged with exit 3, before the question."""
        keep_fixture_family_unverified(self)
        code, stdout, stderr = self.cli(*RESTORE, "original", "--yes")
        self.assertEqual((code, stdout, stderr.strip()), (3, "", NOT_VERIFIED_TEXT.format(family="enoch")))

    def test_command_line_and_help(self) -> None:
        """A mode and a profile are needed; another mode exits 2 with the 8.8 text; the help page names both modes."""
        self.assertEqual(self.cli(*RESTORE)[0], 2)
        self.assertEqual(self.cli("map", "restore", "original")[0], 2)
        code, _stdout, stderr = self.cli(*RESTORE, "sideways")
        self.assertEqual((code, stderr.strip()),
                         (2, f"{INVALID_MODE_TEXT} Type map restore original or map restore previous."))
        code, stdout, _stderr = self.cli("help", "map", "restore")
        self.assertEqual(code, 0)
        self.assertIn("map restore original|previous", stdout)
        self.assertIn("What to restore: original or previous.", stdout)
        self.assertIn("Finish or undo an interrupted mission map change.", self.cli("help", "map", "recover")[1])

    def test_recover_without_a_block_exits_0(self) -> None:
        """6a in text; the JSON value has the recovery answer."""
        code, stdout, stderr = self.cli(*RECOVER)
        self.assertEqual((code, stdout.strip()), (0, words.CLEARED), stderr)
        code, stdout, _stderr = self.cli(*RECOVER, "--json")
        self.assertEqual((code, json.loads(stdout)["value"]["result"]["blocked"]), (0, False))

    def test_output_follows_the_input_name_rules(self) -> None:
        """11.3: backend texts are data; prose has no identifier."""
        RecordingOutput.made.clear()
        original = runner.Output
        runner.Output = RecordingOutput  # type: ignore[misc]
        try:
            self.cli(*RESTORE, "original", "--yes")
            self.cli(*RESTORE, "previous")
            self.cli(*RESTORE, "sideways")
            self.cli(*RECOVER)
        finally:
            runner.Output = original  # type: ignore[misc]
        checker = InputNameChecker(self, flags() | {PROFILE_ID}, [*RESTORE, "previous"])
        for output in RecordingOutput.made:
            checker.blocks(output.written)
