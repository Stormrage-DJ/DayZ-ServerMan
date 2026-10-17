"""Leaf 10.36, D13 and AR-4: the exit codes of `map adopt` for a stale review, a lane refusal and a late failure.

The adoption lane runs for real. The fault hook of the commit step raises after the commit point. The retire hook
raises after the journal was retired as committed. A runner test uses the real command line.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from map_adopt_fixtures import AdoptCliFixture  # noqa: E402
from map_cli_fixtures import PROFILE_ID, MapFixture  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402

from dayz_serverman.application import mission_map_adoption  # noqa: E402
from dayz_serverman.application.activity_wording import KIND_TEXTS  # noqa: E402
from dayz_serverman.application.mission_map_apply_choices import ApplyRefused  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure  # noqa: E402
from dayz_serverman.domain.mission_map_adoption_texts import (  # noqa: E402
    ADOPTION_COMMITTED_TEXT, ADOPTION_STALE_TEXT, NOTHING_TO_ADOPT_TEXT,
)

KIND = "ADOPT_MISSION_MAP"
# The failure sentence of the kind, before the error text of a failed record (ADOPT-29)
FAILED = "The mission map records could not be adopted."


class ChangeDuringQuestion(FakeTerminal):
    """A terminal whose "y" comes after a change, so the reviewed fingerprint is stale."""

    def __init__(self, change) -> None:
        """Keep the change to make while the question waits."""
        super().__init__("y\n")
        self.change = change

    def readline(self, *args: object) -> str:
        """Make the change, then answer yes."""
        self.change()
        return super().readline(*args)


class ExitTests(AdoptCliFixture):
    """Exit 3 for a stale review and a lane refusal, 6 and 1 after the commit point, 6 and 5 before the review."""

    def setUp(self) -> None:
        """A true move after one Apply."""
        super().setUp()
        self.case_move()

    def test_stale_review_exits_3_with_adopt_28(self) -> None:
        """A file change while the question waits: the kind line and ADOPT-28, nothing adopted."""
        before = self.load()
        stdin = ChangeDuringQuestion(lambda: self.write("mapgrouppos.xml", b"<admin-edit/>"))
        run = self.command(yes=False, stdin=stdin)
        self.assertEqual((run.code, run.stderr.splitlines()[-1]), (3, f"{FAILED} {ADOPTION_STALE_TEXT}"))
        self.assertEqual(self.load()["adoption"], before["adoption"])

    def test_lane_invalid_request_of_the_kind_exits_3(self) -> None:
        """AR-4: an INVALID_REQUEST of the kind before the commit point is a refusal of the state."""
        refused = ApplyRefused("adoption_refused", "Adoption is not possible.")
        with patch.object(mission_map_adoption, "build_adoption", side_effect=refused):
            run = self.command(json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["operation"]["kind"]), (3, "INVALID_REQUEST", KIND))

    def test_refusal_after_committing_exits_6_and_blocks_the_lane(self) -> None:
        """A refusal code raised at COMMITTING becomes RECOVERY_REQUIRED; the block belongs to the editor kind."""

        def refuse(phase: str, _index: int) -> None:
            """Raise a refusal code after the commit point."""
            if phase == "AFTER_COMMITTING":
                raise LifecycleFailure("CONTROL_CONFLICT", "refused after the commit point")
        self.faults = refuse
        run = self.command(json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["operation"]["state"]),
                         (6, "RECOVERY_REQUIRED", "RECOVERY_REQUIRED"))
        self.assertEqual({entry[1] for entry in self.lane._recovery_blocks._entries}, {"APPLY_MISSION_MAP"})

    def test_refusal_after_retirement_exits_1_with_the_kind_text(self) -> None:
        """PUBLICATION_FAILED: the kind line, then the Product Owner text of evidence 10.35."""

        def refuse(phase: str) -> None:
            """Raise a refusal code after the retired journal is in place."""
            if phase == "AFTER_RETIRE":
                raise LifecycleFailure("CONTROL_CONFLICT", "refused after the retirement")
        self.journals._retire_hook = refuse
        error = self.command(json_mode=True).json()["error"]
        self.assertEqual((error["code"], error["message"]), ("PUBLICATION_FAILED", f"{FAILED} {ADOPTION_COMMITTED_TEXT}"))
        self.assertEqual(self.adoption.commit_point(error["details"]["operation"]["operation_id"]), "retired")

    def test_recovery_block_exits_6_before_the_review(self) -> None:
        """The snapshot gate comes first (rule 5): no review read, nothing submitted."""
        self.block = "Mutations are blocked by unresolved mission map recovery."
        run = self.command(json_mode=True)
        self.assertEqual((run.code, run.json()["error"]["code"], run.calls),
                         (6, "RECOVERY_REQUIRED", ["get_application_snapshot"]))

    def test_ctrl_c_before_the_call_exits_5(self) -> None:
        """Ctrl+C before the first call exits 5, and nothing is sent (D15)."""
        interrupts = Interrupts()
        interrupts.handle(2, None)
        run = self.command(interrupts=interrupts)
        self.assertEqual((run.code, run.calls), (5, []))

    def test_progress_shows_only_the_kind_label(self) -> None:
        """PO-8: the waiter writes the running label of the kind, without a phase text."""
        run = self.command()
        progress = [line for line in run.stderr.splitlines() if KIND_TEXTS[KIND][0] in line]
        self.assertTrue(progress, run.stderr)
        for line in progress:
            self.assertRegex(line, rf"^\d\d:\d\d:\d\d {re.escape(KIND_TEXTS[KIND][0])}$")
        self.assertEqual(run.stderr.splitlines()[-1], KIND_TEXTS[KIND][1])


class RunnerTests(MapFixture):
    """The real command line: the profile rule, the help page and a refusal with nothing to adopt."""

    def test_command_line_help_and_refusal(self) -> None:
        """No profile exits 2; the fixture mission needs no adoption, so the review refuses with exit 3."""
        self.assertEqual(self.cli("map", "adopt")[0], 2)
        code, stdout, _stderr = self.cli("help", "map", "adopt")
        self.assertEqual(code, 0)
        self.assertIn("Usage: DayZ-ServerMan.py --cli map adopt [--profile ID-OR-NAME] [--json] [--yes]", stdout)
        code, stdout, stderr = self.cli("map", "adopt", "--profile", PROFILE_ID, "--yes")
        self.assertEqual((code, stdout, stderr.strip()), (3, "", NOTHING_TO_ADOPT_TEXT))
        code, stdout, _stderr = self.cli("map", "adopt", "--profile", PROFILE_ID, "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["details"]["reason"]), (3, "nothing_to_adopt"))


if __name__ == "__main__":
    unittest.main()
