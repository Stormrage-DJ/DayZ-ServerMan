"""Owner steps of A13 with a held reader (3.3): refusal with nothing changed and the mapped code."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from folder_lock_fixtures import body_and_gate_free, held_reader, tree_hashes, writer_for  # noqa: E402
import test_mod_publication_application as publication_tests  # noqa: E402
import test_restores as restore_tests  # noqa: E402
from test_restore_startup_guard import NOT_STOPPED, RestoreGuardFixture  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile, ServerFolderBusy  # noqa: E402
from dayz_serverman.application import folder_writer_scope, startup_recoveries  # noqa: E402
from dayz_serverman.application.folder_writer_scope import WriterScope  # noqa: E402
from dayz_serverman.application.mod_publication import ModPublicationError, ModPublicationService  # noqa: E402
from dayz_serverman.application.restore_coordinator import _translate  # noqa: E402
from dayz_serverman.application.restores import RECOVERY_NOT_STOPPED, RestoreService  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorage  # noqa: E402
from dayz_serverman.repositories.mod_publication_journal import PublicationJournalRepository  # noqa: E402
from dayz_serverman.repositories.mod_publication_stage import ModPublicationStorage  # noqa: E402
from dayz_serverman.repositories.restore_journal import RestoreJournalRepository  # noqa: E402
from dayz_serverman.repositories.restore_storage import RestoreStorage  # noqa: E402

# Owner and recovery waits of the tests that do not measure the 5 s bound itself
SHORT_WAIT = 0.3
# Staging phases of a mod publication that come before the live change (6.5)
PUBLICATION_STAGING = {"PUBLICATION_PREFLIGHT", "DISCOVER_ITEM", "CACHE_PROOF_RECHECK", "STAGE_TARGET",
                       "CHECK_TARGET", "COPY_FILE", "COPY_KEY", "BEFORE_PUBLICATION"}


class _Context:
    """Operation context that records every relayed phase."""

    operation_id = "a13-publication"
    cancellation_requested = False

    def __init__(self) -> None:
        """Start with no phase."""
        self.phases: list[str] = []

    def checkpoint(self, phase: str, _percent: int) -> None:
        """Record the phase."""
        self.phases.append(phase)

    def record_evidence(self, _evidence) -> None:
        """Ignore the evidence."""
        return


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class PlainPublicationUnderReaderTests(publication_tests.PublicationApplicationFixture):
    """PUBLISH_MODS_AND_KEYS takes the writer side before BEFORE_PUBLICATION is relayed."""

    def setUp(self) -> None:
        """Build the publication fixture with a writer side on the manager's lock file."""
        super().setUp()
        self.lock_path = self.paths.data / "server-folders.lock"
        FolderLockFile.create(self.lock_path).close()
        self.writer = writer_for(self.lock_path)
        self.addCleanup(self.writer.close)
        self.service = ModPublicationService(
            self.profiles, self.settings, self.operations,
            lambda checkpoint: ModPublicationStorage(checkpoint=checkpoint),
            PublicationJournalRepository(self.paths.publication_journals),
            publication_tests._NoStartLifecycle(), folder_writer=self.writer,
        )

    def test_refusal_takes_the_pre_publication_path_with_nothing_changed(self) -> None:
        """CONTROL_CONFLICT; stage and journal removed; the last relayed phase is a staging phase."""
        preview = self.service.preview(self.request())
        before = tree_hashes(self.dayz)
        context = _Context()
        with held_reader(self.lock_path), patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", SHORT_WAIT):
            with self.assertRaises(ModPublicationError) as raised:
                self.service.publish(self.request(), preview["publication_fingerprint"], context)
        self.assertEqual(raised.exception.code, "CONTROL_CONFLICT")
        self.assertEqual(tree_hashes(self.dayz), before)
        self.assertEqual(list(self.paths.publication_journals.rglob("*.json")), [])
        # The service's own BEFORE_PUBLICATION at 55 % precedes staging; the storage's one was never relayed
        self.assertEqual(context.phases.count("BEFORE_PUBLICATION"), 1)
        self.assertIn(context.phases[-1], PUBLICATION_STAGING - {"BEFORE_PUBLICATION"})
        self.assertTrue(body_and_gate_free(self.lock_path))

    def test_without_a_reader_the_publication_runs_and_releases_the_writer_side(self) -> None:
        """No reader: the run publishes; afterwards the lock is free."""
        preview = self.service.preview(self.request())
        result = self.service.publish(self.request(), preview["publication_fingerprint"], _Context())
        self.assertEqual(result["publication_state"], "VERIFIED")
        self.assertTrue(body_and_gate_free(self.lock_path))

    def test_a_handed_over_scope_is_not_taken_again_and_is_released_after_the_publication(self) -> None:
        """Apply and restart hands its held scope over: no second acquisition, released after storage.publish."""
        preview = self.service.preview(self.request())
        scope = WriterScope(self.writer)
        scope.acquire()
        self.assertFalse(body_and_gate_free(self.lock_path))
        self.service.publish(self.request(), preview["publication_fingerprint"], _Context(), writer_scope=scope)
        self.assertEqual(scope.acquisitions, 1)
        self.assertFalse(scope.held)
        self.assertTrue(body_and_gate_free(self.lock_path))


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class RestoreUnderReaderTests(RestoreGuardFixture, unittest.TestCase):
    """RESTORE_BACKUP, the on-demand restore recovery and its startup recovery under a held reader."""

    def setUp(self) -> None:
        """Build the restore fixture and a writer side on a lock file beside the journals."""
        super().setUp()
        self.lock_path = self.root / "server-folders.lock"
        FolderLockFile.create(self.lock_path).close()
        self.writer = writer_for(self.lock_path)
        self.addCleanup(self.writer.close)

    def locked_service(self, lifecycle=None, mutex=None) -> RestoreService:
        """Return the restore service of the fixture with the writer side wired."""
        return RestoreService(
            self.profiles, self.settings, BackupStorage(), RestoreStorage(),
            RestoreJournalRepository(self.root / "operations" / "restore-journals"), self.recovery,
            lifecycle or restore_tests.FakeLifecycle(), mutex or restore_tests.FakeMutex(),
            folder_writer=self.writer,
        )  # type: ignore[arg-type]

    def test_apply_refuses_before_the_journal_with_nothing_changed(self) -> None:
        """The writer side is taken before WRITE_JOURNAL is relayed; staging is removed; CONTROL_CONFLICT."""
        service = self.locked_service()
        preview = service.preview("main", self.backup_id)
        before = tree_hashes(*self.roots)
        with held_reader(self.lock_path), patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", SHORT_WAIT):
            with self.assertRaises(ServerFolderBusy) as raised:
                self.apply(service, preview, "refused")
        self.assertEqual(tree_hashes(*self.roots), before)
        self.assertEqual(_translate(raised.exception).code.value, "CONTROL_CONFLICT")
        self.assertTrue(body_and_gate_free(self.lock_path))
        # Without the reader the same restore commits
        result, phases = self.apply(service, service.preview("main", self.backup_id), "committed")
        self.assertEqual(result["journal_state"], "COMMITTED")
        self.assertIn("WRITE_JOURNAL", phases)
        self.assertTrue(body_and_gate_free(self.lock_path))

    def test_last_relayed_phase_of_a_refusal_is_a_staging_phase(self) -> None:
        """Phase evidence (6.5): WRITE_JOURNAL is never relayed when the writer side refuses."""
        service = self.locked_service()
        preview = service.preview("main", self.backup_id)
        phases: list[str] = []
        with held_reader(self.lock_path), patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", SHORT_WAIT):
            with self.assertRaises(ServerFolderBusy):
                service.apply("main", self.backup_id, 3, 4, self.snapshot_manifest, preview["fingerprint"],
                              "phases", lambda phase, _percent: phases.append(phase))
        self.assertNotIn("WRITE_JOURNAL", phases)
        self.assertIn(phases[-1], {"VERIFY_SOURCE", "PREPARE_RECOVERY", "STAGE_TARGETS"})

    def test_on_demand_recovery_under_a_reader_returns_the_block_answer(self) -> None:
        """inspect_restore_recovery: the busy writer side is today's block answer; nothing changes."""
        self.interrupt()
        before = tree_hashes(*self.roots)
        service = self.locked_service(self.lifecycle, self.mutex)
        with held_reader(self.lock_path):
            result = service.inspect_recovery(wait_seconds=SHORT_WAIT)
        self.assertEqual((result["blocked"], result["reason"]), (True, RECOVERY_NOT_STOPPED))
        self.assertEqual(result["diagnostics"][0]["code"], "CONTROL_CONFLICT")
        self.assertEqual(tree_hashes(*self.roots), before)
        self.assertFalse(service.inspect_recovery(wait_seconds=SHORT_WAIT)["blocked"])

    def test_startup_recovery_under_a_reader_ends_in_todays_block(self) -> None:
        """The startup recovery waits its bound, then blocks with the existing reason."""
        self.interrupt()
        before = tree_hashes(*self.roots)
        service = self.locked_service(self.lifecycle, self.mutex)
        with held_reader(self.lock_path), patch.object(startup_recoveries, "STARTUP_RECOVERY_WAIT_SECONDS", SHORT_WAIT):
            startup_recoveries.recover_interrupted_restores(self.journals, service, self.operations)
        self.assertEqual(self.operations.recovery_block, NOT_STOPPED)
        self.assertEqual(tree_hashes(*self.roots), before)
        self.assertFalse(self.mutex.held)
        self.assertTrue(body_and_gate_free(self.lock_path))


if __name__ == "__main__":
    unittest.main()
