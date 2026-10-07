"""Task 2.5, design 6.5: the first-change markers are set right before the first change and decide the exit code."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import MappingProxyType

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from profile_fixtures import create_profile_paths, profile_payload  # noqa: E402
from test_lifecycle_service import FakeInventory, FakeLauncher, FakeMutex, FakeStopper  # noqa: E402
from test_lifecycle_service import ProfilesStub, SettingsStub  # noqa: E402
from test_mod_restart_coordinator import _Services  # noqa: E402
from dayz_serverman.application.lifecycle import ServerLifecycleService  # noqa: E402
from dayz_serverman.application.lifecycle_coordinator import LifecycleCoordinator  # noqa: E402
from dayz_serverman.application.mod_restart_coordinator import ModRestartCoordinator  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.profile_coordinator import ProfileCoordinator  # noqa: E402
from dayz_serverman.cli.exit_codes import record_exit  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure, LifecycleSnapshot, ServerState  # noqa: E402
from dayz_serverman.domain.models import ManagerSettings  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.repositories.json_store import VersionedJsonRepository  # noqa: E402
from dayz_serverman.repositories.lifecycle_state import LifecycleStateRepository  # noqa: E402

# A refusal that the lifecycle raises before or after the marker
REFUSAL = LifecycleFailure("EXTERNAL_PROCESS", "refused")


class MarkingLifecycle:
    """Lifecycle fake: refuses before the marker, or calls the marker and refuses after it."""

    def __init__(self) -> None:
        """Refuse after the marker unless told otherwise; the start after a backup may refuse too."""
        self.before = False
        self.refuse_start = False

    def status(self) -> LifecycleSnapshot:
        """Report a stopped server whose profile is not known."""
        return LifecycleSnapshot(ServerState.STOPPED)

    def _change(self, before_change) -> LifecycleSnapshot:
        """Refuse before the marker, or run the marker and then refuse."""
        if not self.before:
            before_change()
        raise REFUSAL

    def start(self, _profile, _profile_revision, _settings_revision, *, before_change=None) -> LifecycleSnapshot:
        """Start: a refusal after a stop and backup when asked, else the marker rule."""
        if self.refuse_start:
            raise REFUSAL
        return self._change(before_change)

    def stop(self, _settings_revision, *, before_change=None) -> LifecycleSnapshot:
        """Stop: succeeds when only the following start is to refuse."""
        if self.refuse_start:
            before_change()
            return LifecycleSnapshot(ServerState.STOPPED)
        return self._change(before_change)

    def restart(self, _profile, _profile_revision, _settings_revision, *, before_change=None) -> LifecycleSnapshot:
        """Restart without a backup: the marker sits before the stop request."""
        return self._change(before_change)


class _Backups:
    """Backup fake that succeeds at once."""

    def create(self, _profile, _profile_revision, _settings_revision, checkpoint) -> dict[str, object]:
        """Report one phase and return a summary."""
        checkpoint("DISCOVER", 10)
        return {"backup_id": "backup-1"}


def wait(manager: OperationManager, operation_id: str) -> dict:
    """Return the terminal record of an operation as a dictionary."""
    for _ in range(500):
        record = manager.get(operation_id)
        if record.state.value in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
            return record.to_dict()
        time.sleep(0.01)
    raise AssertionError("operation did not finish")


class LaneMarkerTests(unittest.TestCase):
    """The coordinators relay each marker to the record; before it exit 3, after it exit 1 (criterion 25)."""

    def setUp(self) -> None:
        """A real operation lane over a temporary store."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_markers_")
        self.addCleanup(self.temporary.cleanup)
        self.manager = OperationManager(OperationStore(Path(self.temporary.name)))
        self.addCleanup(self.manager.shutdown, 3)
        self.lifecycle = MarkingLifecycle()
        self.handlers = LifecycleCoordinator(self.lifecycle, self.manager, _Backups()).handlers()

    def lane(self, method: str, backup: bool | None = None) -> dict:
        """Submit a lifecycle request and return its terminal record."""
        parameters = {"profile_id": "main", "expected_profile_revision": 1, "expected_settings_revision": 2}
        if backup is not None:
            parameters["backup_after_stop"] = backup
        return wait(self.manager, self.handlers[method](parameters)["operation_id"])

    def assert_marker(self, method: str, kind: str, before: int, marker: int, backup: bool | None = None) -> None:
        """Check the percent and the exit code of a refusal before and after the marker."""
        for refuse_before, percent, code in ((True, before, 3), (False, marker, 1)):
            self.lifecycle.before = refuse_before
            record = self.lane(method, backup)
            with self.subTest(kind=kind, before=refuse_before):
                self.assertEqual((record["kind"], record["state"]), (kind, "FAILED"))
                self.assertEqual(record["progress_percent"], percent)
                self.assertEqual(record_exit(record, backup_after_stop=bool(backup)), code)

    def test_start_marker(self) -> None:
        """START_SERVER: preflight 10, marker 11 before the launch."""
        self.assert_marker("start_server", "START_SERVER", 10, 11)

    def test_stop_marker(self) -> None:
        """STOP_SERVER: STOP_SERVER 20, marker 21 before the stop request."""
        self.assert_marker("stop_server", "STOP_SERVER", 20, 21, backup=False)

    def test_restart_markers(self) -> None:
        """RESTART_SERVER: marker 12 without a backup, 16 with a backup; a refused start after it exits 1."""
        self.assert_marker("restart_server", "RESTART_SERVER", 10, 12, backup=False)
        self.assert_marker("restart_server", "RESTART_SERVER", 15, 16, backup=True)
        self.lifecycle.refuse_start = True
        record = self.lane("restart_server", True)
        self.assertEqual((record["progress_percent"], record["last_working_phase"]), (98, "START_SERVER"))
        self.assertEqual(record_exit(record, backup_after_stop=True), 1)

    def test_marker_phases_keep_their_text(self) -> None:
        """A marker repeats the phase of its step, so the operation keeps its phase text."""
        self.lifecycle.before = False
        self.assertEqual(self.lane("start_server")["last_working_phase"], "preflight")
        self.assertEqual(self.lane("stop_server", False)["last_working_phase"], "STOP_SERVER")
        self.assertEqual(self.lane("restart_server", False)["last_working_phase"], "preflight")


class _MarkingRestartServices(_Services):
    """Apply-and-restart fakes whose stop refuses before or after the marker."""

    before = False

    def stop(self, settings_revision: int, *, before_change=None) -> None:
        """Log the stop, then refuse before or after the marker."""
        self._step("stop")
        if not self.before:
            before_change()
        raise REFUSAL


class _Deletion:
    """Profile deletion fake: refuses before the marker or after it."""

    before = False

    def delete(self, _profile_id, _expected, *, before_change=None) -> dict:
        """Refuse before the marker, or run it and refuse."""
        if not self.before:
            before_change()
        raise LifecycleFailure("CONTROL_CONFLICT", "refused")


class OtherMarkerTests(unittest.TestCase):
    """APPLY_MODS_AND_RESTART marker 9 and DELETE_PROFILE marker ("running", 1)."""

    def setUp(self) -> None:
        """A real operation lane over a temporary store."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_markers_")
        self.addCleanup(self.temporary.cleanup)
        self.manager = OperationManager(OperationStore(Path(self.temporary.name)))
        self.addCleanup(self.manager.shutdown, 3)

    def test_apply_and_restart_marker(self) -> None:
        """STOP_SERVER at 8 is before the change, the marker 9 right before the stop request."""
        services = _MarkingRestartServices()
        services.manager = self.manager
        coordinator = ModRestartCoordinator(services, services, services, self.manager)
        for before, percent, code in ((True, 8, 3), (False, 9, 1)):
            services.before = before
            accepted = coordinator.apply({
                "profile_id": "main", "expected_profile_revision": 1, "expected_semantic_profile_digest": "a" * 64,
                "expected_settings_revision": 2, "update_operation_id": "update-1",
                "publication_fingerprint": "b" * 64, "backup_after_stop": False})
            record = wait(self.manager, accepted["operation_id"])
            with self.subTest(before=before):
                self.assertEqual((record["progress_percent"], record["last_working_phase"]), (percent, "STOP_SERVER"))
                self.assertEqual(record_exit(record), code)

    def test_delete_profile_marker(self) -> None:
        """The deletion relays ("running", 1) right before its first change; 0 percent is before it."""
        deletion = _Deletion()
        coordinator = ProfileCoordinator(None, self.manager, deletion)  # type: ignore[arg-type]
        for before, percent, code in ((True, 0, 3), (False, 1, 1)):
            deletion.before = before
            accepted = coordinator.delete_profile({"profile_id": "main", "expected_revision": 3})
            record = wait(self.manager, accepted["operation_id"])
            with self.subTest(before=before):
                self.assertEqual(record["progress_percent"], percent)
                self.assertEqual(record_exit(record), code)


class ServiceMarkerOrderTests(unittest.TestCase):
    """ServerLifecycleService calls the marker after every check and right before the launch or the stop request."""

    def setUp(self) -> None:
        """Assemble the service with the scripted ports of the lifecycle service tests."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_marker_service_")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        dayz = root / "DayZ Root"
        create_profile_paths(dayz)
        executable = dayz / "Bin" / "DayZ Server_x64.exe"
        settings = ManagerSettings(4, str(dayz.resolve()), str(executable.resolve()), None, None, None, None,
                                   MappingProxyType({}))
        self.inventory = FakeInventory()
        self.launcher = FakeLauncher(self.inventory)
        self.stopper = FakeStopper(self.inventory)
        self.order: list[str] = []
        self.launcher.on_launch = lambda: self.order.append("launch")
        self.stopper.on_stop = lambda: self.order.append("stop request")
        state = LifecycleStateRepository(VersionedJsonRepository(root / "manager" / "data" / "state.json"), "s")
        self.service = ServerLifecycleService(
            SettingsStub(settings), ProfilesStub(ProfileRecord(7, ProfileInput.parse(profile_payload()))),
            self.inventory, self.launcher, self.stopper, FakeMutex(), state, root / "manager" / "data" / "logs")

    def mark(self) -> None:
        """Record the marker call."""
        self.order.append("marker")

    def test_start_stop_and_restart_order(self) -> None:
        """Each marker comes once, right before its change."""
        self.service.start("livonia-main", 7, 4, before_change=self.mark)
        self.service.stop(4, before_change=self.mark)
        self.service.start("livonia-main", 7, 4)
        self.service.restart("livonia-main", 7, 4, before_change=self.mark)
        self.assertEqual(self.order, ["marker", "launch", "marker", "stop request", "launch",
                                      "marker", "stop request", "launch"])

    def test_refusals_before_the_change_never_mark(self) -> None:
        """A refused start of a running server and a refused stop of a stopped server call no marker."""
        self.service.start("livonia-main", 7, 4)
        self.order.clear()
        with self.assertRaises(LifecycleFailure):
            self.service.start("livonia-main", 7, 4, before_change=self.mark)
        self.service.stop(4)
        with self.assertRaises(LifecycleFailure):
            self.service.stop(4, before_change=self.mark)
        self.assertNotIn("marker", self.order)


if __name__ == "__main__":
    unittest.main()
