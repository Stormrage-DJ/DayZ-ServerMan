"""The APPLY_MISSION_MAP lane operation of leaf 8.7: submission, failure codes, recovery block and activity wording."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.activity_wording import ERROR_TEXTS, KIND_SHOWN_TEXTS, SHOWN_HOST_TEXTS, error_text
from dayz_serverman.application.mission_map_apply_choices import (
    CHOICE_ENCOUNTERS_TEXT, CHOICE_FILES_TEXT, ApplyRefused,
)
from dayz_serverman.application.mission_map_apply_coordinator import MissionMapApplyCoordinator, apply_error
from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND, RECOVERY_UNRESOLVED
from dayz_serverman.application.operations.models import OperationFailure, QueueUnavailable
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain.lifecycle import LifecycleFailure
from dayz_serverman.domain.mission_map_families import (
    FAMILY_UNKNOWN_TEXT, NOT_VERIFIED_TEXT, FamilyNotVerified, family_gate,
)
from dayz_serverman.domain.mission_map_preview_texts import NO_SAVED_PLAN_TEXT, PREVIEW_STALE_TEXT
from dayz_serverman.repositories.mission_map_incorporate import OWNED_VALUE_TEXT
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError

# A valid request of the lane operation
REQUEST = {"profile_id": "main", "preview_fingerprint": "a" * 64,
           "choices": {"external_changes": {}, "unavailable_encounters": {}}}


class Lane:
    """A fake lane that keeps the submitted work and runs it with a recording context."""

    def __init__(self, refusal: QueueUnavailable | None = None) -> None:
        """Start without work; a refusal refuses the submission."""
        self.refusal, self.kind, self.work, self.blocks = refusal, None, None, []

    def submit(self, kind: str, work: Any, **_options: Any) -> Any:
        """Keep the work, or refuse it as a full or blocked lane does."""
        if self.refusal is not None:
            raise self.refusal
        self.kind, self.work = kind, work
        return SimpleNamespace(operation_id="op1", state=SimpleNamespace(value="ACCEPTED"))

    def run(self) -> Any:
        """Run the work with a context of operation op1."""
        return self.work(SimpleNamespace(operation_id="op1", block_for_recovery=self.blocks.append))


class Service:
    """A fake Apply service that returns a result or raises one failure."""

    def __init__(self, failure: Exception | None = None) -> None:
        """Keep the failure; None succeeds."""
        self.failure, self.calls = failure, []

    def apply(self, profile_id: object, fingerprint: object, choices: object, *, operation_id: str) -> dict:
        """Record the call, then raise or return."""
        self.calls.append((profile_id, fingerprint, choices, operation_id))
        if self.failure is not None:
            raise self.failure
        return {"operation_id": operation_id, "result": "COMMITTED"}


class LaneTests(unittest.TestCase):
    """Submission under the editor kind, the request shape and the failures of the work."""

    def failure(self, error: Exception) -> tuple[OperationFailure, Lane]:
        """Run the work with a service that raises the error and return the operation failure."""
        lane = Lane()
        MissionMapApplyCoordinator(Service(error), lane).apply_mission_map(dict(REQUEST))  # type: ignore[arg-type]
        with self.assertRaises(OperationFailure) as failed:
            lane.run()
        return failed.exception, lane

    def test_submission_and_success(self) -> None:
        """The kind is APPLY_MISSION_MAP; the work passes the request and the operation ID to the service."""
        lane, service = Lane(), Service()
        handle = MissionMapApplyCoordinator(service, lane).apply_mission_map(dict(REQUEST))  # type: ignore[arg-type]
        self.assertEqual((handle, lane.kind), ({"operation_id": "op1", "state": "ACCEPTED"}, EDITOR_KIND))
        self.assertEqual(lane.run(), {"operation_id": "op1", "result": "COMMITTED"})
        self.assertEqual(service.calls, [("main", "a" * 64, REQUEST["choices"], "op1")])

    def test_malformed_request_and_a_refused_submission(self) -> None:
        """Other fields or a fingerprint that is not text refuse; a full or blocked lane is a mutation conflict."""
        coordinator = MissionMapApplyCoordinator(Service(), Lane())  # type: ignore[arg-type]
        for request in ({"profile_id": "main"}, dict(REQUEST, preview_fingerprint=None), dict(REQUEST, extra=1)):
            with self.assertRaises(ApplicationCallError) as refused:
                coordinator.apply_mission_map(request)
            self.assertEqual(refused.exception.code, ErrorCode.INVALID_REQUEST)
        blocked = MissionMapApplyCoordinator(Service(), Lane(QueueUnavailable("blocked", "RECOVERY_BLOCK", EDITOR_KIND)))
        with self.assertRaises(ApplicationCallError) as refused:
            blocked.apply_mission_map(dict(REQUEST))  # type: ignore[arg-type]
        self.assertEqual((refused.exception.code, refused.exception.retryable), (ErrorCode.MUTATION_CONFLICT, True))

    def test_family_refusal_keeps_the_6_11_text_in_the_activity_view(self) -> None:
        """UNSUPPORTED_SNAPSHOT_CONTENT with the gate text; the activity view shows it, never the backup text."""
        for terrain, text in (("enoch", NOT_VERIFIED_TEXT.format(family="enoch")), (None, FAMILY_UNKNOWN_TEXT)):
            with self.subTest(terrain=terrain):
                failure, lane = self.failure(FamilyNotVerified(family_gate(terrain), "apply"))
                self.assertEqual((failure.code, failure.safe_message, failure.retryable, lane.blocks),
                                 ("UNSUPPORTED_SNAPSHOT_CONTENT", text, False, []))
                self.assertTrue(text.startswith(SHOWN_HOST_TEXTS))
                self.assertEqual(error_text(failure.code, failure.safe_message, kind=EDITOR_KIND), text)
                self.assertNotEqual(error_text(failure.code, failure.safe_message), ERROR_TEXTS[failure.code])
        envelope = apply_error(FamilyNotVerified(family_gate("enoch"), "apply"))
        self.assertEqual(envelope.details, {"reason": "family_not_verified", "operation": "apply",
                                            "terrain_id": "enoch"})

    def test_failures_map_to_existing_codes_and_recovery_blocks_the_lane(self) -> None:
        """Running server, stale preview, refused choices, a late edit and recovery each keep their meaning."""
        running = LifecycleFailure("CONTROL_CONFLICT", "Applying mods requires STOPPED; current state is RUNNING_MANAGED.")
        failure, lane = self.failure(running)
        self.assertEqual((failure.code, lane.blocks), ("CONTROL_CONFLICT", []))
        self.assertEqual(error_text(failure.code, failure.safe_message), "This cannot be done while the server is running.")
        failure, _lane = self.failure(PreviewStale("review the preview again"))
        self.assertEqual((failure.code, failure.retryable), ("REVISION_CONFLICT", True))
        refused = apply_error(ApplyRefused("choice_required", "Choose.", path=None))
        self.assertEqual((refused.code, refused.details["reason"]), (ErrorCode.INVALID_REQUEST, "choice_required"))
        late = MissionMapPublicationError("REVISION_CONFLICT", "Apply stopped because mapgroupproto.xml changed.")
        late.details, late.retryable = {"reason": "target_changed"}, True
        self.assertEqual((apply_error(late).code, apply_error(late).details["reason"], apply_error(late).retryable),
                         (ErrorCode.REVISION_CONFLICT, "target_changed", True))
        recovery = MissionMapPublicationError("RECOVERY_REQUIRED", "Mission map records require recovery.",
                                              recovery_required=True)
        failure, lane = self.failure(recovery)
        self.assertEqual((failure.code, failure.recovery_required, lane.blocks),
                         ("RECOVERY_REQUIRED", True, [RECOVERY_UNRESOLVED]))
        self.assertEqual(apply_error(MissionMapPublicationError("PATH_INVALID", "unsafe")).code, ErrorCode.PATH_INVALID)


class ProductOwnerTextTests(unittest.TestCase):
    """The texts of the Product Owner entry of 2026-10-15 01:16:10 in evidence 8.7, in both catalogues."""

    def test_texts_word_for_word(self) -> None:
        """Refused Keep the change, both choice texts, the stale text and the no-plan text."""
        self.assertEqual(OWNED_VALUE_TEXT.format(path="mapgroupproto.xml"), (
            "The change to mapgroupproto.xml affects values that the map editor sets, so the map editor cannot keep "
            "this change. Use the map editor to change these values. You can still replace the file. DayZ-ServerMan "
            "saves a copy of the current file before it replaces the file."))
        self.assertEqual(CHOICE_FILES_TEXT, (
            "Apply needs a choice for each mission file that changed outside the map editor: keep the change or "
            "replace the file. Make a choice for each of these files, then apply again."))
        self.assertEqual(CHOICE_ENCOUNTERS_TEXT, (
            "Apply cannot write an encounter that is not available on this mission. Exclude each of these "
            "encounters from Apply, then apply again."))
        self.assertEqual(PREVIEW_STALE_TEXT, (
            "The map plan, the profile, the settings or the mission files changed after the preview. Nothing was "
            "applied. Preview the changes again and check them before you apply."))
        self.assertEqual(NO_SAVED_PLAN_TEXT,
                         "This mission has no saved map plan. Make a plan in the map editor, then preview it.")

    def test_activity_view_of_the_apply_kind(self) -> None:
        """Stale reviews and late edits get the stale text; refusals show their own text, never the request text."""
        self.assertEqual(error_text("REVISION_CONFLICT", "the plan changed", kind=EDITOR_KIND), PREVIEW_STALE_TEXT)
        self.assertEqual(error_text("REVISION_CONFLICT", "x y"), ERROR_TEXTS["REVISION_CONFLICT"])
        for text in (OWNED_VALUE_TEXT.format(path="env/zombie_territories.xml"), CHOICE_FILES_TEXT,
                     CHOICE_ENCOUNTERS_TEXT, NO_SAVED_PLAN_TEXT):
            with self.subTest(text=text[:30]):
                self.assertEqual(error_text("INVALID_REQUEST", text, kind=EDITOR_KIND), text)
        stale, _lane = LaneTests("test_submission_and_success").failure(PreviewStale(PREVIEW_STALE_TEXT))
        self.assertEqual(error_text(stale.code, stale.safe_message, kind=EDITOR_KIND), PREVIEW_STALE_TEXT)
        refused, _lane = LaneTests("test_submission_and_success").failure(ApplyRefused("choice_required",
                                                                                        CHOICE_FILES_TEXT))
        self.assertEqual(error_text(refused.code, refused.safe_message, kind=EDITOR_KIND), CHOICE_FILES_TEXT)

    def test_frontend_catalogue_has_the_same_rows(self) -> None:
        """operationKindShownTexts holds the same fragments as KIND_SHOWN_TEXTS."""
        script = (Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend" /
                  "operation_messages.js").read_text(encoding="utf-8")
        block = script.split("const operationKindShownTexts = Object.freeze({ APPLY_MISSION_MAP: Object.freeze([", 1)
        fragments = tuple(re.findall(r'"([^"]+)"', block[1].split("]) });", 1)[0]))
        self.assertEqual(fragments, KIND_SHOWN_TEXTS[EDITOR_KIND])
        self.assertIn("operationKindShownTexts[kind].some((part) => message.includes(part))", script)


if __name__ == "__main__":
    unittest.main()
