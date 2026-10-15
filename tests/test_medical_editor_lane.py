"""The zone Apply request, its failures and its recovery block, the kind's records and its wording (8.13)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.activity_wording import ERROR_TEXTS, error_text
from dayz_serverman.application.medical_editor_records import build_toggle
from dayz_serverman.application.medical_feature_coordinator import MedicalFeatureCoordinator
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND, RECOVERY_UNRESOLVED
from dayz_serverman.application.operations.models import OperationFailure
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain.lifecycle import LifecycleFailure
from dayz_serverman.domain.medical_editor_texts import (
    FINGERPRINT_MISSING_TEXT, FINGERPRINT_STALE_TEXT, MEDICAL_KIND, MISMATCH_TEXT, NO_APPLIED_PLAN_TEXT, ZoneReviewStale,
    ZoneToggleRefused,
)
from dayz_serverman.domain.mission_map_ledger import parse_entry
from dayz_serverman.domain.mission_map_records import RecordShapeError, parse_applied, sealed
from dayz_serverman.host.medical_feature_api import MedicalFeatureHostMethods
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.mission_map_journal import MissionMapJournalError
from dayz_serverman.repositories.mission_map_journal_store import parse_journal
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError
from tests.medical_editor_support import ToggleFixture

# A zone Apply of an editor-managed mission: the six fields and the reviewed fingerprint
REQUEST = {"profile_id": "main", "feature": "medical_loot_zones", "enabled": True, "expected_profile_revision": 3,
           "expected_settings_revision": 4, "expected_digest": "d" * 64, "preview_fingerprint": "a" * 64}
KIND = "APPLY_MEDICAL_FEATURE"


class Lane:
    """A fake lane that keeps the submitted work and the recovery blocks of its manager."""

    def __init__(self) -> None:
        """Start without work."""
        self.kind, self.work, self.blocks = None, None, []

    def submit(self, kind: str, work: Any, **_options: Any) -> Any:
        """Keep the work."""
        self.kind, self.work = kind, work
        return SimpleNamespace(operation_id="op1", state=SimpleNamespace(value="ACCEPTED"))

    def run(self) -> Any:
        """Run the work with a context of operation op1 that records its recovery blocks."""
        block = lambda message, owner=None: self.blocks.append((message, owner))  # noqa: E731
        return self.work(SimpleNamespace(operation_id="op1", checkpoint=lambda *_: None, block_for_recovery=block))


class Service:
    """A fake medical service that records the apply call and raises one failure."""

    def __init__(self, failure: Exception | None = None) -> None:
        """Keep the failure; None succeeds."""
        self.failure, self.calls = failure, []

    def apply(self, **arguments: Any) -> dict:
        """Record the call, then raise or return."""
        self.calls.append(arguments)
        if self.failure is not None:
            raise self.failure
        return {"changed_fields": ["medical_loot_zones"]}


class RequestTests(unittest.TestCase):
    """The seven-field zone Apply; item spawns and malformed fingerprints are refused before the lane."""

    def submit(self, request: dict[str, Any], failure: Exception | None = None) -> tuple[Lane, Service]:
        """Submit one request to a fake lane."""
        lane, service = Lane(), Service(failure)
        MedicalFeatureCoordinator(service, lane).apply(request)  # type: ignore[arg-type]
        return lane, service

    def test_fingerprint_and_operation_reach_the_service(self) -> None:
        """The work passes the fingerprint and the lane's operation ID; six fields pass no fingerprint."""
        lane, service = self.submit(dict(REQUEST))
        self.assertEqual(lane.kind, KIND)
        lane.run()
        self.assertEqual((service.calls[0]["preview_fingerprint"], service.calls[0]["operation_id"]), ("a" * 64, "op1"))
        lane, service = self.submit({key: value for key, value in REQUEST.items() if key != "preview_fingerprint"})
        lane.run()
        self.assertIsNone(service.calls[0]["preview_fingerprint"])

    def test_refused_requests(self) -> None:
        """Item spawns with a fingerprint, a fingerprint that is no text and an unknown field are invalid."""
        for request in (dict(REQUEST, feature="medical_item_spawns"), dict(REQUEST, preview_fingerprint=None),
                        dict(REQUEST, extra=1)):
            with self.subTest(request=request), self.assertRaises(ApplicationCallError) as caught:
                self.submit(request)
            self.assertEqual(caught.exception.code, ErrorCode.INVALID_REQUEST)

    def test_failures_keep_their_codes(self) -> None:
        """Refusals, staleness, a running server and a recovery need keep the Apply codes and texts."""
        cases = ((ZoneToggleRefused("files_differ", MISMATCH_TEXT), "INVALID_REQUEST", MISMATCH_TEXT),
                 (ZoneToggleRefused("fingerprint_missing", FINGERPRINT_MISSING_TEXT), "INVALID_REQUEST",
                  FINGERPRINT_MISSING_TEXT),
                 (ZoneReviewStale(FINGERPRINT_STALE_TEXT), "REVISION_CONFLICT", FINGERPRINT_STALE_TEXT),
                 (LifecycleFailure("CONTROL_CONFLICT", "Applying mods requires STOPPED; current state is "
                                   "RUNNING_MANAGED."), "CONTROL_CONFLICT",
                  "Applying mods requires STOPPED; current state is RUNNING_MANAGED."))
        for error, code, message in cases:
            with self.subTest(code=code):
                lane, _service = self.submit(dict(REQUEST), error)
                with self.assertRaises(OperationFailure) as failed:
                    lane.run()
                self.assertEqual((failed.exception.code, failed.exception.safe_message), (code, message))
                self.assertEqual(lane.blocks, [])

    def test_recovery_blocks_the_editor_kind(self) -> None:
        """A commit failure that needs recovery blocks under the editor kind; a layout refusal does not block."""
        for error in (MissionMapPublicationError("RECOVERY_REQUIRED", "records", recovery_required=True),
                      MissionMapJournalError("journal")):
            with self.subTest(error=type(error).__name__):
                lane, _service = self.submit(dict(REQUEST), error)
                with self.assertRaises(OperationFailure) as failed:
                    lane.run()
                self.assertEqual((failed.exception.code, failed.exception.recovery_owner),
                                 ("RECOVERY_REQUIRED", EDITOR_KIND))
                self.assertEqual(lane.blocks, [(RECOVERY_UNRESOLVED, EDITOR_KIND)])
        lane, _service = self.submit(dict(REQUEST), ConfigurationFileError("layout"))
        with self.assertRaises(OperationFailure) as failed:
            lane.run()
        self.assertEqual((failed.exception.recovery_required, failed.exception.recovery_owner), (True, None))
        self.assertEqual(lane.blocks, [])


class HostTests(unittest.TestCase):
    """The window host method sends the fingerprint field only when the review gave one."""

    def test_optional_fingerprint(self) -> None:
        """Six positional arguments give six fields; seven give the fingerprint field too."""
        sent: list[tuple[str, dict[str, Any]]] = []
        host = MedicalFeatureHostMethods()
        host._invoke = lambda method, parameters: sent.append((method, parameters))  # type: ignore[attr-defined]
        host.apply_medical_feature("main", "medical_loot_zones", True, 3, 4, "d" * 64)
        host.apply_medical_feature("main", "medical_loot_zones", True, 3, 4, "d" * 64, "a" * 64)
        self.assertNotIn("preview_fingerprint", sent[0][1])
        self.assertEqual(sent[1][1]["preview_fingerprint"], "a" * 64)
        self.assertEqual(set(sent[1][1]) - set(sent[0][1]), {"preview_fingerprint"})


class WordingTests(unittest.TestCase):
    """The activity view shows the refusals unchanged under the medical kind; a running server as for Apply."""

    def test_texts(self) -> None:
        """Plain sentences pass; staleness uses the existing conflict text; the guard text equals Apply's."""
        for text in (MISMATCH_TEXT, NO_APPLIED_PLAN_TEXT, FINGERPRINT_MISSING_TEXT):
            self.assertEqual(error_text("INVALID_REQUEST", text, kind=KIND), text)
        # Until leaf 8.10 adds the stale text to both catalogues, the general conflict text shows (PO 06:27:10)
        self.assertEqual(error_text("REVISION_CONFLICT", FINGERPRINT_STALE_TEXT, kind=KIND),
                         ERROR_TEXTS["REVISION_CONFLICT"])
        running = "Applying mods requires STOPPED; current state is RUNNING_MANAGED."
        self.assertEqual(error_text("CONTROL_CONFLICT", running, kind=KIND),
                         error_text("CONTROL_CONFLICT", running, kind="APPLY_MISSION_MAP"))


class KindRecordTests(ToggleFixture):
    """The journal, applied-record and ledger values of kind `medical` and their parse rules."""

    def setUp(self) -> None:
        """Apply a plan and toggle medical on."""
        super().setUp()
        self.applied_with_medical()
        self.run_toggle(True, operation_id="medical0001")
        self.record = self.applied_record()[0]

    def test_applied_record_rules(self) -> None:
        """A medical record keeps a plan or none, and always names its generated groups."""
        self.assertEqual(parse_applied(self.record), self.record)
        content = {name: value for name, value in self.record.items() if name != "manifest_sha256"}
        for change in ({"generated_groups": None}, {"plan_id": None}, {"global_abundance": None},
                       {"operation_kind": "toggle"}):
            with self.subTest(change=change), self.assertRaises(RecordShapeError):
                parse_applied(sealed(dict(content, **change)))
        restored = dict(content, plan_id=None, plan_revision=None, configuration_fingerprint=None,
                        global_abundance=None, plan_objects=None)
        self.assertEqual(parse_applied(sealed(restored))["operation_kind"], MEDICAL_KIND)
        version_one = {name: value for name, value in content.items() if not name.startswith(("global", "plan_obj",
                                                                                             "generated"))}
        with self.assertRaises(RecordShapeError):
            parse_applied(sealed(dict(version_one, plan_id=None, plan_revision=None, configuration_fingerprint=None)))

    def test_ledger_rules(self) -> None:
        """A medical entry is editor output; a medical entry without output is refused."""
        entry = LedgerStore(self.area).read(self.key(), "medical0001")
        content = {name: value for name, value in entry.items() if name != "manifest_sha256"}
        self.assertTrue(parse_entry(sealed(content), MISSION_FILE_SET)["output"])
        with self.assertRaises(RecordShapeError):
            parse_entry(sealed(dict(content, output=False)), MISSION_FILE_SET)

    def test_journal_rules(self) -> None:
        """A medical journal names only the zone fingerprint; a plan identity or a missing fingerprint is refused."""
        toggle_target = self.medical._validated_context(*self.request(False))[1]
        review = self.toggle.review(toggle_target)
        publication = build_toggle(review, toggle_target, operation_id="probe0001", now="2026-10-15T09:00:00Z",
                                   paths=self.paths)
        value = publication.journal.to_dict()
        self.assertEqual(parse_journal(value, "probe0001").operation_kind, MEDICAL_KIND)
        for change in ({"preview_fingerprint": None}, {"plan_id": "plan1"}, {"plan_fingerprint": "a" * 64}):
            with self.subTest(change=change), self.assertRaises(MissionMapJournalError):
                parse_journal(dict(value, **change), "probe0001")


if __name__ == "__main__":
    unittest.main()
