"""Leaf 10.32: `map restore original|previous` and `map recover` over the real restore, lane and recovery.

The review prints the window sections of the 8.8 answer with its texts unchanged. The question follows the review.
A refused review, an invalid mode and the 6.11 gate exit before the question. `map recover` prints 6a and 6b.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import map_restore_fixtures  # noqa: E402
from map_restore_fixtures import RestoreCliFixture  # noqa: E402
from map_publish_fixtures import SPECS  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402

from dayz_serverman.application.mission_map_plans import MissionPathChanged  # noqa: E402
from dayz_serverman.cli import help_wording, map_wording  # noqa: E402
from dayz_serverman.cli import map_restore_wording as words  # noqa: E402
from dayz_serverman.cli.commands import map_restore  # noqa: E402
from dayz_serverman.cli.output import CliFailure, TypeText  # noqa: E402
from dayz_serverman.cli.registry import Confirm, Kind, ProfileRule  # noqa: E402
from dayz_serverman.domain.mission_map_restore_texts import (  # noqa: E402
    BACKUP_TEXT, INVALID_MODE_TEXT, NO_ORIGINAL_TEXT, NO_PREVIOUS_STATE_TEXT, SHARED_ORIGINAL_TEXT,
)

# The record time of the fixture Apply in the local form of the read commands
TIME = "2026-10-15 10:00"
# The 8.8 text of an invalid mode, then the CLI line (Product Owner 2026-10-17 02:14:51 in evidence 10.32)
INVALID_MODE = f"{INVALID_MODE_TEXT} Type map restore original or map restore previous."


class SpecTests(unittest.TestCase):
    """D11, D14 and D20: owner commands of phase 10; the restore reviews and asks, recover asks nothing."""

    def test_specs_and_help_texts(self) -> None:
        """Session, confirmation, profile rule, methods, the positional mode and the Product Owner texts."""
        restore, recover = SPECS["map restore"], SPECS["map recover"]
        self.assertEqual((restore.kind, restore.confirm, restore.profile, restore.phase, restore.prestep),
                         (Kind.WRITE, Confirm.REVIEW, ProfileRule.WRITE_REQUIRED, 10, "commands.map_restore:mode"))
        self.assertEqual(restore.bridge_methods,
                         {"get_application_snapshot", "preview_mission_map_restore", "restore_mission_map"})
        self.assertEqual([(option.dest, option.choices, option.metavar) for option in restore.options[1:]],
                         [("restore_mode", (), "original|previous")])
        self.assertEqual((recover.kind, recover.confirm, recover.profile, recover.options, recover.phase),
                         (Kind.WRITE, Confirm.NONE, ProfileRule.NONE, (), 10))
        self.assertEqual(recover.bridge_methods, {"inspect_mission_map_recovery"})
        self.assertEqual(help_wording.COMMAND_TEXTS["map restore"],
                         "Restore the original mission files or the previous state, after a review.")
        self.assertEqual(help_wording.COMMAND_TEXTS["map recover"], "Finish or undo an interrupted mission map change.")
        self.assertEqual(help_wording.OPTION_TEXTS["restore_mode"], "What to restore: original or previous.")


class RestoreTests(RestoreCliFixture):
    """Both modes: the review text, the JSON value of D12, the question and the end line of the kind."""

    def test_previous_state_review_and_restore(self) -> None:
        """The other profiles with the intro line, what the restore writes, the backup line and the end line."""
        self.applied()
        self.edit_positions()
        run = self.restore("previous")
        self.assertEqual(run.code, 0, run.stderr)
        lines = run.stdout.splitlines()
        self.assertEqual(lines[:4], ["Profile: Main", words.NOTICES_HEADING, "  " + words.OTHER_PROFILES, "    Second"])
        start = lines.index(words.WRITES_HEADING)
        self.assertEqual(lines[start + 1:start + 3],
                         ["  " + words.PREVIOUS_WRITES.format(change="Apply", time=TIME), "  " + words.AFFECTED])
        self.assertIn("    mapgrouppos.xml", lines)
        self.assertEqual(lines[lines.index(words.OVERWRITTEN_HEADING) + 1],
                         "  " + BACKUP_TEXT.format(path="mapgrouppos.xml"))
        self.assertEqual(lines[-2:], [words.STOP_SERVER, "Previous mission map restored."])
        self.assertNotIn(words.REFUSALS_HEADING, lines)
        # Restore previous state of the Apply returned every file to the original; the original changes nothing
        self.assertEqual(self.restore("original").code, 0)

    def test_original_review_names_the_shared_original_and_json_value(self) -> None:
        """The shared-original text replaces the intro line; JSON has the record, the 8.8 answer and the result."""
        self.applied()
        review = self.restorer.preview("main", "original")
        run = self.restore("original", json_mode=True)
        value = run.json()["value"]
        self.assertEqual((run.code, value["operations"][0]["kind"], value["result"]["result"]),
                         (0, "RESTORE_MISSION_MAP_ORIGINAL", "COMMITTED"))
        self.assertEqual(value["review"], review)
        self.assertEqual(value["result"], value["operations"][0]["result"])
        # A second Restore original reviews the same original and writes nothing
        lines = self.restore("original").stdout.splitlines()
        self.assertEqual(lines[1:4], [words.NOTICES_HEADING, "  " + SHARED_ORIGINAL_TEXT, "    Second"])
        self.assertNotIn("  " + words.OTHER_PROFILES, lines)
        self.assertIn("  " + words.ORIGINAL_WRITES.format(time=TIME), lines)
        self.assertEqual(lines[-1], "Original mission map restored.")

    def test_question_without_yes_and_without_a_terminal(self) -> None:
        """A terminal asks after the review; "n", no terminal and JSON without `--yes` exit 4 and change nothing."""
        self.applied()
        before = self.snapshot()
        stdin = FakeTerminal("n\n")
        run = self.restore("previous", yes=False, stdin=stdin)
        self.assertEqual((run.code, stdin.reads, run.stdout.splitlines()[-1]), (4, 1, words.STOP_SERVER))
        self.assertIn("Continue? [y/N]", run.stderr)
        stdin = FakeTerminal("y\n", terminal=False)
        self.assertEqual((self.restore("previous", yes=False, stdin=stdin).code, stdin.reads), (4, 0))
        run = self.restore("previous", yes=False, json_mode=True, stdin=FakeTerminal("y\n"))
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["review"]["mode"]),
                         (4, "CONFIRMATION_REQUIRED", "previous"))
        self.assertNotIn("restore_mission_map", run.calls)
        self.assertEqual(self.snapshot(), before)
        stdin = FakeTerminal("y\n")
        self.assertEqual((self.restore("previous", yes=False, stdin=stdin).code, stdin.reads), (0, 1))


    def test_reviews_follow_the_input_name_rules(self) -> None:
        """11.3: paths, names and backend texts are data; window prose holds no identifier."""
        self.applied()
        self.edit_positions()
        RecordingOutput.made.clear()
        with patch.object(map_restore_fixtures, "Output", RecordingOutput):
            self.restore("previous")
            self.restore("original")
            self.recover()
        self.assertEqual(len(RecordingOutput.made), 3)
        checker = InputNameChecker(self, flags() | {"main"}, ["map", "restore", "previous"])
        for output in RecordingOutput.made:
            checker.blocks(output.written)


class RefusalTests(RestoreCliFixture):
    """Refusals before the question: the 8.8 texts unchanged, the 6.11 gate text and the invalid mode."""

    def test_refused_reviews_exit_3_with_the_8_8_text_before_the_question(self) -> None:
        """No original and no previous state: exit 3, `INVALID_REQUEST` with the reason; nothing is asked."""
        before = self.snapshot()
        for mode, reason, text in (("original", "no_original", NO_ORIGINAL_TEXT),
                                   ("previous", "no_previous_state", NO_PREVIOUS_STATE_TEXT)):
            with self.subTest(mode=mode):
                stdin = FakeTerminal("y\n")
                run = self.restore(mode, yes=False, stdin=stdin)
                self.assertEqual((run.code, run.stdout, run.stderr.strip(), stdin.reads), (3, "", text, 0))
                error = self.restore(mode, json_mode=True).json()["error"]
                self.assertEqual((error["code"], error["message"], error["details"]["reason"]),
                                 ("INVALID_REQUEST", text, reason))
                self.assertNotIn("restore_mission_map", run.calls)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_mode_exits_2_with_the_8_8_text_and_the_cli_line_before_any_call(self) -> None:
        """The pre-step refuses another word with `INVALID_MODE_TEXT` and the CLI line, before every bridge call."""
        run = self.restore("sideways", json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["message"], error["details"], run.calls),
                         (2, INVALID_MODE, {"reason": "invalid_mode"}, []))
        # Both commands of the CLI line are text to type (11.3)
        with self.assertRaises(CliFailure) as refused:
            map_restore.mode(None, SimpleNamespace(restore_mode="sideways"), None)  # type: ignore[arg-type]
        self.assertEqual([part for part in refused.exception.message.parts if isinstance(part, TypeText)],
                         [TypeText("map restore original"), TypeText("map restore previous")])

    def test_unverified_family_refuses_restore_original_only(self) -> None:
        """Restore original exits 3 with the 6.11 text; Restore previous state never consults the gate."""
        family = self.restorer.preview("other", "original")["family"]
        run = self.restore("original", profile="other", json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["message"], error["details"]),
                         (3, "UNSUPPORTED_SNAPSHOT_CONTENT", family["text"],
                          {"reason": family["reason"], "operation": "restore_original",
                           "terrain_id": family["terrain_id"]}))
        self.assertFalse(family["verified"])
        run = self.restore("previous", profile="other")
        self.assertEqual((run.code, run.stderr.strip()), (3, NO_PREVIOUS_STATE_TEXT))

    def test_a_read_only_state_of_the_review_gets_the_words_of_map_show(self) -> None:
        """A mission path change: the window text, its CLI line and "Nothing was changed.", exit 3."""
        changed = MissionPathChanged([], "mission_path_changed")
        with patch.object(self.restorer, "preview", side_effect=changed):
            run = self.restore("original", json_mode=True)
        error = run.json()["error"]
        expected = f"{map_wording.READ_ONLY_TEXTS['mission_path_changed']} Run map adopt. Nothing was changed."
        self.assertEqual((run.code, error["code"], error["message"]), (3, "PROFILE_CONTEXT_MISMATCH", expected))


class RecoverTests(RestoreCliFixture):
    """`map recover`: 6a with exit 0, 6b with exit 3, and the block reason of the editor kind with exit 6."""

    def test_no_block_left_exits_0_with_6a(self) -> None:
        """The text is 6a; the JSON value has the recovery answer as its result."""
        run = self.recover()
        self.assertEqual((run.code, run.stdout.strip(), run.calls), (0, words.CLEARED, ["inspect_mission_map_recovery"]))
        value = self.recover(json_mode=True).json()["value"]
        self.assertEqual((value["operations"], value["review"], value["result"]["blocked"]), ([], None, False))

    def test_deferred_exits_3_with_6b(self) -> None:
        """While the lane runs other work, the check waits: 6b, exit 3, nothing changed."""
        with patch.object(self.lane, "is_drained", return_value=False):
            run = self.recover(json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["message"], error["details"]["recovery"]["deferred"]),
                         (3, "CONTROL_CONFLICT", words.DEFERRED, True))

    def test_an_unreadable_journal_exits_6_with_the_block_reason(self) -> None:
        """Any block but a running server exits 6 with the block reason of `APPLY_MISSION_MAP` (6c)."""
        self.journals.root.mkdir(parents=True, exist_ok=True)
        (self.journals.root / "broken.json").write_text("{", encoding="utf-8")
        run = self.recover(json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["message"]), (6, "RECOVERY_REQUIRED", (
            "A mission map change did not finish, and its recovery records cannot be read. Restart DayZ-ServerMan to "
            "check them again.")))


if __name__ == "__main__":
    unittest.main()
