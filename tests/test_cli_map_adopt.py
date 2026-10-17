"""Leaf 10.36: `map adopt` over the real adoption review, the real lane and the real commit of leaf 10.35.

For each case of the review: no terminal and JSON without `--yes` exit 4 and change nothing. Then `--yes` prints
the review sections of the dialog with the answer texts unchanged, and the end line of the kind and the case.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import map_adopt_fixtures  # noqa: E402
from map_adopt_fixtures import SPEC, AdoptCliFixture  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402

from dayz_serverman.application.mission_map_plans import AssociationMismatch  # noqa: E402
from dayz_serverman.cli import help_wording, map_wording  # noqa: E402
from dayz_serverman.cli.map_adopt_render import adopt_blocks  # noqa: E402
from dayz_serverman.cli.output import line_text  # noqa: E402
from dayz_serverman.cli.registry import Confirm, Kind, ProfileRule  # noqa: E402
from dayz_serverman.domain.mission_map_adoption_texts import NOTHING_TO_ADOPT_TEXT  # noqa: E402
from tests.test_mission_map_preview import tree  # noqa: E402

# The kind success sentence of ADOPT-29; the ADOPT-8 line of the case follows it
ADOPTED = "Mission map records adopted."
# An independent copy of ADOPT-10, ADOPT-11, ADOPT-14, ADOPT-15 (list label) and ADOPT-17 (evidence 10.1)
HEADINGS = ("Adoption is not possible", "What happened", "What adoption writes", "Records that adoption writes",
            "What stays unchanged")


def expected_review(name: str, answer: dict) -> list[str]:
    """Return the review lines of an answer that is not refused, in the order of the dialog."""
    profiles = [item["display_name"] or f"{item['profile_id']} (deleted profile)" for item in answer["other_profiles"]]
    lines = [f"Profile: {name}", HEADINGS[1], f"  {answer['happened']}"]
    if answer["other_profiles_text"]:
        lines += [f"  {answer['other_profiles_text']}", *(f"    {profile}" for profile in profiles)]
    lines += [HEADINGS[2], f"  {answer['intro']}", f"  {HEADINGS[3]}", *(f"    {row}" for row in answer["records"])]
    return [*lines, HEADINGS[4], *(f"  {text}" for text in answer["unchanged"])]


class SpecTests(unittest.TestCase):
    """D11, D14 and D20: an owner command of phase 10 that reviews and asks, with no other option."""

    def test_spec_and_help_text(self) -> None:
        """Session, confirmation, profile rule, methods and the Product Owner command text."""
        self.assertEqual((SPEC.kind, SPEC.confirm, SPEC.profile, SPEC.phase, [option.dest for option in SPEC.options]),
                         (Kind.WRITE, Confirm.REVIEW, ProfileRule.WRITE_REQUIRED, 10, ["profile"]))
        self.assertEqual(SPEC.bridge_methods,
                         {"get_application_snapshot", "preview_mission_map_adoption", "adopt_mission_map"})
        self.assertEqual(help_wording.COMMAND_TEXTS["map adopt"], "Move or copy the map editor records of a mission "
                         "that moved, was copied or was restored, after a review.")

    def test_a_refused_answer_shows_the_refusals_and_what_happened_only(self) -> None:
        """The dialog rule for a refused answer; the headings are the Product Owner texts."""
        answer = {"refused": True, "refusals": [{"message": "Refused."}], "happened": "Happened.",
                  "intro": "Intro.", "records": ["Row."], "unchanged": ["Kept."], "other_profiles": []}
        lines = [line_text(block) for block in adopt_blocks({"display_name": "Main"}, answer)]
        self.assertEqual(lines, ["Profile: Main", HEADINGS[0], "  Refused.", HEADINGS[1], "  Happened."])


class CaseTests(AdoptCliFixture):
    """Each case: the question, JSON without `--yes`, then the confirmed adoption and its end line."""

    def check(self, case: str, route: str | None = None) -> dict:
        """Run the four steps for the case that the fixture built; return the JSON value of a last run."""
        who = getattr(self, f"case_{case}")()
        answer = self.reader.review(who).answer
        self.assertEqual((answer["case"], answer["refused"]), ("copy" if case in ("capture", "records_only") else
                                                               case, False))
        before = (tree(self.area), tree(self.root))
        stdin = FakeTerminal("y\n", terminal=False)
        run = self.command(profile=who, yes=False, stdin=stdin)
        self.assertEqual((run.code, stdin.reads), (4, 0))
        run = self.command(profile=who, yes=False, json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["review"]["case"]),
                         (4, "CONFIRMATION_REQUIRED", answer["case"]))
        self.assertNotIn("adopt_mission_map", run.calls)
        self.assertEqual((tree(self.area), tree(self.root)), before)
        name = self.profiles.records[who].values.display_name
        run = self.command(profile=who)
        self.assertEqual(run.code, 0, run.stderr)
        self.assertEqual(run.stdout.splitlines(), [*expected_review(name, answer), f"{ADOPTED} {answer['success']}"])
        # Adoption writes no mission file (ADOPT-18), also with a running server (AR-3)
        self.assertEqual(tree(self.root), before[1])
        self.assertIsNone(self.load(who)["adoption"])
        return answer

    def test_true_move(self) -> None:
        """The records move to the new path."""
        self.check("move")

    def test_copy_after_a_path_change(self) -> None:
        """The path-change variant of the copy texts."""
        self.assertEqual(self.check("copy")["route"], "path_change")

    def test_copy_after_a_capture_refusal(self) -> None:
        """The capture variant of the copy texts, for another profile on a copied folder."""
        self.assertEqual(self.check("capture")["route"], "capture")

    def test_records_only_copy(self) -> None:
        """No original, baseline or ledger row (records only)."""
        self.assertTrue(self.check("records_only")["records_only"])

    def test_retired_folder(self) -> None:
        """The history folder row names the dated name."""
        answer = self.check("retired")
        self.assertIn(answer["history_name"], answer["records"][0])

    def test_restore_case(self) -> None:
        """The invalidated record is closed."""
        self.check("restore")


class QuestionAndValueTests(AdoptCliFixture):
    """A terminal asks after the review; the JSON value of D12."""

    def test_question_in_a_terminal(self) -> None:
        """"n" exits 4 after one read; "y" adopts."""
        self.case_move()
        stdin = FakeTerminal("n\n")
        run = self.command(yes=False, stdin=stdin)
        self.assertEqual((run.code, stdin.reads, run.stdout.splitlines()[-1].startswith("  ")), (4, 1, True))
        self.assertIn("Continue? [y/N]", run.stderr)
        stdin = FakeTerminal("y\n")
        self.assertEqual((self.command(yes=False, stdin=stdin).code, stdin.reads), (0, 1))

    def test_json_value_of_a_confirmed_adoption(self) -> None:
        """The record, the adoption answer and the record result with case, result and history name."""
        self.case_copy()
        answer = self.reader.review("main").answer
        value = self.command(json_mode=True).json()["value"]
        self.assertEqual((value["operations"][0]["kind"], value["review"]), ("ADOPT_MISSION_MAP", answer))
        self.assertEqual((value["result"]["case"], value["result"]["result"]), ("copy", "COMMITTED"))
        self.assertIn("history_name", value["result"])
        self.assertEqual(value["result"], value["operations"][0]["result"])

    def test_reviews_follow_the_input_name_rules(self) -> None:
        """11.3: paths, names and backend texts are data; the headings hold no identifier."""
        self.case_capture()
        RecordingOutput.made.clear()
        with patch.object(map_adopt_fixtures, "Output", RecordingOutput):
            self.command(profile="third")
        checker = InputNameChecker(self, flags() | {"main", "third"}, ["map", "adopt"])
        for output in RecordingOutput.made:
            checker.blocks(output.written)


class RefusalTests(AdoptCliFixture):
    """Refusals of the review exit 3 before the question, with the backend texts unchanged."""

    def test_nothing_to_adopt(self) -> None:
        """`nothing_to_adopt`: exit 3, `INVALID_REQUEST`, nothing asked."""
        self.applied()
        stdin = FakeTerminal("y\n")
        run = self.command(yes=False, stdin=stdin)
        self.assertEqual((run.code, run.stdout, run.stderr.strip(), stdin.reads), (3, "", NOTHING_TO_ADOPT_TEXT, 0))
        error = self.command(json_mode=True).json()["error"]
        self.assertEqual((error["code"], error["details"]), ("INVALID_REQUEST", {"reason": "nothing_to_adopt",
                                                                                 "path": None}))

    def test_a_file_that_differs_refuses_with_its_name(self) -> None:
        """`adoption_refused` (ADOPT-25): the first refusal text and its file."""
        self.case_move()
        self.write("mapgrouppos.xml", b"<admin-edit/>")
        first = self.reader.review("main").answer["refusals"][0]
        before = tree(self.area)
        error = self.command(json_mode=True).json()["error"]
        self.assertEqual((error["code"], error["message"], error["details"]),
                         ("INVALID_REQUEST", first["message"], {"reason": "adoption_refused", "path": first["file"]}))
        self.assertEqual(tree(self.area), before)

    def test_records_that_do_not_match_the_settings(self) -> None:
        """`association_mismatch` at dispatch: the `map show` text, its CLI line and "Nothing was changed."."""
        with patch.object(self.adoption, "preview", side_effect=AssociationMismatch("mismatch")):
            run = self.command(json_mode=True)
        error = run.json()["error"]
        expected = (f"{map_wording.READ_ONLY_TEXTS['association_mismatch']} Run profile edit to correct the profile, "
                    "then run map show. Nothing was changed.")
        self.assertEqual((run.code, error["code"], error["message"]), (3, "PROFILE_CONTEXT_MISMATCH", expected))


if __name__ == "__main__":
    unittest.main()
