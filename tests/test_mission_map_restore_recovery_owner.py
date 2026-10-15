"""The recovery owner of a failed restore on the real operation lane (8.13 Architect finding F1, leaf 8.8).

A restore that needs recovery blocks only under the map editor kind, so the 8.5 recover handler lifts
every block that it set and the lane admits work again. A failure without recovery sets no block.
"""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND, MissionMapRecovery
from dayz_serverman.application.mission_map_recovery_coordinator import MissionMapRecoveryCoordinator
from dayz_serverman.application.mission_map_restore_coordinator import MissionMapRestoreCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import OperationState, QueueUnavailable
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.domain.mission_map_restore_texts import LANE_KINDS
from tests.mission_map_restore_support import PLAN, RestoreFixture

TERMINAL = (OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED, OperationState.RECOVERY_REQUIRED)


class RestoreOwnerTests(RestoreFixture):
    """The restore coordinator over the real lane and the 8.5 recovery coordinator."""

    def setUp(self) -> None:
        """Apply the plan once and build the lane over the fixture's manager root."""
        super().setUp()
        self.save(PLAN, 25)
        self.run_apply()
        self.lane = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(self.lane.shutdown, 5)
        self.coordinator = MissionMapRestoreCoordinator(self.restorer, self.lane)

    def finished(self, operation_id: str) -> Any:
        """Wait until the operation ends and the lane is drained; return its record."""
        for _ in range(1500):
            if self.lane.get(operation_id).state in TERMINAL:
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

    def submit(self, mode: str, fingerprint: str | None = None) -> str:
        """Submit one restore with the reviewed fingerprint unless a test gives another."""
        fingerprint = fingerprint or self.restore_review(mode).fingerprint
        return self.coordinator.restore_mission_map(
            {"profile_id": "main", "mode": mode, "preview_fingerprint": fingerprint})["operation_id"]

    def recover(self) -> dict[str, Any]:
        """Run the 8.5 on-demand recover handler over the fixture's guard ports and locks."""
        settings = SimpleNamespace(load=lambda: self.settings)
        service = MissionMapRecovery(self.paths, settings, InstallationGuard(self.ports, self.ports),  # type: ignore[arg-type]
                                     self.ports, self.plans._locks)
        return MissionMapRecoveryCoordinator(service, self.lane).inspect_mission_map_recovery({})

    def test_failed_restore_blocks_under_the_editor_kind_and_recovery_lifts_it(self) -> None:
        """Record publication fails after COMMITTING; recovery finishes it, and the lane admits a new restore."""
        for mode in ("previous", "original"):
            with self.subTest(mode=mode):
                broken = [True]

                def fail_publish(phase: str, _index: int) -> None:
                    """Fail every record publication while the operation runs."""
                    if phase == "AFTER_PUBLISH_RECORD" and broken[0]:
                        raise OSError("record publication fault")
                self.fault = fail_publish
                record = self.finished(self.submit(mode))
                self.assertEqual((record.state, record.kind), (OperationState.RECOVERY_REQUIRED, LANE_KINDS[mode]))
                self.assertTrue(self.blocks())
                self.assertEqual({owner for _reason, owner in self.blocks()}, {EDITOR_KIND})
                with self.assertRaises(QueueUnavailable):
                    self.lane.submit(LANE_KINDS[mode], lambda _context: {})
                broken[0] = False
                self.assertFalse(self.recover()["blocked"])
                self.assertEqual(self.blocks(), [])
                self.assertEqual(self.records()[2][0]["operation_kind"], f"restore_{mode}")
        # The lane admits a new submission: Restore previous state commits
        self.fault = None
        self.assertEqual(self.finished(self.submit("previous")).state, OperationState.SUCCEEDED)

    def test_failure_without_recovery_sets_no_block(self) -> None:
        """A stale review fails without recovery; no block, and the next restore runs."""
        record = self.finished(self.submit("previous", "0" * 64))
        self.assertEqual((record.state, record.terminal_error.code), (OperationState.FAILED, "REVISION_CONFLICT"))
        self.assertEqual(self.blocks(), [])
        self.assertEqual(self.finished(self.submit("previous")).state, OperationState.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
