"""Loads during an adoption-shaped commit show the old answer until the commit point (Architect 00:21:00, 10.34)."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from dayz_serverman.repositories.mission_map_commit_records import CommitRecords, RecordUpdate
from dayz_serverman.repositories.mission_map_journal import MapJournal
from dayz_serverman.repositories.mission_map_publication import MissionMapPublication
from dayz_serverman.repositories.mission_map_retired import chain_end_retirement
from tests.test_mission_map_retired import MOMENT, AdoptionFixture
from tests import test_mission_map_retired_chain as chain


# The A13 writer side as a test holds it
HELD = SimpleNamespace(held=True)
# Fields of the load answer that must not change before COMMITTING
FIELDS = ("read_only", "read_only_reason", "state", "revision", "plan", "path_change")


class ReadFixture:
    """Load the plan of the fixture profile at every fault point before COMMITTING, and once after the commit."""

    def answer(self: Any) -> dict[str, Any]:
        """Return the fields of the load answer that the comparison uses."""
        load = self.service().load("main")
        return {name: load[name] for name in FIELDS}

    def loads_during(self: Any, updates: Callable[[MapJournal], list[RecordUpdate]]) -> list[tuple[str, dict]]:
        """Run the D4 order of a records-only journal and load at each fault point up to COMMITTING."""
        seen: list[tuple[str, dict[str, Any]]] = []
        committing = False

        def fault(phase: str, index: int) -> None:
            if not committing:
                seen.append((f"{phase}/{index}", self.answer()))
        journal = self.new_journal()
        records = CommitRecords(self.paths, self.repository, fault)
        publication = MissionMapPublication(self.paths, self.repository, HELD, fault_hook=fault)
        records.stage(journal, updates(journal))
        self.repository.save(journal)
        fault("AFTER_PREPARED", -1)
        publication.publish_groups(journal)
        publication.advance(journal)
        fault("AFTER_PUBLISHED", -1)
        committing = True
        publication.advance(journal)
        self.assertTrue(records.roll_forward(journal))
        return seen

    def check(self: Any, updates: Callable[[MapJournal], list[RecordUpdate]]) -> dict[str, Any]:
        """Assert that every load before COMMITTING equals the load before the commit; return the load after it."""
        before = self.answer()
        self.assertTrue(before["read_only"])
        seen = self.loads_during(updates)
        self.assertGreaterEqual(len(seen), 4)
        for point, answer in seen:
            with self.subTest(point=point):
                self.assertEqual(answer, before)
        return self.answer()


class PathChangeReadTests(ReadFixture, AdoptionFixture):
    """D-2: an existing active folder goes to its history name in a path-change copy."""

    def test_loads_before_committing_show_the_old_plan(self) -> None:
        """Until COMMITTING the old plan stays read only; after the commit the moved plan is editable."""
        after = self.check(self.updates)
        self.assertEqual((after["read_only"], after["revision"], after["path_change"]), (False, 3, None))


class RetiredReadTests(ReadFixture, chain.ChainEndTests):
    """The retired case: the chain end stays the read-only plan until COMMITTING."""

    def test_loads_before_committing_show_the_chain_end(self) -> None:
        """Until COMMITTING the reason returned_to_earlier_path stays; after the commit the plan is editable."""
        def updates(journal: MapJournal) -> list[RecordUpdate]:
            return chain_end_retirement(self.paths, journal, MOMENT)[1] + [self.plan_update(revision=5)]
        after = self.check(updates)
        self.assertEqual((after["read_only"], after["read_only_reason"], after["revision"]), (False, None, 5))

    # The inherited tests run in tests.test_mission_map_retired_chain
    test_chain_end_retirement = None  # type: ignore[assignment]
    test_broken_or_cyclic_chains_refuse = None  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
