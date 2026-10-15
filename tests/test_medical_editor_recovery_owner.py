"""The recovery owner of a failed operation on the real operation lane (8.13 Architect finding F1).

A coordinated medical toggle that needs recovery blocks only under the map editor kind, so the 8.5
recover handler lifts every block it set. An ordinary medical failure and a failure without an owner
block under their own kind, as before. A failed toggle carries the code, the message and the retry flag
of `_error` in the lane record. A cancellation at a safe point ends CANCELLED (Architect 2026-10-15
15:02:04 in evidence 8.13).
"""

from __future__ import annotations

import hashlib
import threading
import time
import unittest
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.medical_feature_coordinator import MedicalFeatureCoordinator
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND, MissionMapRecovery
from dayz_serverman.application.mission_map_recovery_coordinator import MissionMapRecoveryCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import OperationFailure, OperationState, QueueUnavailable
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.domain.medical_editor_texts import FINGERPRINT_STALE_TEXT
from tests.medical_editor_support import PROTO, ZONES, ToggleFixture

KIND = "APPLY_MEDICAL_FEATURE"
FIELDS = ("profile_id", "feature", "enabled", "expected_profile_revision", "expected_settings_revision",
          "expected_digest")
TERMINAL = (OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED, OperationState.RECOVERY_REQUIRED)


class LaneFixture(ToggleFixture):
    """The toggle fixture with the real operation lane and the 8.5 recovery coordinator."""

    def setUp(self) -> None:
        """Build the lane over the fixture's manager root."""
        super().setUp()
        self.lane = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(self.lane.shutdown, 5)
        self.coordinator = MedicalFeatureCoordinator(self.medical, self.lane)

    def finished(self, operation_id: str) -> Any:
        """Wait until the operation ends and return its record."""
        for _ in range(1500):
            record = self.lane.get(operation_id)
            if record.state in TERMINAL:
                break
            time.sleep(0.02)
        for _ in range(500):
            if self.lane.is_drained():
                break
            time.sleep(0.02)
        return self.lane.get(operation_id)

    def blocks(self) -> list[tuple[str, str | None]]:
        """Return the reason and owner of every lane block."""
        return [entry[:2] for entry in self.lane._recovery_blocks._entries]

    def zone_apply(self, enabled: bool) -> str:
        """Submit the reviewed zone toggle through the bridge coordinator and return its operation ID."""
        request = self.request(enabled)
        fingerprint = self.medical.preview(*request).get("preview_fingerprint")
        parameters = dict(zip(("profile_id", "feature", "enabled", "expected_profile_revision",
                               "expected_settings_revision", "expected_digest"), request))
        if fingerprint is not None:
            parameters["preview_fingerprint"] = fingerprint
        return self.coordinator.apply(parameters)["operation_id"]

    def recover(self) -> dict[str, Any]:
        """Run the 8.5 on-demand recover handler over the fixture's guard ports and locks."""
        settings = SimpleNamespace(load=lambda: self.settings)
        service = MissionMapRecovery(self.paths, settings, InstallationGuard(self.ports, self.ports),  # type: ignore[arg-type]
                                     self.ports, self.plans._locks)
        return MissionMapRecoveryCoordinator(service, self.lane).inspect_mission_map_recovery({})


class ToggleOwnerTests(LaneFixture):
    """A failed toggle blocks only under the editor kind; the 8.5 recover handler opens the lane again."""

    def test_failed_toggle_blocks_under_the_editor_kind_and_recovery_lifts_it(self) -> None:
        """Record publication fails after COMMITTING; recovery finishes it, and the lane admits work again."""
        self.applied_with_medical()
        broken = [True]

        def fail_publish(phase: str, _index: int) -> None:
            """Fail every record publication while the operation runs."""
            if phase == "AFTER_PUBLISH_RECORD" and broken[0]:
                raise OSError("record publication fault")
        self.fault = fail_publish
        record = self.finished(self.zone_apply(True))
        self.assertEqual(record.state, OperationState.RECOVERY_REQUIRED)
        self.assertEqual((record.terminal_error.code, record.terminal_error.retryable), ("RECOVERY_REQUIRED", False))
        self.assertTrue(self.blocks())
        self.assertEqual({owner for _reason, owner in self.blocks()}, {EDITOR_KIND})
        with self.assertRaises(QueueUnavailable):
            self.lane.submit(KIND, lambda _context: {})
        broken[0] = False
        self.assertFalse(self.recover()["blocked"])
        self.assertEqual(self.blocks(), [])
        self.assertEqual(self.applied_record()[0]["operation_kind"], "medical")
        # The lane admits a new submission: the toggle back to off commits
        record = self.finished(self.zone_apply(False))
        self.assertEqual(record.state, OperationState.SUCCEEDED)


class OtherOwnerTests(LaneFixture):
    """Failures outside the editor journal keep their own kind as owner."""

    def test_ordinary_medical_failure_blocks_under_its_kind(self) -> None:
        """A layout refusal of the ordinary toggle needs recovery under the medical kind, as before 8.13."""
        request = self.request(True)
        (self.mission / "mapgroupproto.xml").write_bytes("<prototype/>".encode("utf-16"))
        digest = hashlib.sha256((self.mission / "mapgroupproto.xml").read_bytes()).hexdigest()
        parameters = dict(zip(("profile_id", "feature", "enabled", "expected_profile_revision",
                               "expected_settings_revision", "expected_digest"), request[:5] + (digest,)))
        self.assertEqual(parameters["feature"], ZONES)
        record = self.finished(self.coordinator.apply(parameters)["operation_id"])
        self.assertEqual((record.state, record.terminal_error.code), (OperationState.RECOVERY_REQUIRED,
                                                                      "RECOVERY_REQUIRED"))
        self.assertEqual({owner for _reason, owner in self.blocks()}, {KIND})

    def test_failure_without_and_with_an_owner(self) -> None:
        """Without an owner the lane kind owns the block; a named owner owns it instead."""
        def failing(owner: str | None):
            """Return work that fails with recovery required."""
            def work(_context: Any) -> None:
                raise OperationFailure("RECOVERY_REQUIRED", "Needs recovery.", recovery_required=True,
                                       recovery_owner=owner)
            return work
        record = self.finished(self.lane.submit("CREATE_BACKUP", failing(None)).operation_id)
        self.assertEqual(record.state, OperationState.RECOVERY_REQUIRED)
        self.assertEqual(self.blocks(), [("Needs recovery.", "CREATE_BACKUP")])
        self.lane.clear_recovery_block("CREATE_BACKUP")
        self.finished(self.lane.submit("CREATE_BACKUP", failing("RESTORE_BACKUP")).operation_id)
        self.assertEqual(self.blocks(), [("Needs recovery.", "RESTORE_BACKUP")])


class LaneFieldTests(LaneFixture):
    """The lane record of a failed toggle has the code, the message and the retry flag of _error."""

    def parameters(self, enabled: bool, fingerprint: bool = True) -> dict[str, Any]:
        """Return the bridge parameters of a toggle, with the fingerprint of its review when one exists."""
        request = self.request(enabled)
        parameters: dict[str, Any] = dict(zip(FIELDS, request))
        found = self.medical.preview(*request).get("preview_fingerprint") if fingerprint else None
        if found is not None:
            parameters["preview_fingerprint"] = found
        return parameters

    def failure(self, parameters: dict[str, Any]) -> tuple[Any, ...]:
        """Submit the toggle and return its state, code, message and retry flag; no block stays."""
        record = self.finished(self.coordinator.apply(parameters)["operation_id"])
        self.assertEqual(self.blocks(), [])
        error = record.terminal_error
        return record.state, error.code, error.message, error.retryable

    def outside_edit(self) -> None:
        """Edit mapgroupproto.xml from outside."""
        (self.mission / PROTO).write_bytes((self.mission / PROTO).read_bytes() + b"\n<!-- outside -->\n")

    def test_stale_review_under_the_guard(self) -> None:
        """An outside edit after the first check fails retryable with the stale text."""
        self.applied_with_medical()

        def edit(phase: str, _index: int) -> None:
            """Edit the loot-zone file after the first check."""
            if phase == "AFTER_REVALIDATE":
                self.fault = None
                self.outside_edit()
        self.fault = edit
        self.assertEqual(self.failure(self.parameters(True)),
                         (OperationState.FAILED, "REVISION_CONFLICT", FINGERPRINT_STALE_TEXT, True))

    def test_stale_review_before_the_guard(self) -> None:
        """An outside edit after the review and before the Apply fails the same way (Product Owner 14:59:22)."""
        self.applied_with_medical()
        parameters = self.parameters(True)
        self.outside_edit()
        self.assertEqual(self.failure(parameters),
                         (OperationState.FAILED, "REVISION_CONFLICT", FINGERPRINT_STALE_TEXT, True))

    def test_value_error_before_the_guard(self) -> None:
        """A ValueError before the guard on an editor-managed mission with a fingerprint stays INVALID_REQUEST."""
        self.applied_with_medical()
        parameters = self.parameters(True)
        self.assertIn("preview_fingerprint", parameters)
        self.settings.dayz_root = None
        self.assertEqual(self.failure(parameters),
                         (OperationState.FAILED, "INVALID_REQUEST", "DayZ root is not configured", False))

    def test_ordinary_change_before_the_apply(self) -> None:
        """On an ordinary mission a change between the preview and the Apply fails retryable."""
        self.set_medical_baseline()
        parameters = self.parameters(True)
        self.assertNotIn("preview_fingerprint", parameters)
        self.outside_edit()
        self.assertEqual(self.failure(parameters), (OperationState.FAILED, "REVISION_CONFLICT",
                                                    "medical feature target changed after it was loaded", True))

    def test_ordinary_same_state(self) -> None:
        """On an ordinary mission a same-state refusal fails without the retry flag."""
        self.set_medical_baseline()
        self.assertEqual(self.failure(self.parameters(False, fingerprint=False)), (
            OperationState.FAILED, "INVALID_REQUEST", "medical feature already has the requested state", False))

    def test_cancellation_at_the_loaded_safe_point(self) -> None:
        """A cancellation requested before the safe point "loaded" ends CANCELLED, without error or block."""
        self.set_medical_baseline()
        parameters = self.parameters(True)
        entered, release = threading.Event(), threading.Event()
        validated = self.medical._validated_context

        def held(*args: Any) -> Any:
            """Hold the toggle before its first safe point until the test requested the cancellation."""
            entered.set()
            self.assertTrue(release.wait(10))
            return validated(*args)
        self.medical._validated_context = held  # type: ignore[method-assign]
        operation_id = self.coordinator.apply(parameters)["operation_id"]
        self.assertTrue(entered.wait(10))
        self.lane.request_cancellation(operation_id)
        release.set()
        record = self.finished(operation_id)
        self.assertEqual((record.state, record.terminal_error, self.blocks()), (OperationState.CANCELLED, None, []))


if __name__ == "__main__":
    unittest.main()
