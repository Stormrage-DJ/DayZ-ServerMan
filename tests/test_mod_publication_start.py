"""End-to-end tests for mod publication followed by a requested server start."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dayz_serverman.application.mod_publication import ModPublicationError
from dayz_serverman.application.operations.models import OperationCancelled
from dayz_serverman.domain.lifecycle import (
    LifecycleFailure,
    LifecycleSnapshot,
    ServerState,
)
from dayz_serverman.domain.profiles import ProfileInput
from dayz_serverman.repositories.mod_publication_stage import PublicationStorageError
from test_mod_publication_application import PublicationApplicationFixture, _SyntheticContext


class _Lifecycle:
    """Lifecycle double that requires retired evidence before starting."""
    def __init__(self, retired_root: Path, failure: LifecycleFailure | None = None) -> None:
        """Record the retired evidence root and the optional failure to raise."""
        self.calls: list[tuple[str, int, int]] = []
        self.retired_root = retired_root
        self.failure = failure

    def start(self, profile_id: str, profile_revision: int, settings_revision: int):
        """Require retired evidence, record the call, and return a running snapshot."""
        if len(list(self.retired_root.glob("*.json"))) != 1:
            raise AssertionError("publication evidence must retire before server start")
        self.calls.append((profile_id, profile_revision, settings_revision))
        if self.failure is not None:
            raise self.failure
        return LifecycleSnapshot(ServerState.RUNNING_MANAGED, process_id=441)


class ModPublicationStartTests(PublicationApplicationFixture):
    """Contract: a requested start runs once and only after verified publication."""
    def setUp(self) -> None:
        """Open a gate that requests a server start."""
        super().setUp()
        self.gate_id = self._gate(complete=True, start_requested=True)

    def test_requested_start_runs_once_after_verified_publication(self) -> None:
        """Run the requested start exactly once after publication verified."""
        # Wire a lifecycle that requires retired evidence before starting
        lifecycle = _Lifecycle(self.paths.publication_journals / "retired")
        self.service._lifecycle = lifecycle
        preview = self.service.preview(self.request())
        # Publish must verify, start once, and report the running server
        result = self.service.publish(
            self.request(), preview["publication_fingerprint"], _SyntheticContext("start-once"),
        )
        self.assertEqual(lifecycle.calls, [
            ("main", self.profile.revision, self.settings_revision),
        ])
        self.assertEqual(result["publication_state"], "VERIFIED")
        self.assertEqual(result["start_state"], "STARTED")
        self.assertEqual(result["server"]["state"], "RUNNING_MANAGED")

    def test_lifecycle_failure_retains_verified_publication_result(self) -> None:
        """Keep the verified publication result when the lifecycle start fails."""
        # Force a busy-server start failure after a verified publication
        lifecycle = _Lifecycle(
            self.paths.publication_journals / "retired",
            LifecycleFailure("SERVER_BUSY", "The server is not stopped."),
        )
        self.service._lifecycle = lifecycle
        preview = self.service.preview(self.request())
        result = self.service.publish(
            self.request(), preview["publication_fingerprint"], _SyntheticContext("start-fails"),
        )
        self.assertEqual(len(lifecycle.calls), 1)
        self.assertEqual(result["publication_state"], "VERIFIED")
        self.assertEqual(result["start_state"], "FAILED")
        self.assertEqual(result["start_error"], "SERVER_BUSY")
        self.assertEqual(len(list(lifecycle.retired_root.glob("*.json"))), 1)

    def test_postpublication_target_drift_blocks_start(self) -> None:
        """Block start when published target bytes drift after publication."""
        lifecycle = _Lifecycle(self.paths.publication_journals / "retired")
        self.service._lifecycle = lifecycle
        # Wrap the storage factory so publishing ends with a tampered target
        original_factory = self.service._storage_factory

        def factory(checkpoint):
            """Build a storage whose publish leaves a tampered target behind."""
            storage = original_factory(checkpoint)
            publish = storage.publish

            def publish_then_tamper(journal, root, repository):
                """Publish the journal, then overwrite a published target file."""
                publish(journal, root, repository)
                (root / "mods/alpha/Addons/111.pbo").write_bytes(b"tampered")

            storage.publish = publish_then_tamper
            return storage

        self.service._storage_factory = factory
        # The tamper must fail verification while no start runs
        preview = self.service.preview(self.request())
        with self.assertRaises(ModPublicationError) as raised:
            self.service.publish(
                self.request(), preview["publication_fingerprint"],
                _SyntheticContext("post-drift"),
            )
        self.assertEqual(raised.exception.code, "PUBLICATION_VERIFICATION_FAILED")
        self.assertEqual(lifecycle.calls, [])

    def test_zero_required_items_accepts_empty_gate_and_starts_once(self) -> None:
        """Accept an empty publication and start once from a zero-item gate."""
        # Re-save a profile with no mods so the gate publishes nothing
        self.profile = self.profiles.save(ProfileInput.parse({
            "profile_id": "main", "display_name": "Main",
            "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
            "runtime_profile": None, "mission_root": None, "game_port": 2302,
            "mods": [], "extra_arguments": [],
        }), self.profile.revision)
        self.gate_id = self._gate(complete=True, start_requested=True)
        lifecycle = _SimpleLifecycle()
        self.service._lifecycle = lifecycle
        preview = self.service.preview(self.request())
        # The empty preview carries no targets and no keys
        self.assertEqual((preview["targets"], preview["key_count"]), ([], 0))
        # The empty publication still starts once and retires cleanly
        result = self.service.publish(
            self.request(), preview["publication_fingerprint"], _SyntheticContext("empty-start"),
        )
        self.assertEqual(lifecycle.calls, 1)
        self.assertEqual(result["start_state"], "STARTED")
        self.assertEqual(list(self.paths.publication_journals.rglob("*.json")), [])

    def test_stale_cancel_and_recovery_failures_never_start(self) -> None:
        """Refuse to start on stale fingerprints, cancellation, or recovery blocks."""
        lifecycle = _SimpleLifecycle()
        self.service._lifecycle = lifecycle
        preview = self.service.preview(self.request())
        # A stale fingerprint must be rejected
        with self.assertRaises(ModPublicationError):
            self.service.publish(self.request(), "0" * 64, _SyntheticContext("stale-start"))
        # Cancellation during discovery must be honoured
        with self.assertRaises(OperationCancelled):
            self.service.publish(
                self.request(), preview["publication_fingerprint"],
                _SyntheticContext("cancel-start", cancel_phase="DISCOVER_ITEM"),
            )
        # A recovery-required storage must block the publication
        self.service._storage_factory = lambda _checkpoint: _RecoveryStorage()
        with self.assertRaises(ModPublicationError) as recovery:
            self.service.publish(
                self.request(), preview["publication_fingerprint"],
                _SyntheticContext("recovery-start"),
            )
        self.assertTrue(recovery.exception.recovery_required)
        # No failure mode may record a start attempt
        self.assertEqual(lifecycle.calls, 0)

    def test_lifecycle_failure_is_preserved_in_durable_operation_result(self) -> None:
        """Preserve the lifecycle failure in the durable operation result."""
        lifecycle = _Lifecycle(
            self.paths.publication_journals / "retired",
            LifecycleFailure("SERVER_BUSY", "The server is not stopped."),
        )
        self.service._lifecycle = lifecycle
        preview = self.service.preview(self.request())
        # Submit the publish as a durable operation
        accepted = self.operations.submit(
            "PUBLISH_MODS_AND_KEYS",
            lambda context: self.service.publish(
                self.request(), preview["publication_fingerprint"], context,
            ),
            safe_points=frozenset(),
        )
        terminal = self.wait(accepted.operation_id)
        # The durable result keeps the verified publication and the start failure
        self.assertEqual(terminal.state.value, "SUCCEEDED")
        self.assertEqual(terminal.result["publication_state"], "VERIFIED")
        self.assertEqual(terminal.result["start_state"], "FAILED")
        self.assertEqual(terminal.result["start_error"], "SERVER_BUSY")



class _SimpleLifecycle:
    """Start double that counts calls and returns a managed snapshot."""
    def __init__(self) -> None:
        """Start with zero recorded calls."""
        self.calls = 0

    def start(self, _profile_id: str, _profile_revision: int, _settings_revision: int):
        """Record the call and return a managed running snapshot."""
        self.calls += 1
        return LifecycleSnapshot(ServerState.RUNNING_MANAGED, process_id=442)


class _RecoveryStorage:
    """Storage double whose staging always demands manual recovery."""
    def stage(self, _intent, _root):
        """Fail staging with a recovery-required publication error."""
        raise PublicationStorageError(
            "RECOVERY_REQUIRED", "Synthetic recovery block.", recovery_required=True,
        )


if __name__ == "__main__":
    unittest.main()
