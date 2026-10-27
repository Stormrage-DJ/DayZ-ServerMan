"""Cover the mission map composition builder: one shared lock set, no build-time writes, observer refusal."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.mission_map_locks import AssociationLocks
from dayz_serverman.composition import build_composition
from dayz_serverman.composition_model import SessionMode
from dayz_serverman.mission_map_composition import MissionMapParts, build_mission_map
from dayz_serverman.repositories.json_store import StagingPolicy
from dayz_serverman.repositories.paths import PortablePaths
from tests.test_mission_map_plan import circle, stored_plan
from tests.test_mission_map_plan_service import Profiles, profile


# Longest wait of a test for a thread; a correct run never reaches it
WAIT_SECONDS = 10.0
# Time in which a save that does not wait for the held lock would end
BLOCKED_SECONDS = 0.3


class Unread:
    """Fail the test if the build reads a profile or the settings."""

    def __getattr__(self, name: str) -> Any:
        """Refuse every service call during the build."""
        raise AssertionError(f"the build called {name}")


class MissionMapCompositionTests(unittest.TestCase):
    """Verify the parts that build_mission_map gives to the composition root."""

    def setUp(self) -> None:
        """Create a DayZ root with one mission and a manager root that has no folders yet."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_map_composition_")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.dayz_root = base / "DayZServer"
        (self.dayz_root / "mpmissions" / "dayzOffline.test").mkdir(parents=True)
        self.paths = PortablePaths.from_root(base / "Manager")

    def build(self, staging: StagingPolicy = StagingPolicy.OWNER) -> MissionMapParts:
        """Build the parts over a fake profile service and the temporary DayZ root."""
        settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.dayz_root), revision=1))
        return build_mission_map(self.paths, Profiles(profile("main")), settings,  # type: ignore[arg-type]
                                 staging=staging)

    def plan(self, parts: MissionMapParts) -> dict[str, Any]:
        """Return a valid stored plan of the active association of profile main."""
        return dict(stored_plan([circle("c42")]), association=parts.plans.active_association("main"))

    def test_plan_saves_wait_for_the_shared_lock_instance(self) -> None:
        """A save waits while the lock of the parts is held, so a commit step with that instance excludes it."""
        parts = self.build()
        self.assertIsInstance(parts.locks, AssociationLocks)
        plan = self.plan(parts)
        results: list[dict[str, Any]] = []
        errors: list[Exception] = []

        def save() -> None:
            """Save the plan in another thread and keep its result or error."""
            try:
                results.append(parts.plans.save("main", plan, None))
            except Exception as error:
                # The main thread reports the error
                errors.append(error)

        # Hold the lock that a task 8 commit step would take, then start a save of the same association
        with parts.locks.hold("main", plan["association"]["mission_key"]):
            worker = threading.Thread(target=save, daemon=True)
            worker.start()
            worker.join(BLOCKED_SECONDS)
            self.assertTrue(worker.is_alive(), "the save did not wait for the held association lock")
            self.assertFalse(self.paths.mission_map.exists())
        # After the release the save ends normally at revision 0
        worker.join(WAIT_SECONDS)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["revision"], 0)

    def test_transfer_reads_through_the_same_plan_service(self) -> None:
        """The export of the transfer service shows the plan that the plan service of the parts saved."""
        parts = self.build()
        parts.plans.save("main", self.plan(parts), None)
        self.assertIn('"id": "c42"', parts.transfer.export_text("main"))

    def test_build_creates_no_folder_and_reads_nothing(self) -> None:
        """The build makes no folder, writes no file and calls no service, for an owner and an observer."""
        for staging in StagingPolicy:
            with self.subTest(staging=staging):
                parts = build_mission_map(self.paths, Unread(), Unread(), staging=staging)  # type: ignore[arg-type]
                self.assertIsNot(parts.plans, None)
                self.assertFalse(self.paths.root.exists())
        self.assertEqual(os.listdir(self.temp.name), ["DayZServer"])

    def test_each_build_has_its_own_lock_set(self) -> None:
        """Two compositions do not share a lock set; inside one, plans and commits meet on one instance."""
        first, second = self.build(), self.build()
        self.assertIsNot(first.locks, second.locks)
        self.assertIs(first.locks.lock_for("main", "k"), first.locks.lock_for("main", "k"))

    def test_observer_parts_refuse_plan_writes(self) -> None:
        """An observer build passes the observer policy, so a save is refused and nothing is created."""
        parts = self.build(StagingPolicy.OBSERVER)
        self.assertRaises(PermissionError, parts.plans.save, "main", self.plan(parts), None)
        self.assertEqual(parts.plans.load("main")["state"], "MISSING")
        self.assertFalse(self.paths.mission_map.exists())


class CompositionRootTests(unittest.TestCase):
    """Verify that the composition root exposes the mission map parts without creating their folder."""

    def setUp(self) -> None:
        """Create an empty temporary folder for the manager root."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_map_root_")
        self.addCleanup(temporary.cleanup)
        self.manager = Path(temporary.name) / "Manager"

    def build(self, mode: SessionMode) -> Any:
        """Build a real composition of the given mode and stop its operation lane at cleanup."""
        composition = build_composition(self.manager, mode=mode)
        self.addCleanup(composition.operations.shutdown, 2)
        return composition

    def test_composition_exposes_mission_map_parts_with_one_lock_set(self) -> None:
        """The owner model holds the parts; the plan service holds their lock set; no editor folder exists."""
        owner = self.build(SessionMode.OWNER)
        self.assertIsInstance(owner.mission_map, MissionMapParts)
        self.assertIs(owner.mission_map.plans._locks, owner.mission_map.locks)
        self.assertTrue(owner.paths.data.is_dir())
        self.assertFalse(owner.paths.mission_map.exists())
        # An observer of the same root gets read-only parts and still creates nothing
        observer = self.build(SessionMode.OBSERVER)
        self.assertRaises(PermissionError, observer.mission_map.plans.save, "main", {}, None)
        self.assertFalse(observer.paths.mission_map.exists())


if __name__ == "__main__":
    unittest.main()
