"""Composition of the 8.9 owner methods: one association lock set, one commit step, the owner allowlist only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.bridge_composition import (
    OBSERVER_READ_METHODS, OWNER_ONLY_METHODS, READER_EXEMPT_METHODS, SLICED_READER_METHODS,
)
from dayz_serverman.composition import build_composition
from dayz_serverman.application.mission_map_publication_coordinator import MissionMapPublicationCoordinator
from dayz_serverman.mission_map_publication_composition import build_mission_map_publication
from dayz_serverman.repositories.mission_map_journal_store import journal_folder
from dayz_serverman.session_observer import open_observer_session

# The owner methods of 8.9 in handler-table order (A11: previews follow the owner-only precedent)
PUBLICATION_METHODS = ("preview_mission_map", "apply_mission_map", "preview_mission_map_restore",
                       "restore_mission_map", "inspect_mission_map_recovery")


class PublicationCompositionTests(unittest.TestCase):
    """The owner composition wires the methods over the shared parts; an observer never holds them."""

    def setUp(self) -> None:
        """Build an owner composition on a disposable manager root."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_publication_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Manager"
        self.composition = build_composition(self.root)
        self.addCleanup(self.composition.operations.shutdown, 2)
        handlers = self.composition.bridge._handlers
        self.publication = handlers["preview_mission_map"].__self__

    def test_one_association_lock_set_and_one_commit_step(self) -> None:
        """Plan saves, Apply, both restores, recovery and the medical toggle share the one lock set (T2.3-F9)."""
        parts, publication = self.composition.mission_map, self.publication
        self.assertIsInstance(publication, MissionMapPublicationCoordinator)
        commit = publication.apply._service._commit
        # Apply and both restores publish through one commit step with the editor's journal folder
        self.assertIs(publication.restore_lane._service._commit, commit)
        self.assertEqual(commit.journals.root, journal_folder(self.composition.paths).resolve(strict=False))
        locks = parts.locks
        for owner in (parts.plans._locks, commit.locks, parts.recovery.locks,
                      self.composition.medical_features._editor._commit.locks):
            self.assertIs(owner, locks)
        # The previews are the reviews of the lane operations, over the composition's plan service and guard
        self.assertIs(publication.apply._service._preview, publication.preview)
        self.assertIs(publication.restore_lane._service, publication.restore)
        self.assertIs(publication.preview._plans, parts.plans)
        self.assertIs(publication.restore._reader._plans, parts.plans)
        self.assertEqual(publication.preview._medical_baseline, self.composition.medical_features.zone_baseline_path)
        self.assertEqual(publication.restore._reader._medical_baseline,
                         self.composition.medical_features.zone_baseline_path)
        self.assertIs(commit.guard, parts.recovery.guard)
        # Both lane coordinators and the recovery coordinator use the composition's one lane
        self.assertIs(publication.recovery, parts.recovery_coordinator)
        for lane in (publication.apply._operations, publication.restore_lane._operations,
                     publication.recovery.operations):
            self.assertIs(lane, self.composition.operations)

    def test_handlers_are_registered_in_order_as_owner_methods(self) -> None:
        """The table holds the five methods after the display reads, each bound to the publication coordinator."""
        names = list(self.composition.bridge._handlers)
        start = names.index("read_mission_map_background") + 1
        self.assertEqual(tuple(names[start:start + 5]), PUBLICATION_METHODS)
        self.assertEqual({name: self.composition.bridge._handlers[name] for name in PUBLICATION_METHODS},
                         dict(self.publication.handlers()))
        self.assertLessEqual(set(PUBLICATION_METHODS), OWNER_ONLY_METHODS)
        for reads in (OBSERVER_READ_METHODS, READER_EXEMPT_METHODS, SLICED_READER_METHODS):
            self.assertEqual(set(PUBLICATION_METHODS) & reads, set())

    def test_an_observer_holds_none_of_the_methods_and_creates_nothing(self) -> None:
        """The observer facade refuses each method as unknown; the observer creates no journal folder."""
        observer_root = self.root.parent / "Observed"
        session = open_observer_session(observer_root)
        self.addCleanup(session.close)
        self.assertEqual(set(PUBLICATION_METHODS) & session.composition.bridge.allowed_methods, set())
        for method in PUBLICATION_METHODS:
            with self.subTest(method=method):
                answer = session.composition.bridge.dispatch(
                    {"contract_version": 1, "request_id": "x", "method": method, "parameters": {}})
                self.assertEqual(answer["error"]["code"], "INVALID_REQUEST")
        self.assertFalse(observer_root.exists())

    def test_the_builder_needs_the_recovery_coordinator(self) -> None:
        """Parts built without the guard or the lane have no recovery owner, so no publication is built."""
        parts = SimpleNamespace(locks=self.composition.mission_map.locks, plans=self.composition.mission_map.plans,
                                recovery_coordinator=None)
        with self.assertRaises(ValueError):
            build_mission_map_publication(self.composition.paths, self.composition.profiles, parts,  # type: ignore[arg-type]
                                          None, None, self.composition.operations,  # type: ignore[arg-type]
                                          medical_baseline=lambda _profile, _mission: None, create_root=False)


if __name__ == "__main__":
    unittest.main()
