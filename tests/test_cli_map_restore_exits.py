"""Leaf 10.32, D13 and AR-4: the exit codes of `map restore` for each refusal and failure, with their texts.

The restore lane runs for real over the synthetic missions of `ApplyFixture`. The fault hook of the commit step
edits a file late or raises after the commit point; the recording ports report a running server. The 8.8 texts
print unchanged, with the existing exit codes.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from map_restore_fixtures import RestoreCliFixture  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402

from dayz_serverman.application import mission_map_restore  # noqa: E402
from dayz_serverman.application.activity_wording import KIND_TEXTS, error_text  # noqa: E402
from dayz_serverman.application.mission_map_restore_review import RestoreRefused  # noqa: E402
from dayz_serverman.cli import map_restore_wording as words  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerState  # noqa: E402
from dayz_serverman.domain.mission_map_families import FamilyNotVerified, family_gate  # noqa: E402
from dayz_serverman.domain.mission_map_restore_texts import (  # noqa: E402
    LANE_KINDS, NO_PREVIOUS_STATE, NO_PREVIOUS_STATE_TEXT, RESTORE_STALE_TEXT, TARGET_CHANGED_TEXT,
)

# The failure sentence of each restore kind, before the error text of a failed record
FAILED = {mode: KIND_TEXTS[kind][2] for mode, kind in LANE_KINDS.items()}
# Product Owner 2026-10-17 02:14:51 in evidence 10.32: PUBLICATION_FAILED under both restore kinds
COMMITTED_FAILURE = ("The restore reached its commit point, but a later step failed. The mission files can have "
                     "changed. Preview the restore again to see what is left to restore.")
# The block reason of a running server under the editor kind (6c); Product Owner 2026-10-17 16:18:22 in evidence 8.12
NOT_STOPPED = ("A mission map change did not finish and must be checked. This is possible only while the server is "
               "stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then select Check "
               "again in the map editor, or restart DayZ-ServerMan.")


class ChangeDuringQuestion(FakeTerminal):
    """A terminal whose "y" comes after a change of a mission file, so the reviewed fingerprint is stale."""

    def __init__(self, change) -> None:
        """Keep the change to make while the question waits."""
        super().__init__("y\n")
        self.change = change

    def readline(self, *args: object) -> str:
        """Make the change, then answer yes."""
        self.change()
        return super().readline(*args)


class StateExitTests(RestoreCliFixture):
    """Refusals of the current state exit 3; a recovery block exits 6; Ctrl+C before the call exits 5."""

    def setUp(self) -> None:
        """Apply the plan once, so both modes have something to restore."""
        super().setUp()
        self.applied()

    def test_running_server_exits_3_and_changes_nothing(self) -> None:
        """The lane refusal keeps its class in both modes: no checkpoint ran (PRE_CHANGE_PHASES)."""
        before = self.stores()
        self.ports.state = ServerState.RUNNING_MANAGED
        for mode in LANE_KINDS:
            with self.subTest(mode=mode):
                error = self.restore(mode, json_mode=True).json()["error"]
                self.assertEqual((error["code"], error["details"]["operation"]["state"]), ("CONTROL_CONFLICT", "FAILED"))
                run = self.restore(mode)
                self.assertEqual((run.code, run.stderr.splitlines()[-1]),
                                 (3, f"{FAILED[mode]} This cannot be done while the server is running."))
        self.assertEqual(self.stores(), before)

    def test_stale_review_exits_3_with_the_8_8_stale_text(self) -> None:
        """A file change while the question waits makes the lane refuse the reviewed fingerprint."""
        before = self.mission_bytes()
        run = self.restore("previous", yes=False, stdin=ChangeDuringQuestion(self.edit_positions))
        self.assertEqual((run.code, run.stderr.splitlines()[-1]), (3, f"{FAILED['previous']} {RESTORE_STALE_TEXT}"))
        self.assertEqual(self.mission_bytes()["mapgroupproto.xml"], before["mapgroupproto.xml"])

    def test_late_file_change_prints_the_host_text_with_the_file(self) -> None:
        """The commit rolls back; the CLI shows the kind line and the 8.8 text that names the file, exit 3."""
        dirt = self.mission / "mapgroupdirt.xml"
        edited = dirt.read_bytes() + b"<!-- admin -->"

        def edit(phase: str, _index: int) -> None:
            """Edit mapgroupdirt.xml after the journal is durable."""
            if phase == "AFTER_PREPARED":
                dirt.write_bytes(edited)
        self.fault = edit
        run = self.restore("previous", json_mode=True)
        error = run.json()["error"]
        late = TARGET_CHANGED_TEXT.format(path="mapgroupdirt.xml")
        self.assertEqual((run.code, error["code"], error["message"]),
                         (3, "REVISION_CONFLICT", f"{FAILED['previous']} {late}"))
        self.assertEqual(dirt.read_bytes(), edited)

    def test_lane_refusals_of_both_kinds_exit_3(self) -> None:
        """AR-4: a lane INVALID_REQUEST before the commit point and the 6.11 gate in the lane are state refusals."""
        refused = RestoreRefused(NO_PREVIOUS_STATE, NO_PREVIOUS_STATE_TEXT)
        with patch.object(mission_map_restore, "build_restore", side_effect=refused):
            for mode in LANE_KINDS:
                with self.subTest(mode=mode):
                    error = self.restore(mode, json_mode=True).json()["error"]
                    self.assertEqual((error["code"], error["details"]["operation"]["kind"]),
                                     ("INVALID_REQUEST", LANE_KINDS[mode]))
                    self.assertEqual(self.restore(mode).code, 3)
        gate = FamilyNotVerified(family_gate("deerisle"), "restore_original")
        with patch.object(mission_map_restore, "require_verified_family", side_effect=gate):
            run = self.restore("original")
        self.assertEqual((run.code, run.stderr.splitlines()[-1]), (3, f"{FAILED['original']} {gate}"))

    def test_recovery_block_exits_6_before_the_review(self) -> None:
        """The snapshot gate comes first (rule 5): no review read, nothing submitted."""
        self.block = "Mutations are blocked by unresolved mission map recovery."
        run = self.restore("previous", json_mode=True)
        self.assertEqual((run.code, run.json()["error"]["code"], run.calls),
                         (6, "RECOVERY_REQUIRED", ["get_application_snapshot"]))

    def test_ctrl_c_before_the_call_exits_5(self) -> None:
        """Ctrl+C before the first call exits 5, and nothing is sent (D15)."""
        interrupts = Interrupts()
        interrupts.handle(2, None)
        run = self.restore("original", interrupts=interrupts)
        self.assertEqual((run.code, run.calls), (5, []))

    def test_progress_shows_only_the_kind_label(self) -> None:
        """PO-8: the waiter writes the running label of the kind, without a phase text."""
        for mode, label in (("previous", "Restoring previous mission map"), ("original", "Restoring original mission map")):
            run = self.restore(mode)
            progress = [line for line in run.stderr.splitlines() if label in line]
            self.assertTrue(progress, run.stderr)
            for line in progress:
                self.assertRegex(line, rf"^\d\d:\d\d:\d\d {re.escape(label)}$")
            self.assertEqual(run.stderr.splitlines()[-1], KIND_TEXTS[LANE_KINDS[mode]][1])

    def mission_bytes(self) -> dict[str, bytes]:
        """Return the bytes of each file of the verified mission."""
        return {path.name: path.read_bytes() for path in self.mission.iterdir() if path.is_file()}


class LateExitTests(RestoreCliFixture):
    """A refusal code after COMMITTING exits 6 or 1 (AR-4); `map recover` then finishes the restore."""

    def setUp(self) -> None:
        """Apply the plan once."""
        super().setUp()
        self.applied()

    def test_refusal_after_committing_exits_6_and_map_recover_finishes_it(self) -> None:
        """RECOVERY_REQUIRED blocks the lane; recover exits 3 while the server runs, then 0, and the lane is free."""

        def refuse(phase: str, _index: int) -> None:
            """Raise a refusal code after the commit point."""
            if phase == "AFTER_COMMITTING":
                raise LifecycleFailure("CONTROL_CONFLICT", "refused after the commit point")
        self.fault = refuse
        run = self.restore("previous", json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["details"]["operation"]["state"]),
                         (6, "RECOVERY_REQUIRED", "RECOVERY_REQUIRED"))
        self.assertTrue(self.lane._recovery_blocks._entries)
        self.assertEqual(self.restorer.commit_point(error["details"]["operation"]["operation_id"]), "active")
        self.fault = None
        self.ports.state = ServerState.RUNNING_MANAGED
        run = self.recover(json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"], error["message"]), (3, "CONTROL_CONFLICT", NOT_STOPPED))
        self.ports.state = ServerState.STOPPED
        run = self.recover()
        self.assertEqual((run.code, run.stdout.strip()), (0, words.CLEARED))
        self.assertFalse(self.lane._recovery_blocks._entries)
        self.assertEqual(self.records()[2][0]["operation_kind"], "restore_previous")

    def test_refusal_after_retirement_exits_1(self) -> None:
        """A refusal code after the journal was retired as committed becomes PUBLICATION_FAILED (exit 1)."""

        def refuse(phase: str) -> None:
            """Raise a refusal code after the retired journal is in place."""
            if phase == "AFTER_RETIRE":
                raise LifecycleFailure("CONTROL_CONFLICT", "refused after the retirement")
        self.journals._retire_hook = refuse
        run = self.restore("original", json_mode=True)
        error = run.json()["error"]
        self.assertEqual((run.code, error["code"]), (1, "PUBLICATION_FAILED"))
        self.assertEqual(self.restorer.commit_point(error["details"]["operation"]["operation_id"]), "retired")
        self.assertEqual(error["message"], f"{FAILED['original']} {COMMITTED_FAILURE}")

    def test_the_committed_failure_text_is_in_both_catalogues_for_both_kinds(self) -> None:
        """Evidence 10.32: PUBLICATION_FAILED under each restore kind has its kind text, never the mods text."""
        script = (Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend" /
                  "operation_messages.js").read_text(encoding="utf-8")
        own = script.split("const operationKindErrorTexts = Object.freeze({", 1)[1].split("\n});", 1)[0]
        for kind in LANE_KINDS.values():
            with self.subTest(kind=kind):
                self.assertEqual(error_text("PUBLICATION_FAILED", "host text", kind=kind), COMMITTED_FAILURE)
                rows = own.split(f"{kind}: Object.freeze({{", 1)[1].split("}),", 1)[0]
                self.assertIn(f'PUBLICATION_FAILED: "{COMMITTED_FAILURE}",', rows)
        self.assertNotEqual(error_text("PUBLICATION_FAILED", "host text"), COMMITTED_FAILURE)

    def test_no_journal_has_no_commit_point(self) -> None:
        """Without a journal of the operation the failure keeps its code."""
        self.assertIsNone(self.restorer.commit_point("restore-none"))


if __name__ == "__main__":
    unittest.main()
