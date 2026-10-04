"""The shown block's reason and owner belong to one block, and its way out follows the owner (QF-046 to QF-048)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application import activity_wording as wording  # noqa: E402
from dayz_serverman.application.log_activity import format_manager_record  # noqa: E402
from dayz_serverman.bridge.contracts import CONTRACT_VERSION  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402

# A block that only a restart lifts, and an uncatalogued block that a restore owns
LAUNCH = ("The launched server ownership could not be verified.", "START_SERVER")
RESTORE = ("Committed restore targets could not be proven.", "RESTORE_BACKUP")
RESTART_ACTION = "Restart DayZ-ServerMan to check again."
BACKUPS_ACTION = "Open Backups to finish the restore."


class ShownBlockPairingTests(unittest.TestCase):
    """Snapshot, refusal, page sentence and Manager activity all name the latest block and its own way out."""

    def setUp(self) -> None:
        """Build the production composition below a disposable manager root, without a DayZ folder."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_block_pairing_")
        self.addCleanup(self.temporary.cleanup)
        self.composition = build_composition(Path(self.temporary.name) / "manager")
        self.addCleanup(self.composition.operations.shutdown, 2)
        self.calls = 0

    def refuse(self) -> dict:
        """Submit a settings save through the bridge and return the refusal."""
        self.calls += 1
        result = self.composition.host_bridge.dispatch({
            "contract_version": CONTRACT_VERSION, "request_id": f"pairing-{self.calls}", "method": "save_settings",
            "parameters": {"expected_revision": None, "dayz_root": None, "steamcmd_root": None,
                           "custom_backup_root": None}})
        self.assertFalse(result["success"])
        return result["error"]

    def last_refusal_line(self) -> str:
        """Return the Manager activity line of the last refused call."""
        log = self.composition.paths.logs / "manager.jsonl"
        records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return format_manager_record([record for record in records if record["event"] == "bridge.failure"][-1])

    def assert_shown(self, block: tuple[str, str], action: str) -> None:
        """The snapshot pair, the refusal details, its sentence and its log line all belong to block."""
        reason, owner = block
        snapshot = self.composition.coordinator.get_application_snapshot({})
        self.assertEqual((snapshot["mutation_block"], snapshot["mutation_block_owner"]), block)
        self.assertEqual(self.composition.operations.recovery_block_pair(), block)
        error = self.refuse()
        self.assertEqual((error["message"], error["details"]), (reason, {"reason": "RECOVERY_BLOCK", "owner": owner}))
        sentence = wording.error_text(error["code"], error["message"], error["details"]["owner"])
        self.assertTrue(sentence.endswith(f"{reason} {action}"), sentence)
        self.assertTrue(self.last_refusal_line().endswith(f"{reason} {action}"), self.last_refusal_line())

    def test_restore_block_set_last_is_shown_and_then_the_earlier_block(self) -> None:
        """The latest block is shown with its owner; lifting it shows the earlier pair again."""
        operations = self.composition.operations
        operations.block_for_recovery(*LAUNCH)
        operations.block_for_recovery(*RESTORE)
        self.assert_shown(RESTORE, BACKUPS_ACTION)
        operations.clear_recovery_block("RESTORE_BACKUP")
        self.assert_shown(LAUNCH, RESTART_ACTION)

    def test_restart_block_set_last_is_not_worded_as_a_restore(self) -> None:
        """A restore block set first does not lend its owner to a later block."""
        operations = self.composition.operations
        operations.block_for_recovery(*RESTORE)
        operations.block_for_recovery(*LAUNCH)
        self.assert_shown(LAUNCH, RESTART_ACTION)
        operations.clear_recovery_block("START_SERVER")
        self.assert_shown(RESTORE, BACKUPS_ACTION)


class ActionFollowsOwnerTests(unittest.TestCase):
    """The way out is chosen by the owner of the block, never by the words of its reason."""

    def test_restore_words_without_the_restore_owner_get_a_restart(self) -> None:
        """A reason that speaks of a restore but has no owner, or another owner, is lifted by a restart."""
        for owner in (None, "START_SERVER"):
            self.assertEqual(wording.block_reason_text(RESTORE[0], owner), f"{RESTORE[0]} {RESTART_ACTION}")

    def test_other_words_with_the_restore_owner_point_to_backups(self) -> None:
        """A reason that does not speak of a restore but is owned by RESTORE_BACKUP points to Backups."""
        self.assertEqual(wording.block_reason_text(LAUNCH[0], "RESTORE_BACKUP"), f"{LAUNCH[0]} {BACKUPS_ACTION}")
        self.assertEqual(wording.conflict_text(LAUNCH[0], owner="RESTORE_BACKUP"),
                         f"{wording.BLOCKED_TEXT} {LAUNCH[0]} {BACKUPS_ACTION}")


if __name__ == "__main__":
    unittest.main()
