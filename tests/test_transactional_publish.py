"""Transactional publish staging, rollback, and journal recovery tests."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from reference.prototypes.transactional_publish import (  # noqa: E402
    FailurePoint,
    RecoveryState,
    publish_staged,
    recover_publication,
)


class InjectedFailure(OSError):
    """Publication failure injected at a scripted failure point."""
    pass


def fail_at(expected: FailurePoint):
    """Build an injector that fails at the named failure point."""
    def inject(actual: FailurePoint) -> None:
        """Raise once the observed failure point matches the expected one."""
        if actual == expected:
            raise InjectedFailure(f"injected at {actual.value}")

    return inject


class TransactionalPublishTests(unittest.TestCase):
    """Staged publication commit, rollback, and recovery contracts."""
    def setUp(self) -> None:
        """Create a temporary publication area with staged and live files."""
        self.temporary = tempfile.TemporaryDirectory(prefix="dayz_serverman_publish_")
        self.root = Path(self.temporary.name)
        self.staged = self.root / "staged.txt"
        self.live = self.root / "live.txt"
        self.journal = self.root / "operation.json"

    def tearDown(self) -> None:
        """Remove the temporary publication area."""
        self.temporary.cleanup()

    def prepare(self, *, old: str | None = "old", new: str = "new") -> None:
        """Write the staged bytes and optionally the existing live content."""
        self.staged.write_text(new, encoding="utf-8")
        if old is not None:
            self.live.write_text(old, encoding="utf-8")

    def test_success_publishes_new_content_and_commits_journal(self) -> None:
        """A happy-path publication writes new content and commits the journal."""
        # Run one publication over an existing live file
        self.prepare()
        result = publish_staged(self.staged, self.live, self.journal, "op-success")
        # Confirm the new content is live and the journal reports commit
        self.assertTrue(result.success)
        self.assertEqual(result.state, RecoveryState.NEW)
        self.assertEqual(self.live.read_text(encoding="utf-8"), "new")
        self.assertEqual(json.loads(self.journal.read_text())["phase"], "COMMITTED")

    def test_failure_after_journal_preserves_old_content(self) -> None:
        """A failure after journaling keeps the old content live."""
        # Inject a failure immediately after the journal is written
        self.prepare()
        result = publish_staged(
            self.staged,
            self.live,
            self.journal,
            "op-journal",
            fail_at(FailurePoint.AFTER_JOURNAL),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.state, RecoveryState.OLD)
        self.assertEqual(self.live.read_text(encoding="utf-8"), "old")

    def test_failure_after_old_move_restores_old_content(self) -> None:
        """A failure after moving the old file restores it to the live path."""
        # Inject a failure after the old file moved aside
        self.prepare()
        result = publish_staged(
            self.staged,
            self.live,
            self.journal,
            "op-old-moved",
            fail_at(FailurePoint.AFTER_OLD_MOVE),
        )
        self.assertEqual(result.state, RecoveryState.OLD)
        self.assertEqual(self.live.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staged.read_text(encoding="utf-8"), "new")

    def test_failure_after_new_move_rolls_back_both_versions(self) -> None:
        """A failure after promoting the new file rolls both versions back."""
        # Inject a failure after the new file replaced the live path
        self.prepare()
        result = publish_staged(
            self.staged,
            self.live,
            self.journal,
            "op-new-moved",
            fail_at(FailurePoint.AFTER_NEW_MOVE),
        )
        self.assertEqual(result.state, RecoveryState.OLD)
        self.assertEqual(self.live.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staged.read_text(encoding="utf-8"), "new")

    def test_first_publication_failure_restores_absent_old_state(self) -> None:
        """A first publication failure restores the absent old state."""
        # Publish with no pre-existing live file
        self.prepare(old=None)
        result = publish_staged(
            self.staged,
            self.live,
            self.journal,
            "op-first",
            fail_at(FailurePoint.BEFORE_COMMIT),
        )
        self.assertEqual(result.state, RecoveryState.OLD)
        self.assertFalse(self.live.exists())
        self.assertEqual(self.staged.read_text(encoding="utf-8"), "new")

    def test_recovery_failure_is_classified_and_preserves_evidence(self) -> None:
        """A recovery failure is classified and leaves its evidence on disk."""
        self.prepare()

        # Fail both promotion and recovery so evidence must be preserved
        def inject(point: FailurePoint) -> None:
            """Raise at both the promotion and recovery-restore failure points."""
            if point in (
                FailurePoint.AFTER_NEW_MOVE,
                FailurePoint.BEFORE_RECOVERY_RESTORE,
            ):
                raise InjectedFailure(f"injected at {point.value}")

        # Run the publication so it ends in recovery required
        result = publish_staged(
            self.staged,
            self.live,
            self.journal,
            "op-recovery-required",
            inject,
        )
        # Confirm the journal and recovery artifact remain for a later recovery
        recovery = self.root / ".live.txt.op-recovery-required.recovery"
        self.assertEqual(result.state, RecoveryState.RECOVERY_REQUIRED)
        self.assertTrue(self.journal.exists())
        self.assertTrue(recovery.exists())

    def test_startup_recovery_uses_persisted_journal(self) -> None:
        """Startup recovery completes a publication abandoned mid-promotion."""
        # Abandon the publication right after the new file was promoted
        self.prepare()
        with self.assertRaises(SystemExit):
            publish_staged(
                self.staged,
                self.live,
                self.journal,
                "op-deferred",
                lambda point: (_ for _ in ()).throw(SystemExit())
                if point == FailurePoint.AFTER_NEW_MOVE
                else None,
            )
        # Confirm recovery restores the old content from the persisted journal
        self.assertEqual(recover_publication(self.journal), RecoveryState.OLD)
        self.assertEqual(self.live.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.staged.read_text(encoding="utf-8"), "new")

    def test_corrupt_journal_requires_recovery(self) -> None:
        """A corrupt journal is reported as requiring recovery."""
        self.journal.write_text("not json", encoding="utf-8")
        self.assertEqual(
            recover_publication(self.journal),
            RecoveryState.RECOVERY_REQUIRED,
        )

    def test_untrusted_recovery_path_is_rejected_without_mutation(self) -> None:
        """An untrusted recovery path is rejected without touching the live file."""
        # Persist a journal whose recovery path leaves the publication area
        self.prepare()
        record = {
            "schema_version": 1,
            "operation_id": "op-untrusted",
            "phase": "OLD_MOVED",
            "staged_path": str(self.staged),
            "live_path": str(self.live),
            "recovery_path": str(self.root / "unexpected.txt"),
            "old_existed": True,
        }
        self.journal.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(recover_publication(self.journal), RecoveryState.RECOVERY_REQUIRED)
        self.assertEqual(self.live.read_text(encoding="utf-8"), "old")

    def test_invalid_operation_id_is_rejected_before_mutation(self) -> None:
        """An invalid operation identifier is rejected before any mutation."""
        self.prepare()
        with self.assertRaises(ValueError):
            publish_staged(self.staged, self.live, self.journal, "../escape")
        self.assertEqual(self.live.read_text(encoding="utf-8"), "old")


if __name__ == "__main__":
    unittest.main()
