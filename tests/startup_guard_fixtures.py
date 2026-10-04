"""Shared doubles for the guarded startup recoveries (QF-027): a logging lane, the guard and tree snapshots."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from applied_gate_fixtures import CountingLifecycle  # noqa: E402
from test_publication_start_guard import REFUSALS, _Mutex  # noqa: E402
from dayz_serverman.application.installation_guard import InstallationGuard  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402
from dayz_serverman.observability.structured_log import StructuredLogger  # noqa: E402

# Every state that is not a proven stopped server: running, starting, stopping, outside the manager, unknown
NOT_STOPPED_STATES = tuple(REFUSALS)
# A guarded recovery that ran: the mutex was taken, the state read under it, and the mutex released
GUARDED_RUN = ["acquire", "status", "release"]


class GuardedStartupFixture:
    """Mixin for a TestCase: a lane that logs its blocks, a settable server state and a recording mutex."""

    def start_guard(self, base: Path) -> None:
        """Build the lane, the lifecycle and mutex doubles and the guard below a disposable folder."""
        self.log = base / "guard-manager.jsonl"
        self.operations = OperationManager(
            OperationStore(base / "guard-operations"), logger=StructuredLogger(self.log))
        self.addCleanup(self.operations.shutdown, 2)
        self.lifecycle = CountingLifecycle()
        self.mutex = _Mutex()
        status = self.lifecycle.status

        def recorded_status():
            """Record the state read among the mutex events, so its order is visible."""
            self.mutex.events.append("status")
            return status()
        self.lifecycle.status = recorded_status
        self.guard = InstallationGuard(self.lifecycle, self.mutex)

    def logged_blocks(self) -> list[str]:
        """Return the reasons of the logged recovery blocks."""
        if not self.log.exists():
            return []
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [record["fields"]["reason"] for record in records
                if record["event"] == "operation_lane.recovery_block"]

    @staticmethod
    def tree(*roots: Path) -> dict[str, bytes | None]:
        """Return every path below the roots, with the bytes of each file."""
        return {str(path): path.read_bytes() if path.is_file() else None
                for root in roots for path in sorted(root.rglob("*"))}

    def assert_each_refusal_writes_nothing(self, recover, roots: tuple[Path, ...], reason: str) -> None:
        """In every state that is not STOPPED: nothing changes, and one block per start is logged."""
        before = self.tree(*roots)
        for index, state in enumerate(NOT_STOPPED_STATES):
            with self.subTest(state=state.value):
                self.mutex.events.clear()
                self.lifecycle.state = state
                recover()
                self.assertEqual(self.tree(*roots), before)
                self.assertEqual(self.operations.recovery_block, reason)
                self.assertEqual(self.logged_blocks(), [reason] * (index + 1))
                # The state was read under the mutex, and the mutex was released
                self.assertEqual(self.mutex.events, GUARDED_RUN)
                self.assertFalse(self.mutex.held)
        self.lifecycle.state = ServerState.STOPPED

    def assert_busy_mutex_writes_nothing(self, recover, roots: tuple[Path, ...], reason: str) -> None:
        """Another manager holds the installation: the state is not read and nothing changes."""
        before = self.tree(*roots)
        self.mutex.busy = True
        recover()
        self.assertEqual(self.tree(*roots), before)
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (reason, [reason]))
        self.assertEqual(self.mutex.events, [])
        self.mutex.busy = False
