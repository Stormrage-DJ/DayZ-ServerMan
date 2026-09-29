"""Cover publication storage faults, cancellation, and recovery handles."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dayz_serverman.repositories.mod_publication_journal import PublicationJournalRepository
from dayz_serverman.repositories.mod_publication_stage import (
    ModPublicationStorage,
    PublicationCancelled,
    PublicationStorageError,
)
from test_mod_publication_inventory import PublicationFixture


class PublicationFaultTests(PublicationFixture):
    """Verify storage faults restore prior bytes and keep recovery evidence."""

    def _dayz(self) -> Path:
        """Build a DayZ root with prior mod and key bytes for fault tests."""
        root = self.base / "DayZ"
        for relative, content in (("mods/alpha", b"old-a"), ("mods/beta", b"old-b")):
            target = root / relative
            target.mkdir(parents=True)
            (target / "old.pbo").write_bytes(content)
        (root / "keys").mkdir()
        (root / "keys" / "keep.bikey").write_bytes(b"keep")
        (root / "mods/local").mkdir()
        (root / "mods/local/local.pbo").write_bytes(b"local")
        return root

    def test_faults_before_and_during_each_target_restore_prior_bytes(self) -> None:
        """Verify a fault at each publication point restores every prior byte."""
        # Fault points spanning journal saves, target moves, and verifications
        phases = (
            ("AFTER_PREPARED_JOURNAL", -1),
            ("AFTER_PUBLISHING_SAVE", -1),
            ("BEFORE_PRIOR_MOVE", 0),
            ("AFTER_PRIOR_MOVED_SAVE", 0),
            ("BEFORE_OUTPUT_MOVE", 0),
            ("AFTER_OUTPUT_PUBLISHED_SAVE", 0),
            ("BEFORE_OUTPUT_VERIFY", 0),
            ("AFTER_OUTPUT_VERIFIED", 0),
            ("BEFORE_OUTPUT_MOVE", 1),
            ("BEFORE_OUTPUT_MOVE", 2),
        )
        for number, (phase, index) in enumerate(phases):
            with self.subTest(phase=phase, index=index):
                # Rebuild a fresh DayZ root for each fault point
                dayz = self._dayz()
                repository = PublicationJournalRepository(self.base / f"journals-{number}")

                def fault(actual: str, actual_index: int) -> None:
                    """Raise the synthetic fault at the watched phase and index."""
                    if (actual, actual_index) == (phase, index):
                        raise OSError("injected")

                storage = ModPublicationStorage(fault)
                journal = storage.stage(self.build(dayz), dayz)
                with self.assertRaises(PublicationStorageError) as raised:
                    storage.publish(journal, dayz, repository)
                self.assertFalse(raised.exception.recovery_required)
                self.assertEqual((dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
                self.assertEqual((dayz / "mods/beta/old.pbo").read_bytes(), b"old-b")
                self.assertEqual((dayz / "keys/keep.bikey").read_bytes(), b"keep")
                self.assertEqual((dayz / "mods/local/local.pbo").read_bytes(), b"local")
                self.assertFalse(any(path.name.startswith(".serverman-") for path in dayz.rglob("*")))
                if dayz.exists():
                    import shutil
                    shutil.rmtree(dayz)

    def test_stage_fault_leaves_no_residue_or_live_change(self) -> None:
        """Verify a staging fault leaves no residue or live change."""
        dayz = self._dayz()

        def fault(phase: str, index: int) -> None:
            """Raise the synthetic fault after the second staged target."""
            if (phase, index) == ("AFTER_STAGE", 1):
                raise OSError("injected stage failure")

        with self.assertRaises(OSError):
            ModPublicationStorage(fault).stage(self.build(dayz), dayz)
        self.assertEqual((dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
        self.assertFalse(any(path.name.startswith(".serverman-") for path in dayz.rglob("*")))

    def test_copy_checkpoint_cancellation_cleans_partial_stage(self) -> None:
        """Verify cancellation during copy cleans the partial stage."""
        dayz = self._dayz()

        def checkpoint(phase: str, index: int) -> None:
            """Raise a cancellation error during the first copied file."""
            if (phase, index) == ("COPY_FILE", 0):
                raise RuntimeError("cancelled")

        with self.assertRaises(RuntimeError):
            ModPublicationStorage(checkpoint=checkpoint).stage(self.build(dayz), dayz)
        self.assertEqual((dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
        self.assertFalse(any(path.name.startswith(".serverman-") for path in dayz.rglob("*")))

    def test_cancellation_before_publication_cleans_and_after_publication_rolls_back(self) -> None:
        """Verify cancellation cleans before publication and rolls back after it."""
        # Cancel before publication and again after the live target changed
        for phase, index in (("BEFORE_PUBLICATION", -1), ("AFTER_LIVE_TARGET", 0)):
            with self.subTest(phase=phase):
                dayz = self._dayz()
                repository = PublicationJournalRepository(self.base / f"cancel-{index}")

                def checkpoint(actual: str, actual_index: int) -> None:
                    """Raise cancellation at the watched phase and index."""
                    if (actual, actual_index) == (phase, index):
                        raise PublicationCancelled("cancelled")

                storage = ModPublicationStorage(checkpoint=checkpoint)
                journal = storage.stage(self.build(dayz), dayz)
                with self.assertRaises(PublicationStorageError) as raised:
                    storage.publish(journal, dayz, repository)
                self.assertEqual(raised.exception.code, "UPDATE_CANCELLED")
                self.assertEqual((dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
                self.assertFalse(any(path.name.startswith(".serverman-") for path in dayz.rglob("*")))
                import shutil
                shutil.rmtree(dayz)

    def test_cleanup_failure_keeps_unresolved_commit_for_startup_recovery(self) -> None:
        """Verify a cleanup failure keeps an unresolved commit for startup recovery."""
        dayz = self._dayz()
        repository = PublicationJournalRepository(self.base / "cleanup-journal")
        storage = ModPublicationStorage()
        journal = storage.stage(self.build(dayz), dayz)
        # Force the final cleanup to fail so the commit stays unresolved
        with patch(
            "dayz_serverman.repositories.mod_publication_stage.shutil.rmtree",
            side_effect=OSError("cleanup failed"),
        ):
            with self.assertRaises(PublicationStorageError) as raised:
                storage.publish(journal, dayz, repository)
        self.assertTrue(raised.exception.recovery_required)
        active = repository.load(repository.path_for("publication-1"))
        self.assertEqual(active.phase.value, "COMMITTED")
        self.assertFalse(active.resolved)
        # Verify recovery reports no block and the journal still retired
        from dayz_serverman.repositories.mod_publication_recovery import PublicationRecovery
        self.assertFalse(PublicationRecovery(repository).inspect(dayz)["blocked"])
        self.assertTrue((repository.retired_root / "publication-1.json").is_file())

    def test_durable_transition_hooks_cover_commit_and_compensation(self) -> None:
        """Verify durable transition hooks fire during commit and compensation."""
        dayz = self._dayz()
        events: list[tuple[str, int]] = []
        repository = PublicationJournalRepository(self.base / "hook-success")
        storage = ModPublicationStorage(lambda phase, index: events.append((phase, index)))
        journal = storage.stage(self.build(dayz), dayz)
        storage.publish(journal, dayz, repository)
        # Require the commit path to report every durable hook
        names = {item[0] for item in events}
        self.assertTrue({
            "AFTER_PUBLISHING_SAVE", "AFTER_PRIOR_MOVED_SAVE",
            "AFTER_OUTPUT_PUBLISHED_SAVE", "AFTER_COMMITTED_SAVE",
            "AFTER_CLEANUP", "AFTER_RETIREMENT",
        }.issubset(names))

        # Rebuild the tree, then fault one move to exercise compensation
        import shutil
        shutil.rmtree(dayz)
        dayz = self._dayz()
        events.clear()
        repository = PublicationJournalRepository(self.base / "hook-rollback")

        def fail_and_record(phase: str, index: int) -> None:
            """Record every hook and raise a fault before the second output move."""
            events.append((phase, index))
            if (phase, index) == ("BEFORE_OUTPUT_MOVE", 1):
                raise OSError("fault")

        storage = ModPublicationStorage(fail_and_record)
        with self.assertRaises(PublicationStorageError):
            storage.publish(storage.stage(self.build(dayz), dayz), dayz, repository)
        names = {item[0] for item in events}
        self.assertTrue({
            "AFTER_COMPENSATING_SAVE", "AFTER_COMPENSATION_TARGET_SAVE",
            "AFTER_ROLLED_BACK_SAVE", "AFTER_CLEANUP", "AFTER_RETIREMENT",
        }.issubset(names))


if __name__ == "__main__":
    unittest.main()
