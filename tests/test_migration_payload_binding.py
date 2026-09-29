"""Payload binding tests for migration publication planning and recovery."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.repositories.migration_journal import MigrationJournalRepository  # noqa: E402
from dayz_serverman.repositories.migration_publication import (  # noqa: E402
    MigrationPublication, PublicationTarget,
)
from dayz_serverman.repositories.migrations import MigrationStorage, MigrationStorageError  # noqa: E402
try:  # noqa: E402
    from tests.migration_payload_fixtures import profile_bytes, report_bytes
except ModuleNotFoundError:  # bundled discovery adds tests directly to sys.path
    from migration_payload_fixtures import profile_bytes, report_bytes


SHA = hashlib.sha256(b"source").hexdigest()
FINGERPRINT = hashlib.sha256(b"preview").hexdigest()


class SyntheticCrash(BaseException):
    """Raised by publication hooks to simulate an abrupt process crash."""
    pass


class MigrationPayloadBindingTests(unittest.TestCase):
    """Journal plan and payload identity contracts for migration publication."""
    def setUp(self) -> None:
        """Build the storage and journal repositories over a temporary manager."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_payload_binding_")
        self.root = Path(self.temporary.name) / "Manager"
        self.storage = MigrationStorage(self.root / "data" / "migrations")
        self.journals = MigrationJournalRepository(self.storage.root / "publication-journals")
        self.alpha = self.root / "data" / "profiles" / "alpha.json"
        self.beta = self.root / "data" / "profiles" / "beta.json"

    def tearDown(self) -> None:
        """Remove the temporary manager tree."""
        self.temporary.cleanup()

    def targets(self, migration_id: str, *, reversed_profiles: bool = False):
        """Build the two profile targets and one report target for a migration."""
        profiles = [
            PublicationTarget("PROFILE", "alpha", self.alpha, profile_bytes("alpha")),
            PublicationTarget("PROFILE", "beta", self.beta, profile_bytes("beta")),
        ]
        if reversed_profiles:
            profiles.reverse()
        return (*profiles, PublicationTarget(
            "REPORT", migration_id, self.storage.report_path(migration_id),
            report_bytes(migration_id),
        ))

    def publisher(self, hook=None) -> MigrationPublication:
        """Build a publication bound to the shared storage and journals."""
        return MigrationPublication(self.root, self.storage, self.journals, hook)

    def crash_prepared(self, migration_id: str) -> tuple[Path, Path]:
        """Crash the publication right after the journal is prepared."""
        def crash(phase: str) -> None:
            """Abort once the journal reaches the prepared state."""
            if phase == "JOURNAL_PREPARED":
                raise SyntheticCrash()
        with self.assertRaises(SyntheticCrash):
            self.publisher(crash).publish(
                migration_id, SHA, FINGERPRINT, self.targets(migration_id),
            )
        return self.journals.path(migration_id), self.storage.root / f".{migration_id}.stage"

    def test_canonical_profile_order_is_mandatory_and_genuine_publication_succeeds(self) -> None:
        """Reversed profile order is rejected while canonical order publishes."""
        rejected_id = "1" * 32
        with self.assertRaises(MigrationStorageError):
            self.publisher().publish(
                rejected_id, SHA, FINGERPRINT,
                self.targets(rejected_id, reversed_profiles=True),
            )
        self.assertFalse(self.alpha.exists())
        self.assertFalse(self.beta.exists())
        # Canonical order must publish both profiles and the report
        accepted_id = "2" * 32
        self.publisher().publish(accepted_id, SHA, FINGERPRINT, self.targets(accepted_id))
        self.assertEqual(self.alpha.read_bytes(), profile_bytes("alpha"))
        self.assertEqual(self.beta.read_bytes(), profile_bytes("beta"))

    def test_plan_and_slot_swap_matrix_blocks_without_target_mutation(self) -> None:
        """Plan and slot swaps block recovery without mutating any target."""
        cases = ("identity_swap", "entry_reorder", "slot_swap", "digest_swap")
        for number, case in enumerate(cases, 3):
            migration_id = f"{number:032x}"
            journal_path, _stage = self.crash_prepared(migration_id)
            document = json.loads(journal_path.read_text(encoding="utf-8"))
            first, second = document["destinations"][:2]
            if case == "identity_swap":
                for field in ("label", "payload_identity", "target_relative"):
                    first[field], second[field] = second[field], first[field]
            elif case == "entry_reorder":
                document["destinations"][:2] = [second, first]
            elif case == "slot_swap":
                for field in ("staged_relative", "staged_sha256"):
                    first[field], second[field] = second[field], first[field]
            else:
                first["staged_sha256"], second["staged_sha256"] = (
                    second["staged_sha256"], first["staged_sha256"],
                )
            journal_path.write_text(json.dumps(document), encoding="utf-8")
            # Tampered plans must require recovery and leave targets untouched
            with self.subTest(case=case):
                result = self.publisher().inspect_recovery()
                self.assertEqual(result["state"], "RECOVERY_REQUIRED")
                self.assertFalse(self.alpha.exists())
                self.assertFalse(self.beta.exists())
                self.assertTrue(journal_path.exists())
            journal_path.unlink()
            self.storage.cleanup(self.storage.root / f".{migration_id}.stage")

    def test_staged_profile_document_identity_tamper_blocks_even_with_matching_digest(self) -> None:
        """A staged document with the wrong identity blocks despite a matching digest."""
        migration_id = "8" * 32
        journal_path, stage = self.crash_prepared(migration_id)
        document = json.loads(journal_path.read_text(encoding="utf-8"))
        tampered = profile_bytes("beta", marker="alpha-slot")
        (stage / "output" / "0000.bin").write_bytes(tampered)
        document["destinations"][0]["staged_sha256"] = hashlib.sha256(tampered).hexdigest()
        journal_path.write_text(json.dumps(document), encoding="utf-8")
        # Recovery must reject the swapped document and keep both targets absent
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "RECOVERY_REQUIRED")
        self.assertFalse(self.alpha.exists())
        self.assertFalse(self.beta.exists())

    def test_terminal_active_and_retired_missing_stage_require_live_identity(self) -> None:
        """Missing stages require the live target identity in active and retired states."""
        for number, phase in ((9, "COMMITTED"), (10, "JOURNAL_RETIRED")):
            migration_id = f"{number:032x}"
            def crash(current: str, expected: str = phase) -> None:
                """Abort the publication once the expected phase is reached."""
                if current == expected:
                    raise SyntheticCrash()
            with self.assertRaises(SyntheticCrash):
                self.publisher(crash).publish(
                    migration_id, SHA, FINGERPRINT, self.targets(migration_id),
                )
            journal_path = (
                self.journals.retired_path(migration_id)
                if phase == "JOURNAL_RETIRED" else self.journals.path(migration_id)
            )
            stage = self.storage.root / f".{migration_id}.stage"
            self.storage.cleanup(stage)
            # A live target that does not match the journal identity must block
            tampered = profile_bytes("beta", marker="wrong-live-identity")
            self.alpha.write_bytes(tampered)
            document = json.loads(journal_path.read_text(encoding="utf-8"))
            document["destinations"][0]["staged_sha256"] = hashlib.sha256(tampered).hexdigest()
            journal_path.write_text(json.dumps(document), encoding="utf-8")
            with self.subTest(phase=phase):
                result = self.publisher().inspect_recovery()
                self.assertEqual(result["state"], "RECOVERY_REQUIRED")
                self.assertEqual(self.alpha.read_bytes(), tampered)
                self.assertTrue(journal_path.exists())
            journal_path.unlink()
            self.alpha.unlink(missing_ok=True)
            self.beta.unlink(missing_ok=True)
            self.storage.report_path(migration_id).unlink(missing_ok=True)

    def test_committed_missing_stage_with_valid_live_identity_retires(self) -> None:
        """A committed migration with a missing stage and valid identity retires."""
        migration_id = "b" * 32
        def crash(phase: str) -> None:
            """Abort the publication once it is committed."""
            if phase == "COMMITTED":
                raise SyntheticCrash()
        with self.assertRaises(SyntheticCrash):
            self.publisher(crash).publish(
                migration_id, SHA, FINGERPRINT, self.targets(migration_id),
            )
        self.storage.cleanup(self.storage.root / f".{migration_id}.stage")
        self.assertEqual(self.publisher().inspect_recovery()["state"], "READY")
        self.assertFalse(self.journals.active_paths())

    def test_rolled_back_active_and_retired_missing_stage_require_prior_identity(self) -> None:
        """Rolled-back journals require the prior identity in active and retired states."""
        for number, active in ((12, True), (13, False)):
            migration_id = f"{number:032x}"
            self.alpha.parent.mkdir(parents=True, exist_ok=True)
            self.alpha.write_bytes(profile_bytes("alpha", revision=4, marker="prior"))
            self.beta.write_bytes(profile_bytes("beta", revision=4, marker="prior"))
            def fault(phase: str) -> None:
                """Fail report publication and abort once the journal retires."""
                if phase == "PUBLISH_REPORT":
                    raise OSError("synthetic publication failure")
                if phase == "JOURNAL_RETIRED":
                    raise SyntheticCrash()
            with self.assertRaises(SyntheticCrash):
                self.publisher(fault).publish(
                    migration_id, SHA, FINGERPRINT, self.targets(migration_id),
                )
            retired = self.journals.retired_path(migration_id)
            journal_path = self.journals.path(migration_id) if active else retired
            if active:
                os.replace(retired, journal_path)
            self.storage.cleanup(self.storage.root / f".{migration_id}.stage")
            # A mismatched prior identity must block recovery for both states
            tampered = profile_bytes("beta", revision=4, marker="wrong-prior-identity")
            self.alpha.write_bytes(tampered)
            document = json.loads(journal_path.read_text(encoding="utf-8"))
            document["destinations"][0]["prior_sha256"] = hashlib.sha256(tampered).hexdigest()
            journal_path.write_text(json.dumps(document), encoding="utf-8")
            with self.subTest(active=active):
                result = self.publisher().inspect_recovery()
                self.assertEqual(result["state"], "RECOVERY_REQUIRED")
                self.assertEqual(self.alpha.read_bytes(), tampered)
                self.assertTrue(journal_path.exists())
            journal_path.unlink()
            self.alpha.unlink()
            self.beta.unlink()


if __name__ == "__main__":
    unittest.main()
