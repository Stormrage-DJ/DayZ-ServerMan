"""Startup recovery of an interrupted mod publication runs only inside the write guard (QF-026)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from applied_gate_fixtures import CountingLifecycle  # noqa: E402
from test_mod_publication_inventory import PublicationFixture  # noqa: E402
import test_mod_publication_recovery as recovery_tests  # noqa: E402
from test_publication_start_guard import REFUSALS, _Mutex  # noqa: E402
from dayz_serverman import composition, workshop_composition  # noqa: E402
from dayz_serverman.application import activity_wording as wording  # noqa: E402
from dayz_serverman.application.installation_guard import InstallationGuard  # noqa: E402
from dayz_serverman.application.log_activity import format_manager_record  # noqa: E402
from dayz_serverman.application.mod_publication_startup import (  # noqa: E402
    recover_interrupted_publications,
)
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402
from dayz_serverman.observability.structured_log import StructuredLogger  # noqa: E402
from dayz_serverman.repositories.mod_publication_journal import (  # noqa: E402
    PublicationJournalRepository,
)
from dayz_serverman.repositories.mod_publication_recovery import PublicationRecovery  # noqa: E402

# Block reason of a recovery that the guard refused, and the two reasons that existed before
NOT_STOPPED = "Mutations are blocked by an interrupted mod publication while the server is not proven stopped."
NO_ROOT = "Mutations are blocked by unresolved mod publication."
UNPROVEN = "Mutations are blocked by unresolved mod publication recovery."


class PublicationStartupRecoveryTests(PublicationFixture):
    """Recovery writes into the DayZ root, so it needs the mutex and a proven stopped server."""

    def setUp(self) -> None:
        """Seed live mod folders, a journal repository, a logging lane, and the guard doubles."""
        super().setUp()
        self.dayz = self.base / "DayZ"
        for relative, content in (("mods/alpha", b"old-a"), ("mods/beta", b"old-b")):
            (self.dayz / relative).mkdir(parents=True)
            (self.dayz / relative / "old.pbo").write_bytes(content)
        (self.dayz / "keys").mkdir()
        (self.dayz / "keys" / "keep.bikey").write_bytes(b"keep")
        self.repository = PublicationJournalRepository(self.base / "journals")
        self.log = self.base / "manager.jsonl"
        self.operations = OperationManager(
            OperationStore(self.base / "operations"), logger=StructuredLogger(self.log))
        self.addCleanup(self.operations.shutdown, 2)
        self.lifecycle = CountingLifecycle()
        self.mutex = _Mutex()
        self.settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.dayz)))

    # The interrupted publication of the recovery tests: alpha is swapped, beta is not
    _save_new = staticmethod(recovery_tests.PublicationRecoveryTests._save_new)
    interrupt = recovery_tests.PublicationRecoveryTests._interrupt_after_first

    def recover(self) -> None:
        """Run the startup recovery with the guard over the doubles."""
        recover_interrupted_publications(
            self.repository, self.settings, self.operations,
            InstallationGuard(self.lifecycle, self.mutex))

    def tree(self) -> dict[str, bytes | None]:
        """Return every path below the DayZ root and the journal folder, with file bytes."""
        return {str(path): path.read_bytes() if path.is_file() else None
                for root in (self.dayz, self.base / "journals") for path in sorted(root.rglob("*"))}

    def logged_blocks(self) -> list[str]:
        """Return the reasons of the logged recovery blocks."""
        if not self.log.exists():
            return []
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [record["fields"]["reason"] for record in records
                if record["event"] == "operation_lane.recovery_block"]

    def assert_refused_without_a_write(self, before: dict[str, bytes | None], times: int = 1) -> None:
        """Assert that nothing changed, and that the block with the new reason is set and logged."""
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.operations.recovery_block, NOT_STOPPED)
        self.assertEqual(self.logged_blocks(), [NOT_STOPPED] * times)
        self.assertFalse(self.mutex.held)

    def test_stopped_server_recovers_as_before(self) -> None:
        """Under the mutex and with a stopped server the interrupted set is rolled back."""
        self.interrupt()
        self.recover()
        self.assertEqual((self.dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
        self.assertEqual((self.dayz / "mods/beta/old.pbo").read_bytes(), b"old-b")
        self.assertFalse(any(path.name.startswith(".serverman-") for path in self.dayz.rglob("*")))
        self.assertEqual(list(self.repository.records()), [])
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (None, []))
        self.assertEqual(self.mutex.events, ["acquire", "release"])

    def test_each_state_that_is_not_stopped_writes_nothing_and_blocks(self) -> None:
        """A running, starting, stopping or unknown server: no rename, no removal, one logged block."""
        self.interrupt()
        before = self.tree()
        # The swapped output is live, and its prior copy is beside it
        self.assertFalse((self.dayz / "mods/alpha/old.pbo").exists())
        for index, state in enumerate(REFUSALS):
            with self.subTest(state=state.value):
                self.lifecycle.state = state
                with patch.object(PublicationRecovery, "inspect", side_effect=AssertionError("no recovery")):
                    self.recover()
                self.assert_refused_without_a_write(before, index + 1)
        # A later start with a stopped server finishes the recovery
        self.lifecycle.state = ServerState.STOPPED
        self.recover()
        self.assertEqual((self.dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")

    def test_busy_installation_writes_nothing_and_blocks(self) -> None:
        """Another manager holds the installation: the state is not read and nothing is written."""
        self.interrupt()
        before = self.tree()
        self.mutex.busy = True
        self.lifecycle.status = lambda: self.fail("the state must not be read")
        self.recover()
        self.assert_refused_without_a_write(before)
        self.assertEqual(self.mutex.events, [])

    def test_nothing_to_recover_does_nothing(self) -> None:
        """Without a journal the settings, the mutex and the server state are not touched."""
        before = self.tree()
        self.settings.load = lambda: self.fail("the settings must not be read")
        self.lifecycle.status = lambda: self.fail("the state must not be read")
        self.recover()
        self.assertEqual((self.tree(), self.mutex.events), (before, []))
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (None, []))

    def test_earlier_block_reasons_are_kept(self) -> None:
        """No DayZ folder, and a state that recovery cannot prove, block with their own reasons."""
        self.interrupt()
        self.settings.load = lambda: SimpleNamespace(dayz_root=None)
        self.recover()
        self.assertEqual((self.operations.recovery_block, self.mutex.events), (NO_ROOT, []))
        self.settings.load = lambda: SimpleNamespace(dayz_root=str(self.dayz))
        with patch.object(PublicationRecovery, "inspect", return_value={"blocked": True, "diagnostics": []}):
            self.recover()
        self.assertEqual(self.operations.recovery_block, UNPROVEN)
        self.assertEqual(self.mutex.events, ["acquire", "release"])

    def test_new_reason_has_an_operator_sentence_in_manager_activity(self) -> None:
        """The logged reason is worded by the catalogue: what happened and what to do."""
        sentence = wording.block_reason_text(NOT_STOPPED)
        self.assertEqual(sentence, (
            "Applying mods to the server folder was interrupted and must be finished. This is possible only while "
            "the server is stopped and no other DayZ-ServerMan uses this DayZ installation. "
            "Stop the server, then restart DayZ-ServerMan."))
        self.assertIn(sentence, {text for _fragment, text in wording.BLOCK_REASONS})
        self.assertFalse(wording.leaks_identifier(sentence))
        line = format_manager_record({"event": "operation_lane.recovery_block", "level": "ERROR",
                                      "occurred_at": "2026-10-04T10:00:00Z", "fields": {"reason": NOT_STOPPED}})
        self.assertTrue(line.endswith(f"Changes are now blocked. {sentence}"), line)
        # The two earlier reasons keep their own sentences
        self.assertNotEqual(wording.block_reason_text(NO_ROOT), sentence)
        self.assertNotEqual(wording.block_reason_text(UNPROVEN), sentence)

    def test_composition_recovers_only_through_the_guarded_function(self) -> None:
        """The composition root no longer runs the recovery itself; the workshop composition wires the guard."""
        root_source = Path(composition.__file__).read_text(encoding="utf-8")
        self.assertNotIn("PublicationRecovery", root_source)
        self.assertNotIn("mod publication", root_source.replace("mod publication is recovered", ""))
        wiring = Path(workshop_composition.__file__).read_text(encoding="utf-8")
        self.assertIn("recover_interrupted_publications(publication_journals, settings, operations, guard)", wiring)
        self.assertIn("guard = InstallationGuard(lifecycle, WindowsInstallationMutex())", wiring)
        self.assertEqual(wiring.count("InstallationGuard("), 1)
        self.assertIn(ServerState.STOPPED, set(ServerState) - set(REFUSALS))


if __name__ == "__main__":
    unittest.main()
