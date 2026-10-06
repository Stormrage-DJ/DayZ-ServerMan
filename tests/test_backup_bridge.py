"""Bridge tests for backup creation, restore preview, and safety gates."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.backup_coordinator import BackupCoordinator  # noqa: E402
from dayz_serverman.application.backups import BackupService, MISSION_INVENTORY  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.bridge.contracts import CONTRACT_VERSION  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorage  # noqa: E402
from tests.profile_fixtures import create_profile_paths, profile_payload  # noqa: E402
from tests.test_backups import FakeProfiles, FakeSettings, create_runtime_profile, record  # noqa: E402


# Operation states that end a backup operation
TERMINAL = {
    OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED,
    OperationState.RECOVERY_REQUIRED,
}


def request(method: str, parameters: dict[str, object]) -> dict[str, object]:
    """Build a bridge request envelope for the given method."""
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "backup-request",
        "method": method,
        "parameters": parameters,
    }


class BackupBridgeTests(unittest.TestCase):
    """End-to-end backup bridge contracts over a real composition."""
    def setUp(self) -> None:
        """Build a manager composition with a saved profile and settings."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_backup_bridge_")
        self.root = Path(self.temporary.name)
        self.manager = self.root / "Portable Manager"
        self.dayz = self.root / "DáyZ Server"
        executable = self.dayz / "Bin" / "DayZ Server_x64.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        create_profile_paths(self.dayz)
        (self.dayz / "Profiles" / "Máin Runtime" / "runtime.cfg").write_text(
            "fixture", encoding="utf-8",
        )
        server = self.dayz / "Config Files" / "serverDZ.cfg"
        server.parent.mkdir(parents=True, exist_ok=True)
        server.write_text('hostname="fixture"; class Missions { class DayZ { template="dayzOffline.enoch"; }; };\n', encoding="utf-8")
        mission = self.dayz / "mpmissions" / "dayzOffline.enoch"
        # Seed every required mission inventory item
        for item in MISSION_INVENTORY:
            target = mission.joinpath(*item.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item, encoding="utf-8")
        self.composition = build_composition(self.manager)
        settings = self.composition.settings.save(
            SettingsInput(dayz_root=str(self.dayz), dayz_executable=str(executable)), None,
        )
        self.settings_revision = settings.revision
        profile = self.composition.profile_repository.save(
            ProfileInput.parse(profile_payload()), None,
        )
        self.profile_revision = profile.revision

    def tearDown(self) -> None:
        """Shut the operations down and remove the temporary tree."""
        self.composition.operations.shutdown(2)
        self.temporary.cleanup()

    def dispatch(self, method: str, parameters: dict[str, object]) -> dict:
        """Send one bridge request and return the response envelope."""
        return self.composition.bridge.dispatch(request(method, parameters))

    def wait_terminal(self, operation_id: str):
        """Wait for the operation to finish and return its final record."""
        # A restore takes under half a second locally; hosted runners write files far slower
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record_value = self.composition.operations.get(operation_id)
            if record_value.state in TERMINAL:
                return record_value
            time.sleep(0.01)
        raise AssertionError(
            f"{record_value.kind} did not finish: state {record_value.state}, "
            f"phase {record_value.progress_phase}, {record_value.progress_percent}%"
        )

    def test_named_bridge_creates_verified_backup_and_history(self) -> None:
        """A named backup publishes verified history and restores via its preview."""
        empty = self.dispatch("list_backups", {"profile_id": "livonia-main"})
        self.assertTrue(empty["success"])
        self.assertEqual(empty["value"]["backups"], [])
        # Create one backup for the saved profile revision
        accepted = self.dispatch("create_backup", {
            "profile_id": "livonia-main",
            "expected_profile_revision": self.profile_revision,
            "expected_settings_revision": self.settings_revision,
        })
        record_value = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(record_value.state, OperationState.SUCCEEDED)
        history = self.dispatch("list_backups", {"profile_id": "livonia-main"})
        self.assertEqual(len(history["value"]["backups"]), 1)
        summary = history["value"]["backups"][0]
        self.assertEqual(summary["restore_compatibility"], "COMPATIBLE")
        # Preview and apply the restore with the confirmed fingerprint
        preview = self.dispatch("preview_restore", {
            "profile_id": "livonia-main", "backup_id": summary["backup_id"],
        })
        self.assertTrue(preview["success"])
        self.assertTrue(any(
            item["target_kind"] == "RUNTIME_PROFILE" for item in preview["value"]["targets"]
        ))
        apply = self.dispatch("apply_restore", {
            "profile_id": "livonia-main", "backup_id": summary["backup_id"],
            "expected_profile_revision": self.profile_revision,
            "expected_settings_revision": self.settings_revision,
            "expected_manifest_digest": summary["manifest_digest"],
            "preview_fingerprint": preview["value"]["fingerprint"],
        })
        apply_record = self.wait_terminal(apply["value"]["operation_id"])
        self.assertEqual(apply_record.state, OperationState.SUCCEEDED)
        # The progress phases must follow the published pipeline order
        phases = [event.payload.get("phase") for event in
                  self.composition.operations.read_events(0, 100)[0]
                  if event.kind == "progress" and event.operation_id == record_value.operation_id]
        self.assertEqual(phases, ["DISCOVER", "STAGE", "HASH", "WRITE_MANIFEST", "VERIFY", "PUBLISH"])

    def test_contract_rejects_extra_fields_and_stale_context_fails_in_lane(self) -> None:
        """Unknown fields are rejected and a stale revision fails in the lane."""
        # Unknown fields must be rejected without echoing the submitted path
        extra = self.dispatch("list_backups", {"profile_id": "livonia-main", "path": "D:\\secret"})
        self.assertFalse(extra["success"])
        self.assertEqual(extra["error"]["code"], "INVALID_REQUEST")
        self.assertNotIn("D:\\secret", extra["error"]["message"])
        # A stale profile revision must fail the queued operation
        stale = self.dispatch("create_backup", {
            "profile_id": "livonia-main",
            "expected_profile_revision": 99,
            "expected_settings_revision": self.settings_revision,
        })
        record_value = self.wait_terminal(stale["value"]["operation_id"])
        self.assertEqual(record_value.state, OperationState.FAILED)
        self.assertEqual(record_value.terminal_error.code, "REVISION_CONFLICT")

    def test_browser_surface_is_narrow_and_named(self) -> None:
        """The browser surface exposes only the named backup methods."""
        # Mutating restore and delete methods stay off the browser surface
        methods = self.composition.host_bridge.allowed_methods
        self.assertIn("list_backups", methods)
        self.assertIn("create_backup", methods)
        for forbidden in ("restore_backup", "delete_backup", "browse_backup_path"):
            self.assertNotIn(forbidden, methods)


class BackupOperationSafetyTests(unittest.TestCase):
    """Shutdown, cancellation, and serialization safety for backup operations."""
    def setUp(self) -> None:
        """Create a dayz tree with a runtime profile and server config."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_backup_ops_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "dayz"
        self.backups = self.root / "backups"
        self.backups.mkdir()
        server = self.dayz / "Config Files" / "serverDZ.cfg"
        server.parent.mkdir(parents=True)
        server.write_text('class Missions { class DayZ { template="dayzOffline.chernarusplus"; }; };', encoding="utf-8")
        create_runtime_profile(self.dayz)
        mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        (mission / "storage_3").mkdir(parents=True)
        (mission / "storage_3" / "players.db").write_bytes(b"world")

    def tearDown(self) -> None:
        """Remove the temporary backup tree."""
        self.temporary.cleanup()

    def coordinator(self, phase: str, entered: threading.Event, release: threading.Event):
        """Build a coordinator whose storage pauses at the named phase."""
        storage = BackupStorage(phase_hook=lambda current: (
            entered.set(), release.wait(2)
        ) if current == phase else None)
        service = BackupService(
            FakeProfiles(record()),  # type: ignore[arg-type]
            FakeSettings(self.dayz, self.backups),  # type: ignore[arg-type]
            storage,
            clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
            identifier=lambda: phase.lower(),
        )
        operations = OperationManager(OperationStore(self.root / f"operations-{phase}"))
        return BackupCoordinator(service, operations), operations

    def test_shutdown_during_publish_completes_publication(self) -> None:
        """A shutdown during publish lets the running backup finish."""
        # Hold the backup at the publish phase before shutdown
        entered, release = threading.Event(), threading.Event()
        coordinator, operations = self.coordinator("PUBLISH", entered, release)
        try:
            accepted = coordinator.create_backup({
                "profile_id": "main", "expected_profile_revision": 3,
                "expected_settings_revision": 4,
            })
            self.assertTrue(entered.wait(1))
            self.assertFalse(any(self.backups.glob("*.zip")))
            # The in-flight publication must complete before the drain returns
            operations.begin_shutdown()
            release.set()
            self.assertTrue(operations.wait_for_drain(2))
            self.assertEqual(operations.get(accepted["operation_id"]).state, OperationState.SUCCEEDED)
            self.assertEqual(len(list(self.backups.glob("*.zip"))), 1)
        finally:
            release.set()
            operations.shutdown(2)

    def test_cancellation_before_publication_cleans_staging(self) -> None:
        """A cancelled staging run leaves no snapshot or staging debris."""
        # Cancel while the snapshot is still being staged
        entered, release = threading.Event(), threading.Event()
        coordinator, operations = self.coordinator("STAGE", entered, release)
        try:
            accepted = coordinator.create_backup({
                "profile_id": "main", "expected_profile_revision": 3,
                "expected_settings_revision": 4,
            })
            self.assertTrue(entered.wait(1))
            operations.request_cancellation(accepted["operation_id"])
            release.set()
            deadline = time.monotonic() + 2
            while operations.get(accepted["operation_id"]).state not in TERMINAL and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(operations.get(accepted["operation_id"]).state, OperationState.CANCELLED)
            # No snapshot, staging folder, or partial file may remain
            self.assertFalse(any(self.backups.glob("*.zip")))
            self.assertFalse(any(self.backups.glob(".staging-*")))
            self.assertFalse(any(self.backups.glob("*.partial")))
        finally:
            release.set()
            operations.shutdown(2)

    def test_two_snapshot_mutations_are_serialized(self) -> None:
        """Concurrent snapshot mutations are serialized in the lane."""
        # Start the first backup and hold it at the stage phase
        entered, release = threading.Event(), threading.Event()
        storage = BackupStorage(phase_hook=lambda phase: (
            entered.set(), release.wait(2)
        ) if phase == "STAGE" and not release.is_set() else None)
        identifiers = iter(("first", "second"))
        service = BackupService(
            FakeProfiles(record()),  # type: ignore[arg-type]
            FakeSettings(self.dayz, self.backups),  # type: ignore[arg-type]
            storage,
            clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
            identifier=lambda: next(identifiers),
        )
        operations = OperationManager(OperationStore(self.root / "operations-serialized"))
        coordinator = BackupCoordinator(service, operations)
        arguments = {"profile_id": "main", "expected_profile_revision": 3,
                     "expected_settings_revision": 4}
        try:
            first = coordinator.create_backup(arguments)
            self.assertTrue(entered.wait(1))
            # The second backup must queue until the first one finishes
            second = coordinator.create_backup(arguments)
            self.assertEqual(operations.get(first["operation_id"]).state, OperationState.RUNNING)
            self.assertEqual(operations.get(second["operation_id"]).state, OperationState.QUEUED)
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if all(operations.get(item["operation_id"]).state in TERMINAL for item in (first, second)):
                    break
                time.sleep(0.01)
            self.assertTrue(all(operations.get(item["operation_id"]).state == OperationState.SUCCEEDED
                                for item in (first, second)))
        finally:
            release.set()
            operations.shutdown(2)


if __name__ == "__main__":
    unittest.main()
