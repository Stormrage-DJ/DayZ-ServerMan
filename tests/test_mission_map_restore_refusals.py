"""Refusals of leaf 8.8 before any write: running server, family gate, stale review, missing state, path change."""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

from dayz_serverman.application.mission_map_plans import MissionPathChanged
from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.application.mission_map_restore import MissionMapRestore
from dayz_serverman.application.mission_map_restore_review import RestoreReader, RestoreRefused
from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerState
from dayz_serverman.domain.mission_map_families import NOT_VERIFIED_TEXT, FamilyNotVerified
from dayz_serverman.domain.mission_map_restore_texts import (
    INVALID_MODE, NO_ORIGINAL, NO_ORIGINAL_TEXT, NO_PREVIOUS_STATE, RESTORE_STALE_TEXT, TARGET_CHANGED_TEXT,
)
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import association_folder
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError
from dayz_serverman.repositories.mission_map_terrain import read_capabilities
from tests.mission_map_apply_support import NOW
from tests.mission_map_restore_support import PLAN, RestoreFixture

PROTO, DIRT = "mapgroupproto.xml", "mapgroupdirt.xml"


class RestoreRefusalTests(RestoreFixture):
    """The guard, the family gate, the fingerprint and the review refuse before the journal starts."""

    def setUp(self) -> None:
        """Apply the plan once, so both restores have their state."""
        super().setUp()
        self.save(PLAN, 25)
        self.run_apply()
        self.applied = self.records()[2]

    def with_terrain(self, terrain_id: str | None, reads: list[str] | None = None) -> None:
        """Rebuild the restore with a capability reader that reports another family for the real mission."""
        def capabilities(mission: Path):
            """Read the real mission and report another family, as a changed folder would."""
            if reads is not None:
                reads.append(mission.name)
            return dataclasses.replace(read_capabilities(mission), terrain_id=terrain_id)
        self.reader = RestoreReader(self.plans, self.profiles, medical_baseline=self.medical.zone_baseline_path,
                                    capabilities=capabilities)
        self.restorer = MissionMapRestore(self.reader, self.commit, self.paths, self.profiles, clock=lambda: NOW)

    def assert_nothing_written(self, before: tuple) -> None:
        """Require both roots byte-equal and the last-applied record unchanged."""
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.records()[2], self.applied)

    def test_running_server_refuses(self) -> None:
        """A server that is not proved STOPPED refuses under the mutex, also on the UNCHANGED path."""
        for mode in ("original", "previous"):
            for state in (ServerState.RUNNING_MANAGED, ServerState.RUNNING_EXTERNAL, ServerState.UNKNOWN):
                with self.subTest(mode=mode, state=state):
                    self.ports.state = state
                    before = self.snapshot()
                    self.events.clear()
                    with self.assertRaises(LifecycleFailure):
                        self.run_restore(mode)
                    self.assert_nothing_written(before)
                    self.assertEqual(self.events, ["mutex", "stopped", "release mutex"])
        self.ports.state = ServerState.STOPPED
        self.run_restore("original")
        self.ports.state = ServerState.RUNNING_MANAGED
        with self.assertRaises(LifecycleFailure):
            self.run_restore("original")

    def test_unverified_family_refuses_restore_original_only(self) -> None:
        """Restore original raises the 6.11 refusal without a guard; Restore previous state never reads the gate."""
        reads: list[str] = []
        self.with_terrain("deerisle", reads)
        review = self.restore_review("original")
        self.assertEqual([item["reason"] for item in review.answer["refusals"]], ["family_not_verified"])
        self.assertEqual(review.answer["family"]["refused"], ["apply", "restore_original"])
        before = self.snapshot()
        self.events.clear()
        with self.assertRaises(FamilyNotVerified) as refused:
            self.run_restore("original", review.fingerprint)
        self.assertEqual((refused.exception.operation, str(refused.exception)),
                         ("restore_original", NOT_VERIFIED_TEXT.format(family="deerisle")))
        self.assertEqual(self.events, [])
        self.assert_nothing_written(before)
        reads.clear()
        self.assertEqual(self.run_restore("previous")["result"], "COMMITTED")
        self.assertEqual(reads, [])
        self.assertIsNone(self.restore_review("previous").answer["family"])

    def test_family_gate_runs_inside_the_guard_on_its_own_read(self) -> None:
        """A family that changes after the review refuses under the guard, before the journal starts."""
        fingerprint = self.restore_review("original").fingerprint
        reads: list[str] = []
        self.with_terrain(None, reads)
        verified = self.reader.capabilities
        self.reader.capabilities = lambda mission: dataclasses.replace(verified(mission), terrain_id="chernarusplus")
        status = self.ports.status

        def changed():
            """Report no family from now on, as a renamed folder would."""
            self.reader.capabilities = verified
            return status()
        self.ports.status = changed
        before = self.snapshot()
        self.events.clear()
        with self.assertRaises(FamilyNotVerified):
            self.run_restore("original", fingerprint)
        # One read for the review before the guard, and the lane's own read under it
        self.assertEqual(reads, ["dayzOffline.chernarusplus"] * 2)
        self.assertEqual(self.events, ["mutex", "stopped", "release mutex"])
        self.assert_nothing_written(before)

    def test_stale_review_refuses(self) -> None:
        """Another fingerprint, a change after the review and a change under the guard refuse as stale."""
        before = self.snapshot()
        with self.assertRaises(PreviewStale) as stale:
            self.run_restore("previous", "0" * 64)
        self.assertEqual(str(stale.exception), RESTORE_STALE_TEXT)
        # A preview of the other mode does not fit
        with self.assertRaises(PreviewStale):
            self.run_restore("previous", self.restore_review("original").fingerprint)
        fingerprint = self.restore_review("previous").fingerprint
        self.write(PROTO, (self.mission / PROTO).read_bytes() + b"\n")
        with self.assertRaises(PreviewStale):
            self.run_restore("previous", fingerprint)
        self.write(PROTO, (self.mission / PROTO).read_bytes()[:-1])
        self.assert_nothing_written(before)
        status = self.ports.status

        def changed_status():
            """Change a restored file while the guard is taken."""
            self.write(DIRT, (self.mission / DIRT).read_bytes() + b" ")
            return status()
        self.ports.status = changed_status
        with self.assertRaises(PreviewStale):
            self.run_restore("previous")
        self.assertEqual(self.records()[2], self.applied)

    def test_missing_state_and_invalid_mode_refuse(self) -> None:
        """No last-applied record, no original or an unknown mode refuse with a closed reason."""
        with self.assertRaises(RestoreRefused) as refused:
            self.run_restore("previous", identifier="second")
        self.assertEqual(refused.exception.reason, NO_PREVIOUS_STATE)
        with self.assertRaises(RestoreRefused) as refused:
            self.reader.review("main", "latest")
        self.assertEqual(refused.exception.reason, INVALID_MODE)
        self.save([], identifier="other")
        review = self.restore_review("original", "other")
        self.assertEqual([item["reason"] for item in review.answer["refusals"]][0], NO_ORIGINAL)
        self.with_terrain("chernarusplus")
        with self.assertRaises(RestoreRefused) as refused:
            self.run_restore("original", identifier="other")
        self.assertEqual((refused.exception.reason, str(refused.exception)), (NO_ORIGINAL, NO_ORIGINAL_TEXT))

    def test_path_change_state_refuses(self) -> None:
        """A retired active folder with a successor refuses the review and the restore; nothing is written."""
        plan = self.plans.load("main")["plan"]
        successor = association_folder(self.area, "main", "f" * 32)
        successor.mkdir()
        VersionedJsonRepository(successor / "plan.json", 1).save(
            {"plan": dict(plan, association=dict(plan["association"], mission_root="mpmissions\\elsewhere",
                                                 mission_key="f" * 32))}, None)
        VersionedJsonRepository(self.folder() / "retired.json", 1).save(
            {"operation_id": "adopt-1", "retired_at": "2026-10-15T07:00:00Z", "new_mission_key": "f" * 32}, None)
        # The plan service reports the state; it does not raise it
        self.assertIsNotNone(self.plans.load("main")["path_change"])
        before = self.snapshot()
        for mode in ("original", "previous"):
            with self.assertRaises(MissionPathChanged) as refused:
                self.run_restore(mode, "0" * 64)
            self.assertEqual(refused.exception.reason, "returned_to_earlier_path")
        self.assertEqual(self.snapshot(), before)

    def test_late_file_change_rolls_back_with_the_restore_text(self) -> None:
        """An edit of a later group after staging rolls the earlier groups back; the edit stays (A46-F2)."""
        before = self.mission_state()
        edited = (self.mission / DIRT).read_bytes() + b"<!-- admin -->"

        def edit(phase: str, _index: int) -> None:
            """Edit the Dirt file after the journal is durable."""
            if phase == "AFTER_PREPARED":
                self.write(DIRT, edited)
        self.fault = edit
        with self.assertRaises(MissionMapPublicationError) as failed:
            self.run_restore("previous")
        error = failed.exception
        self.assertEqual((error.code, str(error)), ("REVISION_CONFLICT", TARGET_CHANGED_TEXT.format(path=DIRT)))
        self.assertEqual((error.details, error.retryable), ({"reason": "target_changed"}, True))
        self.assertEqual(self.mission_state(), dict(before, **{DIRT: edited}))
        self.assertEqual(self.records()[2], self.applied)
        self.assertFalse(list(self.journals.root.glob("*.json")))


if __name__ == "__main__":
    unittest.main()
