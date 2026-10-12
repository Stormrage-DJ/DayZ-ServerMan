"""Editor recovery builder ownership, pre-handler ordering and unregistered recovery seam."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.mission_map_composition import build_mission_map
from dayz_serverman.repositories.json_store import StagingPolicy
from tests.test_mission_map_journal_paths import snapshot
from tests.test_mission_map_recovery_phases import RecoveryFixture


class RecoveryCompositionTests(RecoveryFixture):
    """Owner construction recovers before returning; observer construction never writes."""

    def build_parts(self, staging):
        """Wire the same owner dependencies that the composition root supplies after lifecycle."""
        operations = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(operations.shutdown, 2)
        settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.dayz)))
        return build_mission_map(self.paths, SimpleNamespace(), settings, staging=staging,
                                 guard=InstallationGuard(self.ports, self.ports), folder_writer=self.ports,
                                 operations=operations)

    def test_owner_builder_recovers_and_shares_the_plan_lock_set(self):
        """The builder has retired a PREPARED journal before any caller can expose its handlers."""
        journal = self.crash_at("AFTER_PREPARED")
        parts = self.build_parts(StagingPolicy.OWNER)
        self.assertEqual(self.repository.records(), ())
        self.assertTrue(self.repository.retired_path_for(journal.operation_id).exists())
        self.assertIs(parts.recovery.locks, parts.locks)
        self.assertIs(parts.plans._locks, parts.locks)
        self.assertTrue(callable(parts.recovery_coordinator.inspect_mission_map_recovery))

    def test_observer_builder_reports_pending_without_recovery(self):
        """Observer construction preserves journal, copies, staged records and target bytes."""
        self.crash_at("AFTER_PREPARED")
        before = snapshot(Path(self.temp.name))
        self.events.clear()
        self.build_parts(StagingPolicy.OBSERVER)
        self.assertEqual(snapshot(Path(self.temp.name)), before)
        self.assertEqual(self.events, [])

    def test_composition_places_recovery_after_lifecycle_before_handlers(self):
        """The root wires settings repair to the editor service and leaves recovery unregistered for 8.9."""
        from dayz_serverman import composition

        source = Path(composition.__file__).read_text(encoding="utf-8")
        self.assertLess(source.index("lifecycle, mutex = build_lifecycle("), source.index("mission_map = build_mission_map("))
        self.assertLess(source.index("mission_map = build_mission_map("), source.index("handlers = build_handler_table("))
        self.assertIn("editor_recovery=mission_map.recovery", source)
        self.assertNotIn("inspect_mission_map_recovery", source)
