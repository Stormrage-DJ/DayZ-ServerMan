"""Verify files: rejections, progress, cancellation points and the bridge call."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_files_fixtures as fixtures  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import (  # noqa: E402
    OperationCancelled, OperationFailure,
)
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.workshop_verification import progress_percent  # noqa: E402
from dayz_serverman.application.workshop_verification_coordinator import (  # noqa: E402
    WorkshopVerificationCoordinator,
)
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402

FakeContext = fixtures.FakeContext


class VerifyControlTests(fixtures.VerifyFilesFixture):
    """Rejections inside the operation, progress values and every cancellation point."""

    def failure(self, **changes: int) -> str:
        """Run a verification that must fail and return its error code."""
        with self.assertRaises(OperationFailure) as raised:
            self.verify(**changes)
        return raised.exception.code

    def test_stale_revisions_are_conflicts(self) -> None:
        """A stale profile or settings revision fails before any file is read."""
        self.assertEqual(self.failure(expected_profile_revision=99), "REVISION_CONFLICT")
        self.assertEqual(self.failure(expected_settings_revision=99), "REVISION_CONFLICT")
        self.assertFalse(self.store._path.exists())

    def test_unset_or_unusable_roots_are_invalid_paths(self) -> None:
        """Both roots must be set and the server folder must exist."""
        for field, value in (("dayz_root", None), ("workshop_content_root", None),
                             ("dayz_root", str(self.base / "absent"))):
            with self.subTest(field=field, value=value):
                original = getattr(self.settings, field)
                setattr(self.settings, field, value)
                self.assertEqual(self.failure(), "PATH_INVALID")
                setattr(self.settings, field, original)

    def test_unreadable_manifest_is_reported(self) -> None:
        """A cache without a readable manifest fails the whole operation."""
        (self.base / "steamapps" / "workshop" / "appworkshop_221100.acf").unlink()
        self.assertEqual(self.failure(), "WORKSHOP_MANIFEST_INVALID")

    def test_phases_percent_and_detail_per_item(self) -> None:
        """Each half-step is a checkpoint; the detail names every item and its phase."""
        self.assertEqual([progress_percent(step, 2) for step in range(5)], [5, 27, 50, 72, 95])
        context = FakeContext()
        self.verify(context)
        self.assertEqual(context.checkpoints, [
            ("verify_source", 5), ("verify_target", 27),
            ("verify_source", 50), ("verify_target", 72),
        ])
        phases = [[(item["workshop_id"], item["phase"]) for item in value]
                  for value in context.details]
        self.assertEqual(phases, [
            [("111", "verify_source"), ("222", "queued")],
            [("111", "verify_target"), ("222", "queued")],
            [("111", "done"), ("222", "queued")],
            [("111", "done"), ("222", "verify_source")],
            [("111", "done"), ("222", "verify_target")],
            [("111", "done"), ("222", "done")],
        ])
        # A finished item reports all of its bytes
        self.assertEqual(context.details[-1][0], {
            "workshop_id": "111", "phase": "done", "done_bytes": 5, "total_bytes": 5})
        self.assertEqual(
            {name for value in context.details for item in value for name in item},
            {"workshop_id", "phase", "done_bytes", "total_bytes"})

    def cancelled(self, context: FakeContext) -> set[str]:
        """Run a verification that must be cancelled; return the stored source ids."""
        with self.assertRaises(OperationCancelled):
            self.verify(context)
        return set(self.store.load().sources)

    def test_cancellation_at_the_safe_points(self) -> None:
        """The item in progress writes nothing; finished items stay saved."""
        cases = {
            ("verify_source", 0): set(), ("verify_target", 0): set(),
            ("verify_source", 1): {"111"}, ("verify_target", 1): {"111"},
        }
        for (phase, index), saved in cases.items():
            with self.subTest(phase=phase, index=index):
                if self.store._path.exists():
                    self.store._path.unlink()
                self.assertEqual(self.cancelled(FakeContext(phase, index)), saved)
                # A finished item keeps its target record as well
                self.assertEqual(len(self.store.load().targets), len(saved))

    def test_cancellation_before_each_file(self) -> None:
        """A probe that fires before a source or target file abandons that item only."""
        # Probes: source file of 111, target file of 111, source file of 222, target of 222
        cases = {0: set(), 1: set(), 2: {"111"}, 3: {"111"}}
        for probes, saved in cases.items():
            with self.subTest(probes=probes):
                if self.store._path.exists():
                    self.store._path.unlink()
                context = FakeContext(cancel_after_probes=probes)
                self.assertEqual(self.cancelled(context), saved)
                self.assertEqual(context.probes, probes + 1)

    def test_cancellation_is_never_a_failed_verification(self) -> None:
        """A cancelled hash removes no record and reports no FAILED state."""
        self.verify()
        before = self.store._path.read_bytes()
        self.cancelled(FakeContext(cancel_after_probes=0))
        self.assertEqual(self.store._path.read_bytes(), before)


class VerifyBridgeTests(fixtures.VerifyFilesFixture):
    """The bridge call validates before queueing and runs on the lane."""

    def setUp(self) -> None:
        """Wire the coordinator behind a bridge facade."""
        super().setUp()
        self.operations = OperationManager(OperationStore(self.base / "operations"))
        self.addCleanup(self.operations.shutdown, 2)
        self.bridge = BridgeFacade(
            WorkshopVerificationCoordinator(self.service, self.operations).handlers())

    def call(self, parameters: dict[str, object]) -> dict:
        """Dispatch one `verify_mod_files` request."""
        return self.bridge.dispatch({"contract_version": 1, "request_id": "verify",
                                     "method": "verify_mod_files", "parameters": parameters})

    def parameters(self) -> dict[str, object]:
        """Return the valid request of the fixture profile."""
        return {"profile_id": "main", "expected_profile_revision": self.record.revision,
                "expected_settings_revision": 1}

    def finished(self, operation_id: str):
        """Wait for the terminal state and return the record."""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = self.operations.get(operation_id)
            if record.state.value in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return record
            time.sleep(0.01)
        self.fail("operation did not finish")

    def test_invalid_requests_are_rejected_before_queueing(self) -> None:
        """Unknown, missing and malformed fields never reach the lane."""
        valid = self.parameters()
        cases = [
            {**valid, "authentication_mode": "ANONYMOUS"},
            {key: value for key, value in valid.items() if key != "profile_id"},
            {**valid, "profile_id": "../escape"}, {**valid, "expected_profile_revision": True},
            {**valid, "expected_settings_revision": -1},
            {**valid, "expected_settings_revision": "1"},
        ]
        for parameters in cases:
            with self.subTest(parameters=parameters):
                response = self.call(parameters)
                self.assertEqual(response["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.operations.list_recent(), ())

    def test_blocked_lane_is_a_mutation_conflict(self) -> None:
        """A lane that accepts no work answers MUTATION_CONFLICT before queueing."""
        self.operations.block_for_recovery("synthetic recovery block")
        response = self.call(self.parameters())
        self.assertEqual(response["error"]["code"], "MUTATION_CONFLICT")
        self.assertEqual(self.operations.list_recent(), ())

    def test_operation_runs_on_the_lane_and_keeps_its_detail(self) -> None:
        """The operation succeeds whatever the item states and keeps the per-item detail."""
        (self.dayz / "@Alpha" / "Addons" / "mod.pbo").write_bytes(b"other")
        response = self.call(self.parameters())
        self.assertEqual(set(response["value"]), {"operation_id", "state"})
        record = self.finished(response["value"]["operation_id"])
        self.assertEqual((record.kind, record.state.value),
                         ("VERIFY_WORKSHOP_FILES", "SUCCEEDED"))
        self.assertEqual([(item["workshop_id"], item["source_state"], item["target_state"])
                          for item in record.result["items"]],
                         [("111", "VERIFIED", "DIFFERS"), ("222", "VERIFIED", "MATCHES_SOURCE")])
        self.assertEqual([(item["workshop_id"], item["phase"])
                          for item in record.progress_detail["items"]],
                         [("111", "done"), ("222", "done")])

    def test_stale_revision_fails_in_the_operation(self) -> None:
        """A revision conflict is a failed operation, not a bridge error."""
        response = self.call({**self.parameters(), "expected_profile_revision": 99})
        record = self.finished(response["value"]["operation_id"])
        self.assertEqual((record.state.value, record.terminal_error.code),
                         ("FAILED", "REVISION_CONFLICT"))


if __name__ == "__main__":
    unittest.main()
