"""Refusals of leaf 8.7 before any write: running server, unverified family, stale preview, path change."""

from __future__ import annotations

import dataclasses
import unittest

from dayz_serverman.application.mission_map_apply import MissionMapApply
from dayz_serverman.application.mission_map_apply_choices import ApplyRefused
from dayz_serverman.application.mission_map_plans import MissionPathChanged
from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerState
from dayz_serverman.domain.mission_map_families import FamilyNotVerified, NOT_VERIFIED_TEXT
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import association_folder
from dayz_serverman.repositories.mission_map_terrain import read_capabilities
from tests.mission_map_apply_support import NOW, ApplyFixture
from tests.mission_map_mapgroup_support import zone
from tests.mission_map_preview_support import UNVERIFIED
from tests.test_mission_map_infected import encounter

# One included infected encounter whose event the mission offers
INFECTED = encounter("e1", x=600.0, z=600.0, radius=40.0)


class GuardRefusalTests(ApplyFixture):
    """The guard, the family gate and the fingerprint refuse before the journal starts."""

    def setUp(self) -> None:
        """Save a plan that changes four files."""
        super().setUp()
        self.save([zone("rich"), INFECTED], 25)

    def assert_nothing_written(self, before: tuple) -> None:
        """Require the DayZ root and the manager root to be byte-equal, with no journal and no record."""
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.folder().joinpath("applied.json").exists())

    def test_running_server_refuses(self) -> None:
        """A server that is not proved STOPPED refuses under the mutex; nothing is written."""
        for state in (ServerState.RUNNING_MANAGED, ServerState.RUNNING_EXTERNAL, ServerState.UNKNOWN):
            with self.subTest(state=state):
                self.ports.state = state
                before = self.snapshot()
                self.events.clear()
                with self.assertRaises(LifecycleFailure):
                    self.run_apply()
                self.assert_nothing_written(before)
                self.assertEqual(self.events, ["mutex", "stopped", "release mutex"])

    def test_unverified_family_refuses_with_the_gate_text(self) -> None:
        """An Apply on enoch raises the 6.11 refusal of operation apply; no guard is taken, nothing is written."""
        self.save([INFECTED], identifier="other")
        before = self.snapshot()
        self.events.clear()
        with self.assertRaises(FamilyNotVerified) as refused:
            self.run_apply(identifier="other")
        self.assertEqual((refused.exception.operation, str(refused.exception)),
                         ("apply", NOT_VERIFIED_TEXT.format(family="enoch")))
        self.assertEqual((self.snapshot(), self.events), (before, []))
        self.assertTrue(UNVERIFIED.endswith(".enoch"))

    def test_family_gate_runs_inside_the_guard_on_its_own_read(self) -> None:
        """The lane's own capability read under the guard decides; a family that changes refuses there."""
        reads: list[str] = []

        def capabilities(mission):
            """Read the real mission and report another family, as a changed folder would."""
            reads.append(mission.name)
            return dataclasses.replace(read_capabilities(mission), terrain_id="enoch")
        self.service = MissionMapApply(self.preview, self.commit, self.paths, self.profiles, clock=lambda: NOW,
                                       capabilities=capabilities)
        before = self.snapshot()
        self.events.clear()
        with self.assertRaises(FamilyNotVerified):
            self.run_apply()
        self.assertEqual(reads, ["dayzOffline.chernarusplus"])
        self.assertEqual(self.events, ["mutex", "stopped", "release mutex"])
        self.assert_nothing_written(before)

    def test_stale_preview_refuses(self) -> None:
        """Another fingerprint, or a change while the guard is taken, refuses with the retryable conflict."""
        before = self.snapshot()
        with self.assertRaises(PreviewStale):
            self.run_apply(fingerprint="0" * 64)
        fingerprint = self.review().fingerprint
        self.write("db/events.xml", (self.mission / "db/events.xml").read_bytes() + b"\n")
        with self.assertRaises(PreviewStale):
            self.run_apply(fingerprint=fingerprint)
        self.write("db/events.xml", (self.mission / "db/events.xml").read_bytes()[:-1])
        self.assert_nothing_written(before)
        status = self.ports.status

        def changed_status():
            """Change a mission input after the review that Apply built from, as an editor would."""
            self.write("cfglimitsdefinition.xml", (self.mission / "cfglimitsdefinition.xml").read_bytes() + b" ")
            return status()
        self.ports.status = changed_status
        with self.assertRaises(PreviewStale):
            self.run_apply()
        self.assertFalse(self.folder().joinpath("applied.json").exists())
        self.assertEqual(self.snapshot()[0]["mpmissions\\dayzOffline.chernarusplus\\mapgroupproto.xml"],
                         before[0]["mpmissions\\dayzOffline.chernarusplus\\mapgroupproto.xml"])

    def test_unchanged_reapply_proves_the_server_state_the_gate_and_the_review(self) -> None:
        """The UNCHANGED path also refuses a running server, an unverified family and a stale review in the guard."""
        self.run_apply()
        before = self.snapshot()
        self.ports.state = ServerState.RUNNING_MANAGED
        with self.assertRaises(LifecycleFailure):
            self.run_apply()
        self.ports.state = ServerState.STOPPED
        verified = self.service
        self.service = MissionMapApply(self.preview, self.commit, self.paths, self.profiles, clock=lambda: NOW,
                                       capabilities=lambda mission: dataclasses.replace(read_capabilities(mission),
                                                                                        terrain_id="enoch"))
        with self.assertRaises(FamilyNotVerified):
            self.run_apply()
        self.service = verified
        status = self.ports.status

        def changed_status():
            """Change a mission input after the review, while the guard is taken."""
            self.write("cfglimitsdefinition.xml", (self.mission / "cfglimitsdefinition.xml").read_bytes() + b" ")
            return status()
        fingerprint = self.review().fingerprint
        self.ports.status = changed_status
        with self.assertRaises(PreviewStale):
            self.run_apply(fingerprint=fingerprint)
        self.ports.status = status
        self.write("cfglimitsdefinition.xml", (self.mission / "cfglimitsdefinition.xml").read_bytes()[:-1])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.run_apply()["result"], "UNCHANGED")

    def test_refused_preview_refuses(self) -> None:
        """A review with a refusal (here a capture refusal of a reserved name) refuses Apply without a write."""
        self.write("mapgrouppos.xml", (self.mission / "mapgrouppos.xml").read_bytes().replace(
            b"Land_House", b"ServerManMap_G_000000000000", 1))
        before = self.snapshot()
        with self.assertRaises(ApplyRefused) as refused:
            self.run_apply()
        self.assertEqual((refused.exception.reason, refused.exception.path), ("preview_refused", "mapgrouppos.xml"))
        self.assert_nothing_written(before)


class PathChangeTests(ApplyFixture):
    """The path-change state of a retired active folder refuses saves and Apply and creates no folder."""

    def test_retired_active_folder_refuses_save_and_apply(self) -> None:
        """The retired marker alone starts the state, also when the successor names another mission root."""
        self.save([zone("rich")])
        self.run_apply()
        plan = self.plans.load("main")["plan"]
        successor = association_folder(self.area, "main", "f" * 32)
        successor.mkdir()
        VersionedJsonRepository(successor / "plan.json", 1).save(
            {"plan": dict(plan, association=dict(plan["association"], mission_root="mpmissions\\elsewhere",
                                                 mission_key="f" * 32))}, None)
        VersionedJsonRepository(self.folder() / "retired.json", 1).save(
            {"operation_id": "adopt-1", "retired_at": "2026-10-15T07:00:00Z", "new_mission_key": "f" * 32}, None)
        before = self.snapshot()
        with self.assertRaises(MissionPathChanged) as refused:
            self.run_apply(fingerprint="0" * 64)
        self.assertEqual(refused.exception.reason, "returned_to_earlier_path")
        with self.assertRaises(MissionPathChanged):
            self.save([zone("rich")], expected=0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(path.name for path in self.folder().parent.iterdir()),
                         sorted([self.key(), "f" * 32]))

    def test_commit_refuses_a_retired_folder_found_under_the_lock(self) -> None:
        """A marker written after the review is found by the commit step under the association lock."""
        self.save([zone("rich")])
        self.run_apply()
        self.save([zone("very_rich")], expected=0)
        status = self.ports.status

        def retire():
            """Retire the active folder while the guard is taken."""
            VersionedJsonRepository(self.folder() / "retired.json", 1).save(
                {"operation_id": "adopt-2", "retired_at": "2026-10-15T07:00:00Z", "new_mission_key": "e" * 32},
                None)
            return status()
        self.ports.status = retire
        before = (self.mission / "mapgroupproto.xml").read_bytes()
        with self.assertRaises(MissionPathChanged):
            self.run_apply()
        self.assertEqual((self.mission / "mapgroupproto.xml").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
