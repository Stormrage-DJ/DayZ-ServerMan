"""Cover migration publication commit, rollback, and recovery reconciliation."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.repositories.migration_journal import (  # noqa: E402
    MigrationJournalError, MigrationJournalRepository,
)
from dayz_serverman.repositories.migration_publication import (  # noqa: E402
    MigrationPublication, PublicationTarget,
)
from dayz_serverman.repositories.migrations import MigrationStorage, MigrationStorageError  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.application.operations.models import QueueUnavailable  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
try:  # noqa: E402
    from tests.migration_payload_fixtures import profile_bytes, report_bytes
except ModuleNotFoundError:  # bundled discovery adds tests directly to sys.path
    from migration_payload_fixtures import profile_bytes, report_bytes


SHA = hashlib.sha256(b"source").hexdigest()
FINGERPRINT = hashlib.sha256(b"preview").hexdigest()


class SyntheticCrash(BaseException):
    """Signal a simulated process crash at a named publication phase."""
    pass


class MigrationPublicationTests(unittest.TestCase):
    """Verify migration publication commits, compensates, and reconciles crash boundaries."""

    def setUp(self) -> None:
        """Prepare an isolated manager root with storage and journal repositories."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_migration_publish_")
        self.root = Path(self.temporary.name) / "Manager"
        self.storage = MigrationStorage(self.root / "data" / "migrations")
        self.journals = MigrationJournalRepository(
            self.storage.root / "publication-journals",
        )
        self.blocked: list[str] = []
        self.target = self.root / "data" / "profiles" / "main.json"

    def tearDown(self) -> None:
        """Remove the temporary manager root."""
        self.temporary.cleanup()

    def publisher(self, hook=None) -> MigrationPublication:
        """Build a publication service sharing this test's storage and journals."""
        return MigrationPublication(
            self.root, self.storage, self.journals, hook, self.blocked.append,
        )

    def publish(self, publisher: MigrationPublication, migration_id: str = "a" * 32) -> None:
        """Publish one profile and report pair through the given publisher."""
        publisher.publish(migration_id, SHA, FINGERPRINT, (
            PublicationTarget("PROFILE", "main", self.target, profile_bytes("main", marker="new")),
            PublicationTarget(
                "REPORT", migration_id, self.storage.report_path(migration_id),
                report_bytes(migration_id),
            ),
        ))

    def test_success_retires_journal_and_cleans_stage(self) -> None:
        """Verify a clean publication updates targets and clears stage and journal artifacts."""
        self.publish(self.publisher())
        self.assertEqual(self.target.read_bytes(), profile_bytes("main", marker="new"))
        self.assertEqual(self.journals.active_paths(), ())
        self.assertEqual(self.journals.retired_paths(), ())
        self.assertFalse((self.storage.root / f".{('a' * 32)}.stage").exists())

    def test_startup_removes_proven_prejournal_stage_only(self) -> None:
        """Verify recovery removes only stages that predate any journal."""
        # Remove a stage whose migration journal cannot exist yet
        orphan = self.storage.create_stage("c" * 32)
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "READY")
        self.assertFalse(orphan.exists())

        # Keep an unknown stage that recovery cannot prove safe to delete
        unknown = self.storage.root / ".unknown.stage"
        unknown.mkdir()
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "RECOVERY_REQUIRED")
        self.assertTrue(unknown.exists())

    def test_fault_rolls_back_exact_prior_bytes_and_cleans(self) -> None:
        """Verify a mid-publication fault restores the exact prior bytes and cleans up."""
        # Seed the destination with known prior profile bytes
        self.target.parent.mkdir(parents=True)
        prior = profile_bytes("main", revision=3, marker="prior")
        self.target.write_bytes(prior)

        def fault(phase: str) -> None:
            """Raise the synthetic fault when the profile publish begins."""
            if phase == "PUBLISH_PROFILE":
                raise OSError("synthetic publication fault")

        with self.assertRaises(OSError):
            self.publish(self.publisher(fault))
        self.assertEqual(self.target.read_bytes(), prior)
        self.assertEqual(self.journals.active_paths(), ())
        self.assertEqual(self.journals.retired_paths(), ())

    def test_rollback_failure_blocks_same_process_and_keeps_evidence(self) -> None:
        """Verify a failed rollback blocks operations and keeps recovery evidence."""
        operations = OperationManager(OperationStore(self.root / "data" / "operations"))
        def fault(phase: str) -> None:
            """Raise the synthetic fault during publish and during compensation."""
            if phase in {"PUBLISH_PROFILE", "BEFORE_COMPENSATE_PROFILE"}:
                raise OSError("synthetic recovery fault")

        with self.assertRaises(MigrationStorageError) as caught:
            self.publish(MigrationPublication(
                self.root, self.storage, self.journals, fault,
                operations.block_for_recovery,
            ))
        self.assertTrue(caught.exception.recovery_required)
        self.assertIsNotNone(operations.recovery_block)
        with self.assertRaises(QueueUnavailable):
            operations.submit("PROBE", lambda _context: {})
        self.assertTrue(self.journals.active_paths())
        self.assertTrue((self.storage.root / f".{('a' * 32)}.stage").exists())
        operations.shutdown(1)

    def test_startup_compensates_crash_after_replace_before_step_record(self) -> None:
        """Verify startup compensation reverses a crash after replace but before its record."""
        # Seed the destination, then crash right after the profile replace
        self.target.parent.mkdir(parents=True)
        prior = profile_bytes("main", revision=2, marker="prior")
        self.target.write_bytes(prior)

        def crash(phase: str) -> None:
            """Raise SyntheticCrash once the profile replace has happened."""
            if phase == "AFTER_REPLACE_PROFILE":
                raise SyntheticCrash()

        with self.assertRaises(SyntheticCrash):
            self.publish(self.publisher(crash))
        self.assertEqual(self.target.read_bytes(), profile_bytes("main", marker="new"))
        # Recover and verify the stage reconciles back to the prior bytes
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "READY")
        self.assertEqual(self.target.read_bytes(), prior)
        self.assertFalse(self.journals.active_paths())

    def test_impossible_or_tampered_journal_fails_closed(self) -> None:
        """Verify a tampered journal fails closed and keeps its evidence."""
        def crash(phase: str) -> None:
            """Raise SyntheticCrash right after the journal is prepared."""
            if phase == "JOURNAL_PREPARED":
                raise SyntheticCrash()

        with self.assertRaises(SyntheticCrash):
            self.publish(self.publisher(crash))
        path = self.journals.active_paths()[0]
        document = json.loads(path.read_text(encoding="utf-8"))
        # Flip the committed flag so the stored journal contradicts itself
        document["committed"] = True
        path.write_text(json.dumps(document), encoding="utf-8")
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "RECOVERY_REQUIRED")
        self.assertTrue(self.blocked)
        self.assertTrue(path.exists())

    def test_journal_rejects_cross_field_destination_state(self) -> None:
        """Verify the journal rejects destination state that contradicts its flags."""
        # Claim a published destination while publication never started
        document = {
            "source_digest": SHA, "preview_fingerprint": FINGERPRINT,
            "state": "PREPARED", "publication_started": False,
            "committed": False, "resolved": False, "result": None,
            "destinations": [{
                "role": "PROFILE", "label": "main", "target_relative": "data/main.json",
                "payload_identity": "main", "prior_payload_identity": None,
                "staged_relative": "output/0000.bin", "recovery_relative": None,
                "prior_exists": False, "prior_sha256": None,
                "staged_sha256": hashlib.sha256(b"new").hexdigest(), "state": "PUBLISHED",
            }],
        }
        with self.assertRaises(MigrationJournalError):
            self.journals.create("b" * 32, document)

    def test_fresh_composition_blocks_ambiguous_recovery_but_queries_work(self) -> None:
        """Verify a fresh composition blocks on ambiguous recovery but answers queries."""
        def fault(phase: str) -> None:
            """Raise the synthetic fault during publish and during compensation."""
            if phase in {"PUBLISH_PROFILE", "BEFORE_COMPENSATE_PROFILE"}:
                raise OSError("synthetic recovery fault")

        with self.assertRaises(MigrationStorageError):
            self.publish(self.publisher(fault))
        # Leave a third target state that recovery cannot classify
        self.target.write_bytes(b"ambiguous third state")
        composition = build_composition(self.root)
        try:
            self.assertIsNotNone(composition.operations.recovery_block)
            with self.assertRaises(QueueUnavailable):
                composition.operations.submit("PROBE", lambda _context: {})
            response = composition.host_bridge.dispatch({
                "contract_version": 1, "request_id": "recovery-query",
                "method": "get_application_snapshot", "parameters": {},
            })
            self.assertTrue(response["success"])
        finally:
            composition.operations.shutdown(2)

    def test_startup_reconciles_every_publication_and_retirement_crash_boundary(self) -> None:
        """Verify reconciliation succeeds at every publication and retirement crash boundary."""
        # Replay every durable crash boundary through a fresh publication
        phases = (
            "JOURNAL_PREPARED", "AFTER_REPLACE_PROFILE", "PUBLISH_PROFILE",
            "AFTER_REPLACE_REPORT", "PUBLISH_REPORT", "REPORT", "COMMITTED",
            "JOURNAL_RETIRED",
        )
        for index, crash_phase in enumerate(phases):
            with self.subTest(crash_phase=crash_phase):
                # Build an isolated manager root for this crash boundary
                owner = self.root / f"case-{index}"
                storage = MigrationStorage(owner / "data" / "migrations")
                journals = MigrationJournalRepository(storage.root / "publication-journals")
                profile = owner / "data" / "profiles" / "main.json"
                migration_id = f"{index + 1:032x}"
                report = storage.report_path(migration_id)
                profile.parent.mkdir(parents=True)
                old_profile = profile_bytes("main", revision=3, marker="old")
                new_profile = profile_bytes("main", marker="new")
                profile.write_bytes(old_profile)

                def crash(phase: str, expected: str = crash_phase) -> None:
                    """Raise SyntheticCrash when the crash phase is reached."""
                    if phase == expected:
                        raise SyntheticCrash()

                publisher = MigrationPublication(owner, storage, journals, crash)
                with self.assertRaises(SyntheticCrash):
                    publisher.publish(
                        migration_id, SHA, FINGERPRINT,
                        (PublicationTarget("PROFILE", "main", profile, new_profile),
                         PublicationTarget("REPORT", migration_id, report,
                                           report_bytes(migration_id))),
                    )
                # Reconcile from a fresh publication and verify the boundary outcome
                result = MigrationPublication(owner, storage, journals).inspect_recovery()
                self.assertEqual(result["state"], "READY")
                committed = crash_phase in {"COMMITTED", "JOURNAL_RETIRED"}
                self.assertEqual(profile.read_bytes(), new_profile if committed else old_profile)
                self.assertEqual(report.exists(), committed)
                self.assertFalse(journals.active_paths())
                self.assertFalse(journals.retired_paths())
                self.assertFalse(any(storage.root.glob(".*.stage")))


if __name__ == "__main__":
    unittest.main()
