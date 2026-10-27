"""Recovery log lines of the editor recovery (8.12 Architect 2026-10-17 16:16:19, decision 1).

The recovery service reports each journal it read in the internal `journals` key. The coordinator removes that
key, writes one WARNING line per journal and leaves the bridge answer unchanged. The lane writes the lift line.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from dayz_serverman.application.mission_map_recovery import (
    EDITOR_KIND, RECOVERY_NOT_STOPPED, RECOVERY_UNREADABLE, RECOVERY_UNRESOLVED,
)
from dayz_serverman.application.mission_map_recovery_coordinator import MissionMapRecoveryCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.domain.lifecycle import ServerState
from dayz_serverman.observability.structured_log import StructuredLogger
from tests.test_mission_map_records import VANILLA
from tests.test_mission_map_recovery_phases import RecoveryFixture

RECOVERY_EVENTS = ("mission_map.recovery_resolved", "mission_map.recovery_blocked")
CLEARED_EVENT = "operation_lane.recovery_block_cleared"


class RecoveryJournalItemTests(RecoveryFixture):
    """The service names each journal with its phase before recovery and its durable result after it."""

    def item(self, journal, phase, result):
        """Return the expected internal item of one readable journal."""
        return {"operation_id": journal.operation_id, "operation_kind": "apply", "phase": phase, "result": result}

    def unreadable(self):
        """Write one active journal file that cannot be parsed."""
        self.repository.root.mkdir(parents=True, exist_ok=True)
        (self.repository.root / "op-unreadable.json").write_text("{", encoding="utf-8")

    def test_publishing_journal_is_rolled_back(self):
        """A PUBLISHING journal gives one item with the result ROLLED_BACK."""
        journal = self.crash_at("AFTER_PUBLISH")
        result = self.recovery().inspect()
        self.assertFalse(result["blocked"])
        self.assertEqual(result["journals"], [self.item(journal, "PUBLISHING", "ROLLED_BACK")])

    def test_committing_journal_is_committed(self):
        """A COMMITTING journal gives one item with the result COMMITTED."""
        journal = self.crash_at("AFTER_COMMITTING")
        self.assertEqual(self.recovery().inspect()["journals"], [self.item(journal, "COMMITTING", "COMMITTED")])

    def test_refusal_before_the_engine_keeps_the_phase_and_has_no_result(self):
        """A server that is not proven stopped gives the item with result None and the phase found."""
        journal = self.crash_at("AFTER_COMMITTING")
        self.ports.state = ServerState.RUNNING_MANAGED
        result = self.recovery().inspect()
        self.assertEqual(result["reason"], RECOVERY_NOT_STOPPED)
        self.assertEqual(result["journals"], [self.item(journal, "COMMITTING", None)])

    def test_engine_block_leaves_an_active_journal_without_result(self):
        """An old target after the commit point keeps the journal active, so its item has no result."""
        journal = self.crash_at("AFTER_COMMITTING")
        Path(journal.groups[0].target_path).write_bytes(VANILLA)
        result = self.recovery().inspect()
        self.assertTrue(result["blocked"])
        self.assertEqual(result["journals"], [self.item(journal, "COMMITTING", None)])

    def test_unreadable_journal_gives_the_count_and_no_id(self):
        """An unreadable journal is counted and has no id."""
        self.unreadable()
        result = self.recovery().inspect()
        self.assertEqual(result["reason"], RECOVERY_UNREADABLE)
        self.assertEqual(result["journals"], [{"unreadable": 1}])

    def test_readable_journal_beside_an_unreadable_one_has_no_result(self):
        """A readable journal beside an unreadable one keeps its id; the refusal leaves it active."""
        journal = self.crash_at("AFTER_PUBLISH")
        self.unreadable()
        result = self.recovery().inspect()
        self.assertEqual(result["reason"], RECOVERY_UNREADABLE)
        self.assertEqual(result["journals"], [self.item(journal, "PUBLISHING", None), {"unreadable": 1}])

    def test_clean_start_has_no_item(self):
        """No journal gives an empty list."""
        self.assertEqual(self.recovery().inspect()["journals"], [])


class RecoveryLogLineTests(RecoveryFixture):
    """The coordinator writes exactly the decision 1 lines and returns the envelope without `journals`."""

    def setUp(self):
        """Log to a disposable manager log outside the snapshot tree, and share it with a real lane."""
        super().setUp()
        logs = tempfile.TemporaryDirectory(prefix="serverman_recovery_log_")
        self.addCleanup(logs.cleanup)
        self.log = Path(logs.name) / "manager.jsonl"
        logger = StructuredLogger(self.log)
        self.lane = OperationManager(OperationStore(self.paths.operations), logger=logger)
        self.addCleanup(self.lane.shutdown, 2)
        self.coordinator = MissionMapRecoveryCoordinator(self.recovery(), self.lane, logger)

    def lines(self, events=RECOVERY_EVENTS + (CLEARED_EVENT,)):
        """Return the written records of the named events, in order."""
        if not self.log.exists():
            return []
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [record for record in records if record["event"] in events]

    def shape(self, record):
        """Reduce one record to its event, level, operation id and fields."""
        return record["event"], record["level"], record["operation_id"], record["fields"]

    def resolved(self, journal, phase, result, trigger):
        """Return the expected shape of one resolved line."""
        return ("mission_map.recovery_resolved", "WARNING", journal.operation_id,
                {"operation_kind": "apply", "phase": phase, "result": result, "trigger": trigger})

    def assert_startup_line(self, crash, phase, result):
        """Startup writes one resolved line with the id, the phase, the result and the trigger STARTUP."""
        journal = self.crash_at(crash)
        self.coordinator.startup()
        self.assertEqual([self.shape(line) for line in self.lines()], [self.resolved(journal, phase, result, "STARTUP")])
        self.assertIsNone(self.lane.recovery_block)
        self.assert_private(journal)

    def test_startup_rollback_line(self):
        """A PUBLISHING journal is logged as ROLLED_BACK."""
        self.assert_startup_line("AFTER_PUBLISH", "PUBLISHING", "ROLLED_BACK")

    def test_startup_roll_forward_line(self):
        """A COMMITTING journal is logged as COMMITTED."""
        self.assert_startup_line("AFTER_COMMITTING", "COMMITTING", "COMMITTED")

    def test_not_stopped_block_then_check_again(self):
        """A blocked startup names the reason; Check again then resolves the journal and lifts the block."""
        journal = self.crash_at("AFTER_COMMITTING")
        self.ports.state = ServerState.RUNNING_MANAGED
        self.coordinator.startup()
        self.assertEqual([self.shape(line) for line in self.lines()], [
            ("mission_map.recovery_blocked", "WARNING", journal.operation_id,
             {"operation_kind": "apply", "phase": "COMMITTING", "reason": RECOVERY_NOT_STOPPED, "trigger": "STARTUP"})])
        self.ports.state = ServerState.STOPPED
        result = self.coordinator.inspect_mission_map_recovery({})
        self.assertNotIn("journals", result)
        self.assertEqual(result, {"blocked": False, "diagnostics": []})
        self.assertEqual([self.shape(line) for line in self.lines()][1:], [
            self.resolved(journal, "COMMITTING", "COMMITTED", "ON_DEMAND"),
            (CLEARED_EVENT, "INFO", None, {"owner": EDITOR_KIND, "reasons": [RECOVERY_NOT_STOPPED]})])
        self.assertIsNone(self.lane.recovery_block)
        self.assert_private(journal)

    def test_engine_block_names_the_unresolved_reason(self):
        """A journal that the engine keeps is logged with the reason the coordinator blocks with."""
        journal = self.crash_at("AFTER_COMMITTING")
        Path(journal.groups[0].target_path).write_bytes(VANILLA)
        self.assertNotIn("journals", self.coordinator.inspect_mission_map_recovery({}))
        self.assertEqual([self.shape(line) for line in self.lines()], [
            ("mission_map.recovery_blocked", "WARNING", journal.operation_id,
             {"operation_kind": "apply", "phase": "COMMITTING", "reason": RECOVERY_UNRESOLVED, "trigger": "ON_DEMAND"})])
        self.assertEqual(self.lane.recovery_block_pair(), (RECOVERY_UNRESOLVED, EDITOR_KIND))

    def test_unreadable_journals_give_one_count_line(self):
        """Unreadable journals write one blocked line without an operation id."""
        self.repository.root.mkdir(parents=True, exist_ok=True)
        (self.repository.root / "op-unreadable.json").write_text("{", encoding="utf-8")
        self.coordinator.startup()
        self.assertEqual([self.shape(line) for line in self.lines()], [
            ("mission_map.recovery_blocked", "WARNING", None,
             {"reason": RECOVERY_UNREADABLE, "unreadable": 1, "trigger": "STARTUP"})])

    def test_clean_start_writes_no_recovery_line(self):
        """No journal and no block: no recovery line and no lift line."""
        self.coordinator.startup()
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.coordinator.inspect_mission_map_recovery({}), {"blocked": False, "diagnostics": []})
        self.assertEqual(self.lines(), [])

    def test_no_logger_keeps_the_recovery(self):
        """Without a logger the coordinator recovers and blocks as before and writes no line."""
        journal = self.crash_at("AFTER_PUBLISH")
        MissionMapRecoveryCoordinator(self.recovery(), self.lane).startup()
        self.assertEqual(self.lines(RECOVERY_EVENTS), [])
        self.assertTrue(self.repository.retired_path_for(journal.operation_id).exists())

    def assert_private(self, journal):
        """No line holds the mission root, DayZ root, profile id, mission key or a path separator."""
        secrets = (journal.mission_root, journal.dayz_root, journal.mission_key, str(self.dayz))
        for record in self.lines():
            for value in _values(record["fields"]):
                text = str(value)
                self.assertNotEqual(text, journal.profile_id, record)
                self.assertFalse(any(secret in text for secret in secrets), record)
                self.assertFalse("\\" in text or "/" in text, record)


def _values(value):
    """Yield every leaf value of one field mapping."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)
    else:
        yield value
