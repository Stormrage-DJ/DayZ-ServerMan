"""Startup recovery of an interrupted profile creation runs only inside the installation guard (QF-027, D14)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

import test_profile_provisioning as provisioning_tests  # noqa: E402
from startup_guard_fixtures import GUARDED_RUN, GuardedStartupFixture  # noqa: E402
from dayz_serverman.application import activity_wording as wording  # noqa: E402
from dayz_serverman.application.startup_recoveries import recover_interrupted_provisioning  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402
from dayz_serverman.profile_provisioning_composition import build_profile_provisioning  # noqa: E402
from dayz_serverman.repositories.provisioning_journal import ProvisioningJournal  # noqa: E402

# Block reason of a provisioning recovery that the guard refused, and the reason that existed before
NOT_STOPPED = "Mutations are blocked by an interrupted profile creation while the server is not proven stopped."
REVIEW = "Profile provisioning recovery requires review."
OPERATION = "b" * 32


class ProvisioningStartupRecoveryTests(GuardedStartupFixture, unittest.TestCase):
    """Provisioning recovery removes folders in the DayZ root, so it needs the mutex and a stopped server."""

    def setUp(self) -> None:
        """Build the provisioning fixture with real settings, its journals and the guard doubles."""
        provisioning_tests.ProfileProvisioningTests.setUp(self)
        self.addCleanup(self.temporary.cleanup)
        self.start_guard(self.manager)
        self.roots = (self.dayz, self.journals.root)

    def interrupt(self, root: Path | None = None) -> Path:
        """Leave the folders and journal of a profile creation that ended after its publication."""
        root = root or self.dayz.resolve()
        target = root / "serverman" / "orphan"
        target.mkdir(parents=True)
        (target / "serverDZ.cfg").write_bytes(b"generated")
        (target / ".serverman-provision.json").write_text(
            json.dumps({"operation_id": OPERATION}), encoding="utf-8")
        self.journals.save(ProvisioningJournal(
            OPERATION, "orphan", str(root), r"serverman\.orphan.stage", r"serverman\orphan", "PUBLISHED"))
        return target

    def recover(self) -> None:
        """Run the startup recovery with the guard over the doubles."""
        recover_interrupted_provisioning(self.journals, self.service, self.settings, self.operations, self.guard)

    def test_stopped_server_recovers_as_before(self) -> None:
        """Under the mutex and with a stopped server the owned folder and the journal are removed."""
        target = self.interrupt()
        self.recover()
        self.assertFalse(target.exists())
        self.assertEqual(self.journals.list(), ())
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (None, []))
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_each_state_that_is_not_stopped_writes_nothing_and_blocks(self) -> None:
        """Running, starting, stopping, outside the manager or unknown: no removal, one logged block."""
        target = self.interrupt()
        self.assert_each_refusal_writes_nothing(self.recover, self.roots, NOT_STOPPED)
        # The journal is kept, so a later start with a stopped server finishes the recovery
        self.recover()
        self.assertFalse(target.exists())
        self.assertEqual(self.journals.list(), ())

    def test_busy_installation_writes_nothing_and_blocks(self) -> None:
        """Another manager holds the installation: the state is not read and nothing is removed."""
        target = self.interrupt()
        self.assert_busy_mutex_writes_nothing(self.recover, self.roots, NOT_STOPPED)
        self.recover()
        self.assertFalse(target.exists())

    def test_nothing_to_recover_does_nothing(self) -> None:
        """Without a journal the settings, the mutex and the server state are not touched."""
        before = self.tree(*self.roots)
        self.settings.load = lambda: self.fail("the settings must not be read")
        self.recover()
        self.assertEqual((self.tree(*self.roots), self.mutex.events), (before, []))
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (None, []))

    def test_journal_of_another_installation_recovers_only_under_the_configured_guard(self) -> None:
        """QF-041: as before the plan, also another folder's journal is recovered, but only with a stopped server."""
        other = Path(self.temporary.name) / "Other DayZ"
        other.mkdir()
        target = self.interrupt(other.resolve())
        self.assert_each_refusal_writes_nothing(self.recover, (other, *self.roots), NOT_STOPPED)
        self.mutex.events.clear()
        self.recover()
        self.assertFalse(target.exists())
        self.assertEqual(self.journals.list(), ())
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_no_configured_installation_recovers_as_before_without_a_block(self) -> None:
        """QF-044: without a configured DayZ folder no managed server runs; the journal's folder is cleaned as at HEAD."""
        target = self.interrupt()
        self.settings.load = lambda: SimpleNamespace(dayz_root=None)
        self.lifecycle.status = lambda: self.fail("no server state exists without a DayZ folder")
        self.recover()
        self.assertFalse(target.exists())
        self.assertEqual(self.journals.list(), ())
        self.assertEqual((self.operations.recovery_block, self.logged_blocks(), self.mutex.events), (None, [], []))
        # No block refuses the settings save that configures the DayZ folder
        saved = self.operations.submit("SAVE_SETTINGS", lambda _context: {})
        self.assertEqual(saved.kind, "SAVE_SETTINGS")

    def test_no_configured_installation_keeps_the_review_block(self) -> None:
        """A folder without ownership proof still blocks with the review reason, as at HEAD."""
        target = self.interrupt()
        (target / ".serverman-provision.json").unlink()
        self.settings.load = lambda: SimpleNamespace(dayz_root=None)
        self.recover()
        self.assertTrue(target.exists())
        self.assertEqual((self.operations.recovery_block, self.mutex.events), (REVIEW, []))

    def test_earlier_block_reason_is_kept(self) -> None:
        """A folder without ownership proof still blocks with the review reason, inside the guard."""
        target = self.interrupt()
        (target / ".serverman-provision.json").unlink()
        self.recover()
        self.assertTrue(target.exists())
        self.assertEqual(self.operations.recovery_block, REVIEW)
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_new_reason_has_an_operator_sentence(self) -> None:
        """The logged reason is worded by the catalogue: what happened and what to do."""
        sentence = wording.block_reason_text(NOT_STOPPED)
        self.assertEqual(sentence, (
            "Creating a profile was interrupted and must be finished. This is possible only while the server is "
            "stopped and no other DayZ-ServerMan uses this DayZ installation. "
            "Stop the server, then restart DayZ-ServerMan."))
        self.assertFalse(wording.leaks_identifier(sentence))
        self.assertNotEqual(wording.block_reason_text(REVIEW), sentence)

    def test_composition_recovers_through_the_guard(self) -> None:
        """The provisioning composition passes its journals and the guard to the guarded recovery."""
        target = self.interrupt()
        self.lifecycle.state = ServerState.RUNNING_MANAGED
        build_profile_provisioning(self.profiles, self.settings, self.operations, self.paths.operations, self.guard)
        self.assertTrue(target.exists())
        self.assertEqual(self.operations.recovery_block, NOT_STOPPED)
        self.assertEqual(self.mutex.events, GUARDED_RUN)


if __name__ == "__main__":
    unittest.main()
