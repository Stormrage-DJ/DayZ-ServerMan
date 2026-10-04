"""Additive operation record fields: the target profile and the last working phase."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import (  # noqa: E402
    OperationFailure,
    OperationRecord,
    OperationState,
    TERMINAL_STATES,
    utc_now,
)
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.bridge.contracts import CONTRACT_VERSION  # noqa: E402

APPLICATION = ROOT / "runnable" / "src" / "python" / "dayz_serverman" / "application"
# Submit sites that know the profile, and how many submit calls each holds
PROFILE_SUBMITTERS = {
    "lifecycle_coordinator.py": 1, "backup_coordinator.py": 1, "restore_coordinator.py": 1,
    "configuration_coordinator.py": 1, "mission_configuration_coordinator.py": 2,
    "medical_feature_coordinator.py": 1, "mod_publication_coordinator.py": 1,
    "workshop_verification_coordinator.py": 1, "profile_coordinator.py": 1,
}
# Submit sites whose operation belongs to no single known profile
GLOBAL_SUBMITTERS = (
    "coordinator.py", "legacy_backup_coordinator.py", "migration_coordinator.py",
    "profile_restore_coordinator.py", "profile_provisioning_coordinator.py",
)


class OperationRecordFieldTests(unittest.TestCase):
    """Design section 12.2: both fields are additive, stable, and reach the wire."""

    def setUp(self) -> None:
        """Create a manager over a temporary operations root."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_record_fields_")
        self.root = Path(self.temporary.name) / "operations"
        self.manager = OperationManager(OperationStore(self.root), queue_limit=4)

    def tearDown(self) -> None:
        """Drain the lane and remove the temporary root."""
        self.manager.shutdown(2)
        self.temporary.cleanup()

    def finished(self, operation_id: str) -> OperationRecord:
        """Wait for a terminal state and return the record."""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            record = self.manager.get(operation_id)
            if record.state in TERMINAL_STATES:
                return record
            time.sleep(0.01)
        raise AssertionError("operation did not finish")

    def test_target_profile_is_stored_at_submit_and_never_changes(self) -> None:
        """The submitted profile is on the accepted, the terminal, and the stored record."""
        accepted = self.manager.submit(
            "SAVE_PROFILE", lambda context: context.checkpoint("stage", 10),
            target_profile_id="alpha",
        )
        self.assertEqual(accepted.target_profile_id, "alpha")
        terminal = self.finished(accepted.operation_id)
        self.assertEqual(terminal.to_dict()["target_profile_id"], "alpha")
        self.assertEqual(terminal.snapshot().target_profile_id, "alpha")
        stored = json.loads((self.root / f"{accepted.operation_id}.json").read_text("utf-8"))
        self.assertEqual(stored["target_profile_id"], "alpha")

    def test_target_profile_is_null_when_the_submitter_names_none(self) -> None:
        """An operation without a profile carries an explicit null."""
        accepted = self.manager.submit("SAVE_SETTINGS", lambda _context: None)
        value = self.finished(accepted.operation_id).to_dict()
        self.assertIn("target_profile_id", value)
        self.assertIsNone(value["target_profile_id"])

    def test_last_working_phase_survives_success(self) -> None:
        """The last checkpoint phase stays when the phase becomes complete."""
        def work(context) -> None:
            """Report two working phases."""
            context.checkpoint("BACKUP_STAGE", 40)
            context.checkpoint("START_SERVER", 98)

        record = self.finished(self.manager.submit("RESTART_SERVER", work).operation_id)
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(record.progress_phase, "complete")
        self.assertEqual(record.to_dict()["last_working_phase"], "START_SERVER")

    def test_last_working_phase_survives_failure_and_recovery(self) -> None:
        """A failed and a recovery-required record keep the phase that failed."""
        for recovery, state in ((False, OperationState.FAILED), (True, OperationState.RECOVERY_REQUIRED)):
            manager = OperationManager(OperationStore(self.root / state.value), queue_limit=2)

            def work(context, recovery=recovery) -> None:
                """Fail after one working phase."""
                context.checkpoint("BACKUP_HASH", 55)
                raise OperationFailure("STORAGE_FAILURE", "failed", recovery_required=recovery)

            operation_id = manager.submit("STOP_SERVER", work).operation_id
            deadline = time.monotonic() + 2.0
            while manager.get(operation_id).state not in TERMINAL_STATES and time.monotonic() < deadline:
                time.sleep(0.01)
            record = manager.get(operation_id)
            manager.shutdown(2)
            self.assertEqual(record.state, state)
            self.assertEqual(record.progress_phase, "failed")
            self.assertEqual(record.last_working_phase, "BACKUP_HASH")

    def test_last_working_phase_survives_cancellation_and_stays_null_without_checkpoint(self) -> None:
        """A cancelled record keeps its safe point; work without a checkpoint has null."""
        entered, release = threading.Event(), threading.Event()

        def work(context) -> None:
            """Wait inside the first phase, then reach the safe point again."""
            context.checkpoint("verify_source", 10)
            entered.set()
            release.wait(2)
            context.checkpoint("verify_source", 20)

        running = self.manager.submit("VERIFY_WORKSHOP_FILES", work, safe_points=frozenset({"verify_source"}))
        queued = self.manager.submit("SAVE_SETTINGS", lambda _context: None)
        self.assertTrue(entered.wait(2))
        self.manager.request_cancellation(queued.operation_id)
        self.manager.request_cancellation(running.operation_id)
        self.assertEqual(self.manager.get(running.operation_id).last_working_phase, "verify_source")
        release.set()
        cancelled = self.finished(running.operation_id)
        self.assertEqual(cancelled.state, OperationState.CANCELLED)
        self.assertEqual(cancelled.progress_phase, "cancelled")
        self.assertEqual(cancelled.last_working_phase, "verify_source")
        self.assertIsNone(self.manager.get(queued.operation_id).to_dict()["last_working_phase"])

    def test_fields_are_additive_and_the_contract_version_is_unchanged(self) -> None:
        """The record keeps every earlier field, gains two, and the versions stay 1."""
        value = OperationRecord("op", "KIND", OperationState.ACCEPTED, utc_now()).to_dict()
        self.assertEqual(set(value), {
            "schema_version", "revision", "operation_id", "kind", "state", "accepted_at",
            "started_at", "finished_at", "cancellation_requested", "progress_percent",
            "progress_phase", "result", "terminal_error", "progress_detail",
            "target_profile_id", "last_working_phase",
        })
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(CONTRACT_VERSION, 1)

    def test_each_submitter_that_knows_the_profile_passes_it(self) -> None:
        """Every profile-bound submit call names the profile; the global ones name none."""
        for name, count in PROFILE_SUBMITTERS.items():
            source = (APPLICATION / name).read_text(encoding="utf-8")
            calls = re.findall(r"_operations\.submit\((?:[^()]|\([^()]*\)|\((?:[^()]|\([^()]*\))*\))*\)",
                               source)
            self.assertEqual(len(calls), count, name)
            for call in calls:
                self.assertIn("target_profile_id=", call, name)
        # The Workshop coordinator forwards options; only the profile-bound update names a profile
        workshop = (APPLICATION / "workshop_coordinator.py").read_text(encoding="utf-8")
        self.assertEqual(workshop.count("target_profile_id=profile_id"), 1)
        update = workshop.split('"UPDATE_WORKSHOP_ITEMS"', 1)[1].split("def _submit", 1)[0]
        self.assertIn("target_profile_id=profile_id", update)
        for name in GLOBAL_SUBMITTERS:
            self.assertNotIn("target_profile_id", (APPLICATION / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
