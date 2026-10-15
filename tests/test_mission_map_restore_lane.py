"""The two restore lane operations of leaf 8.8: submission, failure codes, recovery block and activity wording."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.activity_wording import ERROR_TEXTS, KIND_TEXTS, SHOWN_HOST_TEXTS, error_text
from dayz_serverman.application.mission_map_apply_coordinator import apply_error
from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND, RECOVERY_UNRESOLVED
from dayz_serverman.application.mission_map_restore_coordinator import MissionMapRestoreCoordinator
from dayz_serverman.application.mission_map_restore_review import RestoreRefused
from dayz_serverman.application.operations.models import OperationFailure, QueueUnavailable
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain.mission_map_families import (
    FAMILY_UNKNOWN_TEXT, NOT_VERIFIED_TEXT, FamilyNotVerified, family_gate,
)
from dayz_serverman.domain.mission_map_restore_texts import (
    INVALID_MODE_TEXT, LANE_KINDS, NO_ORIGINAL, NO_ORIGINAL_TEXT, NO_PREVIOUS_STATE_TEXT, RESTORE_STALE_TEXT, TARGET_CHANGED_TEXT,
)
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError

# A valid request of the lane operation
REQUEST = {"profile_id": "main", "mode": "original", "preview_fingerprint": "a" * 64}


class Lane:
    """A fake lane that keeps the submitted work and the blocks that the coordinator sets on it."""

    def __init__(self, refusal: QueueUnavailable | None = None) -> None:
        """Start without work; a refusal refuses the submission."""
        self.refusal, self.kind, self.work, self.blocks, self.context_blocks = refusal, None, None, [], []
        self.fields: dict[str, Any] = {}

    def submit(self, kind: str, work: Any, **options: Any) -> Any:
        """Keep the work, or refuse it as a full or blocked lane does."""
        if self.refusal is not None:
            raise self.refusal
        self.kind, self.work, self.fields = kind, work, options
        return SimpleNamespace(operation_id="op1", state=SimpleNamespace(value="ACCEPTED"))

    def block_for_recovery(self, message: str, owner: str | None = None) -> None:
        """Record a block that the coordinator sets on the manager directly, with its owner."""
        self.blocks.append((message, owner))

    def run(self) -> Any:
        """Run the work with a context of operation op1 that records each block with its owner."""
        def block(message: str, owner: str | None = None) -> None:
            """Record a block of the operation context; None is the kind of the operation."""
            self.context_blocks.append((message, owner))
        return self.work(SimpleNamespace(operation_id="op1", block_for_recovery=block))


class Service:
    """A fake restore service that returns a result or raises one failure."""

    def __init__(self, failure: Exception | None = None) -> None:
        """Keep the failure; None succeeds."""
        self.failure, self.calls = failure, []

    def restore(self, profile_id: object, mode: object, fingerprint: object, *, operation_id: str) -> dict:
        """Record the call, then raise or return."""
        self.calls.append((profile_id, mode, fingerprint, operation_id))
        if self.failure is not None:
            raise self.failure
        return {"operation_id": operation_id, "result": "COMMITTED"}


class RestoreLaneTests(unittest.TestCase):
    """Submission under the restore kind of the mode, the request shape and the failures of the work."""

    def failure(self, error: Exception, mode: str = "original") -> tuple[OperationFailure, Lane]:
        """Run the work with a service that raises the error and return the operation failure."""
        lane = Lane()
        MissionMapRestoreCoordinator(Service(error), lane).restore_mission_map(  # type: ignore[arg-type]
            dict(REQUEST, mode=mode))
        with self.assertRaises(OperationFailure) as failed:
            lane.run()
        return failed.exception, lane

    def test_submission_by_mode(self) -> None:
        """Each mode has its own lane kind, with activity wording since 8.5; the work passes the request."""
        for mode, kind in (("original", "RESTORE_MISSION_MAP_ORIGINAL"), ("previous", "RESTORE_MISSION_MAP_PREVIOUS")):
            with self.subTest(mode=mode):
                lane, service = Lane(), Service()
                handle = MissionMapRestoreCoordinator(service, lane).restore_mission_map(  # type: ignore[arg-type]
                    dict(REQUEST, mode=mode))
                self.assertEqual((handle, lane.kind, LANE_KINDS[mode]),
                                 ({"operation_id": "op1", "state": "ACCEPTED"}, kind, kind))
                self.assertEqual(lane.fields["target_profile_id"], "main")
                self.assertEqual(lane.run(), {"operation_id": "op1", "result": "COMMITTED"})
                self.assertEqual(service.calls, [("main", mode, "a" * 64, "op1")])
                self.assertIn(kind, KIND_TEXTS)

    def test_malformed_request_and_a_refused_submission(self) -> None:
        """Other fields, an unknown mode or a fingerprint that is not text refuse; a blocked lane conflicts."""
        coordinator = MissionMapRestoreCoordinator(Service(), Lane())  # type: ignore[arg-type]
        for request in ({"profile_id": "main"}, dict(REQUEST, preview_fingerprint=None), dict(REQUEST, extra=1),
                        dict(REQUEST, mode="latest"), dict(REQUEST, mode=["original"])):
            with self.assertRaises(ApplicationCallError) as refused:
                coordinator.restore_mission_map(request)
            self.assertEqual(refused.exception.code, ErrorCode.INVALID_REQUEST)
        blocked = MissionMapRestoreCoordinator(Service(), Lane(QueueUnavailable("blocked", "RECOVERY_BLOCK",
                                                                                EDITOR_KIND)))
        with self.assertRaises(ApplicationCallError) as refused:
            blocked.restore_mission_map(dict(REQUEST))  # type: ignore[arg-type]
        self.assertEqual((refused.exception.code, refused.exception.retryable), (ErrorCode.MUTATION_CONFLICT, True))

    def test_family_refusal_keeps_the_6_11_envelope_and_text(self) -> None:
        """UNSUPPORTED_SNAPSHOT_CONTENT with the gate text and operation restore_original, shown unchanged."""
        for terrain, text in (("enoch", NOT_VERIFIED_TEXT.format(family="enoch")), (None, FAMILY_UNKNOWN_TEXT)):
            with self.subTest(terrain=terrain):
                failure, lane = self.failure(FamilyNotVerified(family_gate(terrain), "restore_original"))
                self.assertEqual((failure.code, failure.safe_message, failure.retryable, lane.context_blocks,
                                  failure.recovery_owner), ("UNSUPPORTED_SNAPSHOT_CONTENT", text, False, [], None))
                self.assertTrue(text.startswith(SHOWN_HOST_TEXTS))
                kind = LANE_KINDS["original"]
                self.assertEqual(error_text(failure.code, failure.safe_message, kind=kind), text)
                self.assertNotEqual(error_text(failure.code, failure.safe_message, kind=kind),
                                    ERROR_TEXTS[failure.code])
        envelope = apply_error(FamilyNotVerified(family_gate("enoch"), "restore_original"))
        self.assertEqual(envelope.details, {"reason": "family_not_verified", "operation": "restore_original",
                                            "terrain_id": "enoch"})

    def test_failures_map_to_existing_codes_and_recovery_blocks_under_the_editor_kind(self) -> None:
        """Stale review, refused review, a late edit and recovery; the block is lifted by the 8.5 recovery."""
        failure, _lane = self.failure(PreviewStale(RESTORE_STALE_TEXT), "previous")
        self.assertEqual((failure.code, failure.retryable, failure.safe_message),
                         ("REVISION_CONFLICT", True, RESTORE_STALE_TEXT))
        failure, _lane = self.failure(RestoreRefused(NO_ORIGINAL, NO_ORIGINAL_TEXT))
        self.assertEqual((failure.code, failure.safe_message), ("INVALID_REQUEST", NO_ORIGINAL_TEXT))
        self.assertEqual(apply_error(RestoreRefused(NO_ORIGINAL, NO_ORIGINAL_TEXT)).details["reason"], NO_ORIGINAL)
        late = MissionMapPublicationError("REVISION_CONFLICT", TARGET_CHANGED_TEXT.format(path="mapgroupdirt.xml"))
        late.details, late.retryable = {"reason": "target_changed"}, True  # type: ignore[attr-defined]
        failure, lane = self.failure(late, "previous")
        self.assertEqual((failure.code, failure.retryable, lane.context_blocks, failure.recovery_owner),
                         ("REVISION_CONFLICT", True, [], None))
        recovery = MissionMapPublicationError("RECOVERY_REQUIRED", "Mission map records require recovery.",
                                              recovery_required=True)
        for mode in ("original", "previous"):
            with self.subTest(mode=mode):
                failure, lane = self.failure(recovery, mode)
                self.assertEqual((failure.code, failure.recovery_required), ("RECOVERY_REQUIRED", True))
                # Both blocks belong to the editor kind: the coordinator's and the lane's of the failure owner
                self.assertEqual((lane.blocks, lane.context_blocks), ([], [(RECOVERY_UNRESOLVED, EDITOR_KIND)]))
                self.assertEqual(failure.recovery_owner, EDITOR_KIND)

    def test_activity_view_shows_the_restore_refusals(self) -> None:
        """Refusals of a restore show their own sentence under both kinds, never the request text."""
        for kind in LANE_KINDS.values():
            for text in (NO_ORIGINAL_TEXT, NO_PREVIOUS_STATE_TEXT, INVALID_MODE_TEXT):
                with self.subTest(kind=kind, text=text[:30]):
                    self.assertEqual(error_text("INVALID_REQUEST", text, kind=kind), text)


if __name__ == "__main__":
    unittest.main()
