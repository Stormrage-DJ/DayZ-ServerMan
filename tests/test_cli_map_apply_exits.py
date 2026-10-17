"""Leaf 10.5, D13: the exit codes of `map preview` and `map apply` for each refusal and failure, with their texts.

The Apply lane runs for real over the synthetic missions of `ApplyFixture`. The fault hook of the commit step
edits a file late or raises after the commit point (AR-4); the recording ports report a running server.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from map_publish_fixtures import EXCLUDE_U, INFECTED, OBJECTS, PublishCliFixture  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402

from dayz_serverman.application import mission_map_apply  # noqa: E402
from dayz_serverman.application.activity_wording import error_text  # noqa: E402
from dayz_serverman.application.mission_map_apply_choices import CHOICE_REQUIRED, ApplyRefused  # noqa: E402
from dayz_serverman.cli import map_wording  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerState  # noqa: E402
from dayz_serverman.domain.mission_map_families import NOT_VERIFIED_TEXT, FamilyNotVerified, family_gate  # noqa: E402
from dayz_serverman.domain.mission_map_preview_texts import (  # noqa: E402
    NO_SAVED_PLAN_TEXT, PREVIEW_STALE_TEXT, TARGET_CHANGED_TEXT,
)

# The failure sentence of the Apply kind, before the error text of a failed record
FAILED_LINE = "The mission map could not be applied."
# Product Owner 2026-10-16 17:17:52 in evidence 10.5: PUBLICATION_FAILED under APPLY_MISSION_MAP
COMMITTED_FAILURE = ("Apply reached its commit point, but a later step failed. The mission files can have changed. "
                     "Preview the changes again to see what is left to apply.")
UNVERIFIED_TEXT = NOT_VERIFIED_TEXT.format(family="deerisle")


class SaveDuringQuestion(FakeTerminal):
    """A terminal whose "y" comes after a new save of the plan, so the reviewed fingerprint is stale."""

    def __init__(self, change) -> None:
        """Keep the change to make while the question waits."""
        super().__init__("y\n")
        self.change = change

    def readline(self, *args: object) -> str:
        """Save the plan again, then answer yes."""
        self.change()
        return super().readline(*args)


class StateExitTests(PublishCliFixture):
    """Refusals of the current state exit 3; a recovery block exits 6; Ctrl+C before the call exits 5."""

    def test_running_server_exits_3_and_changes_nothing(self) -> None:
        """The lane refusal of a running server keeps its class: no checkpoint ran (PRE_CHANGE_PHASES)."""
        self.save(OBJECTS, 25)
        before = self.stores()
        self.ports.state = ServerState.RUNNING_MANAGED
        run = self.command("apply", *EXCLUDE_U, json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["operation"]["state"]),
                         (3, "CONTROL_CONFLICT", "FAILED"))
        run = self.command("apply", *EXCLUDE_U)
        self.assertEqual(run.code, 3)
        self.assertEqual(run.stderr.splitlines()[-1],
                         f"{FAILED_LINE} This cannot be done while the server is running.")
        self.assertEqual(self.stores(), before)

    def test_unverified_family_exits_3_with_the_6_11_text(self) -> None:
        """Before the question on the review (nothing submitted), and in the lane when the gate refuses there."""
        self.save([INFECTED], identifier="other")
        run = self.command("apply", profile="other", json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["message"], error["details"]["reason"]),
                         (3, "UNSUPPORTED_SNAPSHOT_CONTENT", UNVERIFIED_TEXT, "family_not_verified"))
        self.assertNotIn("apply_mission_map", run.calls)
        lines = self.command("preview", profile="other").stdout.splitlines()
        self.assertEqual(lines[1:3], ["Apply is not possible", "  " + UNVERIFIED_TEXT])
        self.save(OBJECTS, 25)
        refusal = FamilyNotVerified(family_gate("deerisle"), "apply")
        with patch.object(mission_map_apply, "require_verified_family", side_effect=refusal):
            run = self.command("apply", *EXCLUDE_U)
        self.assertEqual((run.code, run.stderr.splitlines()[-1]), (3, f"{FAILED_LINE} {UNVERIFIED_TEXT}"))

    def test_stale_review_exits_3_with_the_stale_text(self) -> None:
        """A save while the question waits makes the lane refuse the reviewed fingerprint."""
        self.save(OBJECTS, 25)
        stdin = SaveDuringQuestion(lambda: self.save(OBJECTS, 50, expected=0))
        run = self.command("apply", *EXCLUDE_U, yes=False, stdin=stdin)
        self.assertEqual((run.code, run.stderr.splitlines()[-1]), (3, f"{FAILED_LINE} {PREVIEW_STALE_TEXT}"))

    def test_no_saved_plan_and_invalid_saved_data_exit_3(self) -> None:
        """No plan: the 8.7 text of the backend. Invalid saved data: the window text and its CLI line."""
        for name in ("preview", "apply"):
            with self.subTest(command=name):
                run = self.command(name)
                self.assertEqual((run.code, run.stderr.strip()), (3, NO_SAVED_PLAN_TEXT))
        self.save(OBJECTS)
        next(self.area.rglob("plan.json")).write_text("{", encoding="utf-8")
        expected = f"{map_wording.READ_ONLY_TEXTS['invalid_saved_data']} Run map set-aside."
        # Evidence 10.5: `map apply` adds "Nothing was changed."; `map preview` changes nothing by design
        for name, text in (("preview", expected), ("apply", f"{expected} Nothing was changed.")):
            with self.subTest(command=name):
                run = self.command(name, json_mode=True)
                error = run.json()["error"]
                self.assertEqual((run.code, error["code"], error["message"]), (3, "REVISION_CONFLICT", text))

    def test_lane_invalid_request_of_the_kind_exits_3(self) -> None:
        """AR-4: an INVALID_REQUEST of APPLY_MISSION_MAP before the commit point is a refusal of the state."""
        self.save(OBJECTS, 25)
        refused = ApplyRefused(CHOICE_REQUIRED, "Apply needs a choice for each mission file that changed outside "
                                                "the map editor: keep the change or replace the file.")
        with patch.object(mission_map_apply, "check_choices", side_effect=refused):
            run = self.command("apply", *EXCLUDE_U, json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["operation"]["kind"]),
                         (3, "INVALID_REQUEST", "APPLY_MISSION_MAP"))

    def test_recovery_block_exits_6_before_the_review(self) -> None:
        """The snapshot gate comes first (rule 5): no review read, nothing submitted."""
        self.save(OBJECTS, 25)
        self.block = "Mutations are blocked by unresolved mission map recovery."
        run = self.command("apply", *EXCLUDE_U, json_mode=True)
        self.assertEqual((run.code, run.json()["error"]["code"], run.calls),
                         (6, "RECOVERY_REQUIRED", ["get_application_snapshot"]))

    def test_ctrl_c_before_the_call_exits_5(self) -> None:
        """Ctrl+C before the preview call exits 5, and nothing is sent (D15)."""
        self.save(OBJECTS, 25)
        for name in ("preview", "apply"):
            with self.subTest(command=name):
                interrupts = Interrupts()
                interrupts.handle(2, None)
                run = self.command(name, interrupts=interrupts)
                self.assertEqual(run.code, 5)
                self.assertNotIn("preview_mission_map", run.calls)


class LateExitTests(PublishCliFixture):
    """A late file change keeps exit 3 with the 7c text; a refusal after COMMITTING exits 6 or 1 (AR-4)."""

    def test_late_file_change_prints_the_7c_text_with_the_file(self) -> None:
        """The commit rolls back; the CLI shows the kind line and the host text that names the file."""
        self.save(OBJECTS, 25)
        dirt = self.mission / "mapgroupdirt.xml"
        edited = dirt.read_bytes() + b"<!-- admin -->"

        def edit(phase: str, _index: int) -> None:
            """Edit mapgroupdirt.xml after the journal is durable."""
            if phase == "AFTER_PREPARED":
                dirt.write_bytes(edited)
        self.fault = edit
        run = self.command("apply", *EXCLUDE_U)
        late = TARGET_CHANGED_TEXT.format(path="mapgroupdirt.xml")
        self.assertEqual((run.code, run.stderr.splitlines()[-1]), (3, f"{FAILED_LINE} {late}"))
        self.assertEqual(dirt.read_bytes(), edited)
        self.assertIsNone(self.service.commit_point("apply-none"))

    def test_refusal_after_committing_exits_6_and_blocks_the_lane(self) -> None:
        """A refusal code raised at COMMITTING becomes RECOVERY_REQUIRED, so the record never claims no change."""
        self.save(OBJECTS, 25)

        def refuse(phase: str, _index: int) -> None:
            """Raise a refusal code after the commit point."""
            if phase == "AFTER_COMMITTING":
                raise LifecycleFailure("CONTROL_CONFLICT", "refused after the commit point")
        self.fault = refuse
        run = self.command("apply", *EXCLUDE_U, json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["operation"]["state"]),
                         (6, "RECOVERY_REQUIRED", "RECOVERY_REQUIRED"))
        self.assertTrue(self.lane._recovery_blocks._entries)

    def test_refusal_after_retirement_exits_1(self) -> None:
        """A refusal code after the journal was retired as committed becomes PUBLICATION_FAILED (exit 1)."""
        self.save(OBJECTS, 25)

        def refuse(phase: str) -> None:
            """Raise a refusal code after the retired journal is in place."""
            if phase == "AFTER_RETIRE":
                raise LifecycleFailure("CONTROL_CONFLICT", "refused after the retirement")
        self.journals._retire_hook = refuse
        run = self.command("apply", *EXCLUDE_U, json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"]), (1, "PUBLICATION_FAILED"))
        operation = error["details"]["operation"]["operation_id"]
        self.assertEqual(self.service.commit_point(operation), "retired")
        self.assertEqual(error["message"], f"{FAILED_LINE} {COMMITTED_FAILURE}")

    def test_the_committed_failure_text_is_in_both_catalogues(self) -> None:
        """Evidence 10.5: PUBLICATION_FAILED under APPLY_MISSION_MAP has its kind text, never the mods text."""
        self.assertEqual(error_text("PUBLICATION_FAILED", "host text", kind="APPLY_MISSION_MAP"), COMMITTED_FAILURE)
        self.assertNotEqual(error_text("PUBLICATION_FAILED", "host text"), COMMITTED_FAILURE)
        script = (Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend" /
                  "operation_messages.js").read_text(encoding="utf-8")
        own = script.split("const operationKindErrorTexts = Object.freeze({", 1)[1].split("\n});", 1)[0]
        apply_rows = own.split("APPLY_MISSION_MAP: Object.freeze({", 1)[1].split("}),", 1)[0]
        self.assertIn(f'PUBLICATION_FAILED: "{COMMITTED_FAILURE}",', apply_rows)

    def test_progress_shows_only_the_kind_label(self) -> None:
        """PO-8: the waiter writes the running label of the kind, without a phase text."""
        self.save(OBJECTS, 25)
        run = self.command("apply", *EXCLUDE_U)
        progress = [line for line in run.stderr.splitlines() if "Applying mission map" in line]
        self.assertTrue(progress, run.stderr)
        for line in progress:
            self.assertRegex(line, r"^\d\d:\d\d:\d\d Applying mission map$")
        self.assertEqual(run.stderr.splitlines()[-1], "Mission map applied.")


if __name__ == "__main__":
    unittest.main()
