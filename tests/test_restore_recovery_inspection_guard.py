"""The Backups page inspects restore recovery only inside the installation guard and lifts only its own block.

QF-039 (guard and scoped lifting), QF-040 (a failed restore operation's own block), QF-042 (a running operation).
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from startup_guard_fixtures import GUARDED_RUN, NOT_STOPPED_STATES  # noqa: E402
from test_publication_start_guard import REFUSALS  # noqa: E402
from test_restore_startup_guard import (  # noqa: E402
    NOT_STOPPED, RestoreGuardFixture, fail_publication_and_compensation,
)
from dayz_serverman.application import activity_wording as wording  # noqa: E402
from dayz_serverman.application.log_activity import format_manager_record  # noqa: E402
from dayz_serverman.application.operations.models import (  # noqa: E402
    TERMINAL_STATES, OperationState, QueueUnavailable,
)
from dayz_serverman.application.restore_coordinator import RestoreCoordinator  # noqa: E402
from dayz_serverman.application.restores import RESTORE_KIND  # noqa: E402
from dayz_serverman.application.startup_recoveries import recover_interrupted_restores  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402
from dayz_serverman.repositories.restore_storage import RestoreStorage  # noqa: E402

# The block that a restore which needs recovery sets, and blocks that other recoveries set
INSPECT = "Mutations are blocked until restore recovery is inspected."
OTHER_BLOCKS = (
    "Mutations are blocked by an interrupted mod publication while the server is not proven stopped.",
    "Mutations are blocked by unresolved mod publication recovery.",
    "Mutations are blocked by an interrupted profile creation while the server is not proven stopped.",
    "Profile provisioning recovery requires review.",
    "Mutations are blocked by an interrupted direct profile restore while the server is not proven stopped.",
    "Direct profile restore recovery requires attention.",
    "Mutations are blocked by unresolved migration recovery.",
    "Mutations are blocked by an interrupted SteamCMD update with an unknown result.",
)


class RestoreRecoveryInspectionTests(RestoreGuardFixture, unittest.TestCase):
    """Each Backups render inspects restore recovery; it may write only under the guard."""

    def inspect(self) -> dict[str, object]:
        """Call the bridge handler of the Backups page over the recording doubles."""
        return RestoreCoordinator(self.guarded_service(), self.operations).inspect_restore_recovery({})

    def wait_terminal(self, operation_id: str):
        """Wait until the lane finished the operation, and return its record."""
        deadline = time.monotonic() + 5
        while self.operations.get(operation_id).state not in TERMINAL_STATES:
            self.assertLess(time.monotonic(), deadline, "the operation did not finish")
            time.sleep(0.01)
        return self.operations.get(operation_id)

    def test_stopped_server_recovers_and_lifts_its_own_block(self) -> None:
        """Under the mutex and with a stopped server the prior file returns and the restore block is lifted."""
        before = self.interrupt()
        self.operations.block_for_recovery(INSPECT, owner=RESTORE_KIND)
        self.assertEqual(self.inspect(), {"blocked": False, "diagnostics": []})
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.journals.records(), ())
        self.assertIsNone(self.operations.recovery_block)
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_failed_restore_operation_is_lifted_by_the_inspection(self) -> None:
        """QF-040: both blocks of a restore that ended RECOVERY_REQUIRED go; an earlier publication block stays."""
        before = self.target.read_bytes()
        failing = self.service(RestoreStorage(fault_hook=fail_publication_and_compensation))
        preview = failing.preview("main", self.backup_id)
        accepted = RestoreCoordinator(failing, self.operations).apply_restore({
            "profile_id": "main", "backup_id": self.backup_id, "expected_profile_revision": 3,
            "expected_settings_revision": 4, "expected_manifest_digest": self.snapshot_manifest,
            "preview_fingerprint": preview["fingerprint"]})
        self.assertEqual(self.wait_terminal(accepted["operation_id"]).state, OperationState.RECOVERY_REQUIRED)
        # The lane names the operation's own failure as the block shown last
        self.assertNotEqual(self.operations.recovery_block, INSPECT)
        self.assertTrue(self.journals.records())
        # A block of another recovery, set meanwhile, is not the restore's to lift
        self.operations.block_for_recovery(OTHER_BLOCKS[0], owner="PUBLISH_MODS_AND_KEYS")
        self.assertEqual(self.inspect(), {"blocked": False, "diagnostics": []})
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.operations.recovery_block, OTHER_BLOCKS[0])
        self.operations.clear_recovery_block("PUBLISH_MODS_AND_KEYS")
        self.assertIsNone(self.operations.recovery_block)
        # The lane accepts work again
        work = self.operations.submit("SAVE_SETTINGS", lambda _context: {})
        self.assertEqual(self.wait_terminal(work.operation_id).state, OperationState.SUCCEEDED)

    def assert_startup_block_is_lifted_by_the_inspection(self, reason: str, unreadable: bool) -> None:
        """QF-043: startup refused, then the Backups inspection recovers and lifts the startup block only."""
        publication = OTHER_BLOCKS[0]
        before = self.interrupt()
        self.operations.block_for_recovery(publication)
        # At startup the server runs, or the DayZ folder cannot be read
        if unreadable:
            self.settings.value.dayz_root = None
        else:
            self.lifecycle.state = ServerState.RUNNING_MANAGED
        recover_interrupted_restores(self.journals, self.guarded_service(), self.operations)
        self.assertEqual(self.operations.recovery_block, reason)
        # The operator stops the server (or the folder is readable again) and opens Backups
        self.lifecycle.state = ServerState.STOPPED
        self.settings.value.dayz_root = str(self.dayz)
        self.assertEqual(self.inspect(), {"blocked": False, "diagnostics": []})
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.operations.recovery_block, publication)

    def test_startup_not_stopped_block_is_lifted_by_the_backups_inspection(self) -> None:
        """The server ran at startup; the sentence says: stop it, then open Backups again."""
        self.assert_startup_block_is_lifted_by_the_inspection(NOT_STOPPED, unreadable=False)

    def test_startup_unresolved_block_is_lifted_by_the_backups_inspection(self) -> None:
        """The DayZ folder could not be read at startup; the sentence says: open Backups."""
        self.assert_startup_block_is_lifted_by_the_inspection(
            "Backup restore recovery requires a configured DayZ root.", unreadable=True)

    def test_each_state_that_is_not_stopped_writes_nothing_and_stays_blocked(self) -> None:
        """Running, starting, stopping, outside the manager or unknown: no write, blocked with the reason."""
        before_bytes = self.interrupt()
        self.operations.block_for_recovery(INSPECT, owner=RESTORE_KIND)
        before = self.tree(*self.roots)
        for state in NOT_STOPPED_STATES:
            with self.subTest(state=state.value):
                self.mutex.events.clear()
                self.lifecycle.state = state
                result = self.inspect()
                self.assertEqual((result["blocked"], result["reason"]), (True, NOT_STOPPED))
                self.assertEqual(result["diagnostics"][0]["code"], REFUSALS[state])
                self.assertEqual(self.tree(*self.roots), before)
                self.assertEqual(self.operations.recovery_block, INSPECT)
                self.assertEqual(self.mutex.events, GUARDED_RUN)
        # The journal is kept, so a later inspection with a stopped server finishes the recovery
        self.lifecycle.state = ServerState.STOPPED
        self.assertFalse(self.inspect()["blocked"])
        self.assertEqual(self.target.read_bytes(), before_bytes)
        self.assertIsNone(self.operations.recovery_block)

    def test_busy_installation_writes_nothing_and_stays_blocked(self) -> None:
        """Another manager holds the installation: the state is not read and nothing is written."""
        self.interrupt()
        self.operations.block_for_recovery(INSPECT, owner=RESTORE_KIND)
        before = self.tree(*self.roots)
        self.mutex.busy = True
        result = self.inspect()
        self.assertEqual((result["blocked"], result["reason"]), (True, NOT_STOPPED))
        self.assertEqual(result["diagnostics"][0]["code"], "CONTROL_CONFLICT")
        self.assertEqual((self.tree(*self.roots), self.mutex.events), (before, []))
        self.assertEqual(self.operations.recovery_block, INSPECT)

    def test_running_operation_defers_the_check(self) -> None:
        """QF-042: while the lane runs an operation, a journal is not called interrupted and nothing is touched."""
        self.interrupt()
        before = self.tree(*self.roots)
        started, release = threading.Event(), threading.Event()

        def hold(_context) -> dict[str, object]:
            """Keep the lane busy until the test releases it."""
            started.set()
            release.wait(5)
            return {}
        running = self.operations.submit("RESTORE_BACKUP", hold)
        self.assertTrue(started.wait(5))
        try:
            self.assertEqual(self.inspect(), {"blocked": True, "deferred": True, "diagnostics": []})
            self.assertEqual((self.tree(*self.roots), self.mutex.events), (before, []))
        finally:
            release.set()
        self.wait_terminal(running.operation_id)
        # When the lane is idle again, the next inspection recovers
        self.assertFalse(self.inspect()["blocked"])
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_running_operation_without_a_journal_is_not_deferred(self) -> None:
        """Without a journal a busy lane changes nothing: the fast path answers not blocked."""
        started, release = threading.Event(), threading.Event()
        running = self.operations.submit("CREATE_BACKUP", lambda _context: (started.set(), release.wait(5), {})[2])
        self.assertTrue(started.wait(5))
        try:
            self.assertEqual(self.inspect(), {"blocked": False, "diagnostics": []})
        finally:
            release.set()
        self.wait_terminal(running.operation_id)

    def test_no_journal_reads_nothing_and_keeps_other_blocks(self) -> None:
        """Without a journal nothing is locked or read; a block of another recovery is never lifted."""
        self.settings.load = lambda: self.fail("the settings must not be read")
        for other in OTHER_BLOCKS:
            for owner in (None, "PUBLISH_MODS_AND_KEYS", "PROVISION_PROFILE", "RESTORE_PROFILE_FROM_BACKUP"):
                with self.subTest(block=other, owner=owner):
                    self.operations.block_for_recovery(other, owner=owner)
                    self.assertEqual(self.inspect(), {"blocked": False, "diagnostics": []})
                    self.assertEqual(self.operations.recovery_block, other)
                    self.operations.clear_recovery_block()
        self.assertEqual(self.mutex.events, [])

    def test_lifting_the_restore_block_uncovers_an_earlier_block(self) -> None:
        """A publication and a provisioning block set before the restore block stay after it is lifted."""
        publication, provisioning = OTHER_BLOCKS[0], OTHER_BLOCKS[2]
        self.operations.block_for_recovery(provisioning)
        self.operations.block_for_recovery(publication, owner="PUBLISH_MODS_AND_KEYS")
        self.operations.block_for_recovery(INSPECT, owner=RESTORE_KIND)
        self.assertEqual(self.operations.recovery_block, INSPECT)
        self.inspect()
        self.assertEqual(self.operations.recovery_block, publication)
        self.operations.clear_recovery_block("PUBLISH_MODS_AND_KEYS")
        self.assertEqual(self.operations.recovery_block, provisioning)

    def test_a_restore_text_without_the_restore_owner_is_not_lifted(self) -> None:
        """Ownership is the operation kind, not the wording of the reason."""
        self.operations.block_for_recovery(INSPECT)
        self.inspect()
        self.assertEqual(self.operations.recovery_block, INSPECT)

    def test_an_uncatalogued_block_names_its_way_out(self) -> None:
        """QF-045: a restore-owned reason points to Backups, any other one to a restart; log and refusal agree."""
        for reason, owner, action, details in (
            ("Committed restore targets could not be proven.", RESTORE_KIND, "Open Backups to finish the restore.",
             {"reason": "RECOVERY_BLOCK", "owner": RESTORE_KIND}),
            ("Publication journal verification failed.", None, "Restart DayZ-ServerMan to check again.",
             {"reason": "RECOVERY_BLOCK"})):
            with self.subTest(owner=owner):
                self.operations.block_for_recovery(reason, owner=owner)
                self.assertEqual(self.operations.recovery_block_pair(), (reason, owner))
                with self.assertRaises(QueueUnavailable) as refused:
                    self.operations.submit("SAVE_SETTINGS", lambda _context: {})
                self.assertEqual(refused.exception.details, details)
                record = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()][-1]
                self.assertEqual(format_manager_record(record).split("Changes are now blocked. ")[1],
                                 f"{reason} {action}")
                self.operations.clear_recovery_block()

    def test_the_page_words_the_refusal_by_the_catalogue(self) -> None:
        """The reason that the page receives has an operator sentence that says what to do."""
        sentence = wording.block_reason_text(NOT_STOPPED)
        self.assertIn("Stop the server, then open Backups again or restart DayZ-ServerMan.", sentence)
        self.assertFalse(wording.leaks_identifier(sentence))


if __name__ == "__main__":
    unittest.main()
