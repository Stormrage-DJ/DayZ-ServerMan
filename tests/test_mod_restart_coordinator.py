"""Apply mods and restart: the coordinator orders stop, backup and publication, and nothing else."""
from __future__ import annotations

import ast
import inspect
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application import mod_restart_coordinator  # noqa: E402
from dayz_serverman.application.activity_wording import restart_apply_text  # noqa: E402
from dayz_serverman.application.backups import BackupStorageError  # noqa: E402
from dayz_serverman.application.mod_publication import ModPublicationError  # noqa: E402
from dayz_serverman.application.mod_restart_coordinator import ModRestartCoordinator  # noqa: E402
from dayz_serverman.application.lifecycle_coordinator import (  # noqa: E402
    OTHER_PROFILE_RUNNING,
    for_running_profile,
)
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.bridge.facade import ApplicationCallError  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure  # noqa: E402

# Phases that the fake publication reports, in the order of a real apply with a start
PUBLICATION_PHASES = ("PUBLICATION_PREFLIGHT", "STAGE_TARGET", "BEFORE_PUBLICATION",
                      "VERIFY_BEFORE_START", "START_SERVER")
# Phases that the fake backup reports
BACKUP_PHASES = ("DISCOVER", "STAGE", "HASH", "WRITE_MANIFEST", "VERIFY", "PUBLISH")


class _Services:
    """Fake lifecycle, backup and publication services that share one call log."""

    def __init__(self) -> None:
        """Start with an empty log, a writing plan and no failure."""
        self.calls: list[str] = []
        self.phases: list[tuple[str, int]] = []
        self.writes = True
        self.fail: dict[str, Exception] = {}
        self.cancel_at: str | None = None
        self.manager: OperationManager | None = None
        self.operation_id: str | None = None
        # Set when the test knows the operation identifier
        self.submitted = threading.Event()
        self.start_state = "STARTED"
        # Profile that the fake server runs with; None when it is not known
        self.running_profile: str | None = None

    def _step(self, name: str) -> None:
        """Log the call and raise the failure that the test planned for it."""
        self.calls.append(name)
        if name in self.fail:
            raise self.fail[name]

    def _checkpoint(self, context_checkpoint, phase: str, percent: int, reported: str) -> None:
        """Request the planned cancellation, then report the phase."""
        if reported == self.cancel_at:
            self.manager.request_cancellation(self.operation_id)
        context_checkpoint(phase, percent)

    # Publication service
    def confirm_restart_plan(self, request, fingerprint) -> bool:
        """Log the plan check and return whether the plan writes."""
        self._step("confirm")
        if self.cancel_at == "confirm":
            # The operator cancels while the plan check runs
            self.submitted.wait(5)
            self.manager.request_cancellation(self.operation_id)
        return self.writes

    def publish(self, request, fingerprint, context) -> dict[str, object]:
        """Report the publication phases through the given context and return a result."""
        self._step("publish")
        for index, phase in enumerate(PUBLICATION_PHASES):
            if phase in self.fail:
                context.checkpoint(phase, 90 if index == 3 else 96 if index == 4 else index * 25 + 5)
                raise self.fail[phase]
            self._checkpoint(context.checkpoint, phase,
                             90 if index == 3 else 96 if index == 4 else index * 25 + 5, phase)
        return {"profile_id": request.profile_id, "publication_state": "VERIFIED",
                "start_state": self.start_state, "prestart_check": {"hashed": 1, "fingerprint_accepted": 2}}

    # Lifecycle service
    def status(self) -> SimpleNamespace:
        """Report the profile that the server runs with."""
        return SimpleNamespace(profile_id=self.running_profile)

    def stop(self, settings_revision: int, *, before_change=None) -> None:
        """Log the stop."""
        self._step("stop")

    # Backup service
    def create(self, profile_id, profile_revision, settings_revision, checkpoint) -> dict[str, object]:
        """Report the backup phases and return a backup summary."""
        self._step("backup")
        for index, phase in enumerate(BACKUP_PHASES):
            self._checkpoint(checkpoint, phase, index * 20, f"BACKUP_{phase}")
        return {"backup_id": "backup-1"}


class ModRestartCoordinatorTests(unittest.TestCase):
    """One lane operation: plan check, stop, optional backup, guarded apply with its start."""

    def setUp(self) -> None:
        """Create an operation lane and the coordinator over the fake services."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = OperationManager(OperationStore(Path(self.temporary.name)))
        self.addCleanup(self.manager.shutdown, 3)
        self.services = _Services()
        self.services.manager = self.manager
        self.coordinator = ModRestartCoordinator(
            self.services, self.services, self.services, self.manager)
        self.apply = self.coordinator.apply

    def run_restart(self, backup: bool = False):
        """Submit the restart and return the terminal operation record."""
        accepted = self.apply({
            "profile_id": "main", "expected_profile_revision": 1,
            "expected_semantic_profile_digest": "a" * 64, "expected_settings_revision": 2,
            "update_operation_id": "update-1", "publication_fingerprint": "b" * 64,
            "backup_after_stop": backup})
        self.services.operation_id = accepted["operation_id"]
        self.services.submitted.set()
        for _ in range(500):
            record = self.manager.get(accepted["operation_id"])
            if record.state.value in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
                return record
            time.sleep(0.01)
        self.fail("operation did not finish")

    def test_request_for_another_profile_than_the_running_one_is_refused(self) -> None:
        """D11: nothing is queued, checked or stopped for a profile that is not the running one."""
        self.services.running_profile = "other"
        # The composition puts the same check in front of this call as in front of stop and restart
        self.apply = for_running_profile(self.services, self.coordinator.apply)
        self.assertIn("for_running_profile(lifecycle, handler)", inspect.getsource(build_composition))
        with self.assertRaises(ApplicationCallError) as raised:
            self.run_restart(backup=True)
        self.assertEqual(raised.exception.code.value, "INVALID_REQUEST")
        self.assertEqual(raised.exception.safe_message, OTHER_PROFILE_RUNNING)
        self.assertEqual(self.services.calls, [])
        self.assertEqual(self.manager.list_recent(), ())
        # The running profile itself, and a server whose profile is not known, pass as before
        for running in ("main", None):
            self.services.running_profile = running
            self.services.calls.clear()
            self.assertEqual(self.run_restart().state.value, "SUCCEEDED")
            self.assertEqual(self.services.calls, ["confirm", "stop", "publish"])

    def test_success_runs_the_steps_in_order(self) -> None:
        """Plan check, stop, apply; the result is the publication result plus the backup."""
        record = self.run_restart()
        self.assertEqual(self.services.calls, ["confirm", "stop", "publish"])
        self.assertEqual((record.state.value, record.kind, record.target_profile_id),
                         ("SUCCEEDED", "APPLY_MODS_AND_RESTART", "main"))
        self.assertEqual((record.result["start_state"], record.result["backup"]), ("STARTED", None))
        self.assertEqual(record.last_working_phase, "START_SERVER")

    def test_backup_runs_between_stop_and_apply(self) -> None:
        """With the backup flag the backup follows the stop and its summary is in the result."""
        record = self.run_restart(backup=True)
        self.assertEqual(self.services.calls, ["confirm", "stop", "backup", "publish"])
        self.assertEqual(record.result["backup"], {"backup_id": "backup-1"})

    def test_refused_plan_stops_nothing(self) -> None:
        """A stale review fails the operation before the stop."""
        self.services.fail["confirm"] = ModPublicationError("PUBLICATION_PREVIEW_STALE", "Publication preview changed.")
        record = self.run_restart(backup=True)
        self.assertEqual(self.services.calls, ["confirm"])
        self.assertEqual((record.state.value, record.terminal_error.code, record.last_working_phase),
                         ("FAILED", "PUBLICATION_PREVIEW_STALE", "preflight"))

    def test_nothing_to_apply_neither_stops_nor_restarts(self) -> None:
        """A plan that writes nothing ends the operation without a stop."""
        self.services.writes = False
        record = self.run_restart(backup=True)
        self.assertEqual(self.services.calls, ["confirm"])
        self.assertEqual(record.state.value, "SUCCEEDED")
        self.assertEqual(record.result, {
            "profile_id": "main", "update_operation_id": "update-1", "publication_state": "UNCHANGED",
            "start_requested": True, "start_authorized": False, "start_state": "NOT_NEEDED",
            "start_error": None, "prestart_check": None, "backup": None})

    def test_each_failure_point_ends_the_sequence_there(self) -> None:
        """Stop, backup, apply and pre-start check failures: no later step runs."""
        cases = (
            ("stop", LifecycleFailure("STOP_TIMEOUT", "DayZ did not stop."), True,
             ["confirm", "stop"], "STOP_TIMEOUT", "STOP_SERVER", "FAILED"),
            ("backup", BackupStorageError("STORAGE_FAILURE", "Backup storage failed."), True,
             ["confirm", "stop", "backup"], "STORAGE_FAILURE", "BACKUP_DISCOVER", "FAILED"),
            ("STAGE_TARGET", ModPublicationError("PUBLICATION_FAILED", "Publication storage failed."), False,
             ["confirm", "stop", "publish"], "PUBLICATION_FAILED", "STAGE_TARGET", "FAILED"),
            ("STAGE_TARGET", ModPublicationError("RECOVERY_REQUIRED", "not proven", recovery_required=True), False,
             ["confirm", "stop", "publish"], "RECOVERY_REQUIRED", "STAGE_TARGET", "RECOVERY_REQUIRED"),
            ("publish", ModPublicationError("CONTROL_CONFLICT", "Applying mods requires STOPPED; current state is STARTING."),
             False, ["confirm", "stop", "publish"], "CONTROL_CONFLICT", "STOP_SERVER", "FAILED"),
            ("VERIFY_BEFORE_START", ModPublicationError("PUBLICATION_VERIFICATION_FAILED", "changed"), False,
             ["confirm", "stop", "publish"], "PUBLICATION_FAILED", "VERIFY_BEFORE_START", "FAILED"),
        )
        for point, error, backup, calls, code, phase, state in cases:
            with self.subTest(point=point, code=code):
                self.setUp()
                self.services.fail[point] = error
                record = self.run_restart(backup=backup)
                self.assertEqual(self.services.calls, calls)
                self.assertEqual((record.state.value, record.terminal_error.code, record.last_working_phase),
                                 (state, code, phase))

    def test_failed_start_keeps_the_applied_result(self) -> None:
        """A start that fails is a result of the publication, not a failure of the operation."""
        self.services.start_state = "FAILED"
        record = self.run_restart()
        self.assertEqual((record.state.value, record.result["start_state"]), ("SUCCEEDED", "FAILED"))

    def test_cancellation_stops_at_each_safe_point_and_nowhere_else(self) -> None:
        """Backup and publication safe points cancel; the start phases do not."""
        for point, calls in (("BACKUP_STAGE", ["confirm", "stop", "backup"]),
                             ("BACKUP_VERIFY", ["confirm", "stop", "backup"]),
                             ("STAGE_TARGET", ["confirm", "stop", "backup", "publish"]),
                             ("BEFORE_PUBLICATION", ["confirm", "stop", "backup", "publish"])):
            with self.subTest(point=point):
                self.setUp()
                self.services.cancel_at = point
                record = self.run_restart(backup=True)
                self.assertEqual((record.state.value, record.last_working_phase), ("CANCELLED", point))
                self.assertEqual(self.services.calls, calls)
        for point in ("VERIFY_BEFORE_START", "START_SERVER"):
            with self.subTest(point=point):
                self.setUp()
                self.services.cancel_at = point
                self.assertEqual(self.run_restart(backup=True).state.value, "SUCCEEDED")

    def test_cancellation_during_the_plan_check_stops_nothing(self) -> None:
        """QF-020: a cancel that arrives in the plan check ends before the stop; the server keeps running."""
        for backup in (False, True):
            with self.subTest(backup=backup):
                self.setUp()
                self.services.cancel_at = "confirm"
                record = self.run_restart(backup=backup)
                self.assertEqual(self.services.calls, ["confirm"])
                self.assertEqual((record.state.value, record.last_working_phase), ("CANCELLED", "preflight"))
                # The operator reads the sentence of a server that keeps running
                self.assertEqual(restart_apply_text(record.state.value, record.last_working_phase),
                                 "Cancelled. The server keeps running; nothing was applied.")

    def test_progress_keeps_the_bands_of_the_design(self) -> None:
        """Plan check 3, stop 8, backup 10 to 35, apply 35 to 88, check 90, start 96."""
        self.manager._publication.progress = (
            lambda pending, phase, percent, real=self.manager._publication.progress:
            (self.services.phases.append((phase, percent)), real(pending, phase, percent))[1])
        self.run_restart(backup=True)
        phases = dict(self.services.phases)
        self.assertEqual((phases["preflight"], phases["STOP_SERVER"]), (3, 8))
        self.assertEqual((phases["BACKUP_DISCOVER"], phases["BACKUP_PUBLISH"]), (10, 35))
        self.assertTrue(all(35 <= phases[name] <= 88 for name in PUBLICATION_PHASES[:3]))
        self.assertEqual((phases["VERIFY_BEFORE_START"], phases["START_SERVER"]), (90, 96))

    def test_request_needs_the_exact_field_set(self) -> None:
        """A missing or non-boolean backup flag and an extra field are invalid requests."""
        good = {"profile_id": "main", "expected_profile_revision": 1,
                "expected_semantic_profile_digest": "a" * 64, "expected_settings_revision": 2,
                "update_operation_id": "update-1", "publication_fingerprint": "b" * 64}
        for parameters in (good, {**good, "backup_after_stop": "yes"},
                           {**good, "backup_after_stop": True, "extra": 1},
                           {**good, "backup_after_stop": False, "publication_fingerprint": "short"}):
            with self.assertRaises(ApplicationCallError) as raised:
                self.coordinator.apply(parameters)
            self.assertEqual(raised.exception.code.value, "INVALID_REQUEST")
        self.assertEqual(self.services.calls, [])

    def test_coordinator_owns_no_mutex_state_copy_or_start(self) -> None:
        """The module names no mutex, no status read, no file operation and no lifecycle start."""
        tree = ast.parse(inspect.getsource(mod_restart_coordinator))
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                  for alias in node.names}
        self.assertEqual(names & {
            "status", "start", "restart", "guard", "stopped", "shutil", "copy", "copytree", "rename",
            "replace", "InstallationGuard", "WindowsInstallationMutex", "ServerState", "stage"}, set())
        self.assertLessEqual({"confirm_restart_plan", "publish", "stop", "create"}, names)


if __name__ == "__main__":
    unittest.main()
