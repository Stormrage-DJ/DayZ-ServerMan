"""Cover the retired case of adoption: the chain end of a return to an earlier path (RQ2, 10.34)."""

from __future__ import annotations

import unittest

from dayz_serverman.application.mission_map_locks import AssociationLocks
from dayz_serverman.repositories.mission_map_journal import MapJournal
from dayz_serverman.repositories.mission_map_layout import (
    association_folder, association_profiles, history_name, profile_folder,
)
from dayz_serverman.repositories.mission_map_plans import retired_marker
from dayz_serverman.repositories.mission_map_recovery_cleanup import cleanup_orphans
from dayz_serverman.repositories.mission_map_retired import chain_end, chain_end_retirement, retired_update
from tests.test_mission_map_retired import END, MOMENT, OLD, ROOT, AdoptionFixture


class ChainEndTests(AdoptionFixture):
    """The retired case: return to an earlier path (RQ2)."""

    def build(self) -> None:
        """The active folder is retired toward OLD, OLD toward END, and END is the chain end on another path."""
        super().build()
        self.save_plan(END, 5, "mpmissions\\dayzOffline.moved")
        for key, new in ((self.key, OLD), (OLD, END)):
            journal = MapJournal(f"op-{key[:4]}", "restore_original", "main", ROOT, new, None, None, str(self.dayz),
                                 None, None, None, None, 2, 7, [], [])
            self.commit(journal, [retired_update(self.paths, journal, key, MOMENT)])

    def test_chain_end_retirement(self) -> None:
        """The retired active folder moves to history, the chain end is retired and the state ends."""
        self.assertEqual(self.service().load("main")["read_only_reason"], "returned_to_earlier_path")
        journal = self.new_journal()
        end, updates = chain_end_retirement(self.paths, journal, MOMENT)
        self.assertEqual(end, END)
        self.assertEqual([update.kind for update in updates], ["rename", "retired"])
        self.commit(journal, updates + [self.plan_update(revision=5)])
        history = profile_folder(self.area, "main") / history_name(self.key, MOMENT)
        self.assertEqual(retired_marker(history)["new_mission_key"], OLD)
        self.assertEqual(retired_marker(association_folder(self.area, "main", END))["new_mission_key"], self.key)
        self.assertEqual(chain_end(self.area, "main", OLD), self.key)
        load = self.service().load("main")
        self.assertEqual((load["read_only"], load["read_only_reason"], load["revision"]), (False, None, 5))
        # The history folder is no association: the shared check and the orphan cleanup ignore it
        self.assertEqual(association_profiles(self.area, self.key), ("main",))
        cleanup_orphans(self.paths, AssociationLocks())
        self.assertIsNotNone(retired_marker(history))

    def test_broken_or_cyclic_chains_refuse(self) -> None:
        """A missing folder, a cycle or an active folder that is not retired gives no chain end."""
        journal = MapJournal("op-cycle", "restore_original", "main", ROOT, OLD, None, None, str(self.dayz),
                             None, None, None, None, 2, 7, [], [])
        unretired = MapJournal("op-end", "restore_original", "main", ROOT, END, None, None, str(self.dayz),
                               None, None, None, None, 2, 7, [], [])
        self.assertRaises(ValueError, chain_end_retirement, self.paths, unretired, MOMENT)
        self.assertRaises(ValueError, chain_end, self.area, "main", "3" * 32)
        self.commit(journal, [retired_update(self.paths, journal, END, MOMENT)])
        self.assertRaises(ValueError, chain_end, self.area, "main", self.key)
        stale = MapJournal("op-end", "restore_original", "main", ROOT, END, None, None, str(self.dayz),
                           None, None, None, None, 2, 7, [], [])
        self.assertRaises(ValueError, chain_end_retirement, self.paths, stale, MOMENT)


if __name__ == "__main__":
    unittest.main()
