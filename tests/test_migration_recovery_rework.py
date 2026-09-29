"""Cover startup reconciliation for ambiguous migration publication states."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.repositories.migration_journal import MigrationJournalRepository  # noqa: E402
from dayz_serverman.repositories.migration_publication import (  # noqa: E402
    MigrationPublication, PublicationTarget,
)
from dayz_serverman.repositories.migrations import MigrationStorage, MigrationStorageError  # noqa: E402
try:  # noqa: E402
    from tests.migration_payload_fixtures import (
        index_bytes, profile_bytes, report_bytes, settings_bytes,
    )
except ModuleNotFoundError:  # bundled discovery adds tests directly to sys.path
    from migration_payload_fixtures import (
        index_bytes, profile_bytes, report_bytes, settings_bytes,
    )


SHA = hashlib.sha256(b"source").hexdigest()
FINGERPRINT = hashlib.sha256(b"preview").hexdigest()


class SyntheticCrash(BaseException):
    """Signal a simulated process crash at a named publication phase."""
    pass


class MigrationRecoveryReworkTests(unittest.TestCase):
    """Verify recovery reconciles mixed, malformed, and forged publication states."""

    def setUp(self) -> None:
        """Create a temporary root with two stored prior profiles."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_migration_recovery_")
        self.root = Path(self.temporary.name) / "Manager"
        self.storage = MigrationStorage(self.root / "data" / "migrations")
        self.journals = MigrationJournalRepository(self.storage.root / "publication-journals")
        self.first = self.root / "data" / "profiles" / "first.json"
        self.second = self.root / "data" / "profiles" / "second.json"
        self.first.parent.mkdir(parents=True)
        self.first.write_bytes(profile_bytes("first", revision=4, marker="prior"))
        self.second.write_bytes(profile_bytes("second", revision=4, marker="prior"))

    def tearDown(self) -> None:
        """Remove the temporary root."""
        self.temporary.cleanup()

    def publisher(self, hook=None) -> MigrationPublication:
        """Build a publication service sharing this test's storage and journals."""
        return MigrationPublication(self.root, self.storage, self.journals, hook)

    def targets(self, migration_id: str = "a" * 32) -> tuple[PublicationTarget, ...]:
        """Build profile and report targets for one migration identifier."""
        return (
            PublicationTarget("PROFILE", "first", self.first, profile_bytes("first", marker="output")),
            PublicationTarget("PROFILE", "second", self.second, profile_bytes("second", marker="output")),
            PublicationTarget(
                "REPORT", migration_id, self.storage.report_path(migration_id),
                report_bytes(migration_id),
            ),
        )

    def targets_with_settings(self, migration_id: str) -> tuple[PublicationTarget, ...]:
        """Prepend a manager settings target to the standard targets."""
        return (
            PublicationTarget(
                "SETTINGS", "Manager settings", self.root / "config" / "manager.json",
                settings_bytes(),
            ),
            *self.targets(migration_id),
        )

    def targets_with_index(self, migration_id: str) -> tuple[PublicationTarget, ...]:
        """Insert a legacy backup index target before the report target."""
        base = self.targets_with_settings(migration_id)
        return (
            *base[:-1],
            PublicationTarget(
                "LEGACY_BACKUP_INDEX", "Legacy backup index",
                self.storage.root / "legacy-backup-index.json",
                index_bytes(self.root / "Legacy backups"),
            ),
            base[-1],
        )

    def leave_recovery_required(self) -> None:
        """Publish with an unresolved report fault so recovery stays required."""
        def fault(phase: str) -> None:
            """Raise the synthetic fault while the report stays unresolved."""
            if phase in {"PUBLISH_REPORT", "BEFORE_COMPENSATE_REPORT"}:
                raise OSError("synthetic unresolved publication")

        with self.assertRaises(MigrationStorageError):
            self.publisher(fault).publish("a" * 32, SHA, FINGERPRINT, self.targets())

    def test_third_state_is_found_before_any_target_is_mutated(self) -> None:
        """Verify a third target state is detected before any target is mutated."""
        self.leave_recovery_required()
        # Restore the first target while the second holds a third state
        self.first.write_bytes(profile_bytes("first", revision=4, marker="prior"))
        self.second.write_bytes(b"third state")
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "RECOVERY_REQUIRED")
        self.assertEqual(self.first.read_bytes(), profile_bytes("first", revision=4, marker="prior"))
        self.assertEqual(self.second.read_bytes(), b"third state")
        journal = self.journals.read(self.journals.active_paths()[0])
        self.assertEqual(journal["state"], "RECOVERY_REQUIRED")

    def test_prior_and_output_mixture_compensates_only_output(self) -> None:
        """Verify a prior and output mixture compensates only the output side."""
        self.leave_recovery_required()
        # Return the first target to prior bytes while the second keeps output bytes
        self.first.write_bytes(profile_bytes("first", revision=4, marker="prior"))
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "READY")
        self.assertEqual(self.first.read_bytes(), profile_bytes("first", revision=4, marker="prior"))
        self.assertEqual(self.second.read_bytes(), profile_bytes("second", revision=4, marker="prior"))
        self.assertFalse(self.journals.active_paths())

    def test_output_only_marker_is_cleaned_only_while_targets_are_prior(self) -> None:
        """Verify an output-only marker is removed only while targets remain prior."""
        def crash(phase: str) -> None:
            """Raise SyntheticCrash when only the prepublication marker exists."""
            if phase == "PREPUBLICATION_MARKER":
                raise SyntheticCrash()

        with self.assertRaises(SyntheticCrash):
            self.publisher(crash).publish("b" * 32, SHA, FINGERPRINT, self.targets("b" * 32))
        stage = self.storage.root / f".{('b' * 32)}.stage"
        self.assertTrue((stage / "output").exists())
        self.assertFalse((stage / "recovery").exists())
        self.assertEqual(self.publisher().inspect_recovery()["state"], "READY")
        self.assertFalse(stage.exists())

        # Repeat the marker while one target has already moved to output bytes
        with self.assertRaises(SyntheticCrash):
            self.publisher(crash).publish("c" * 32, SHA, FINGERPRINT, self.targets("c" * 32))
        output_first = profile_bytes("first", marker="output")
        self.first.write_bytes(output_first)
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "RECOVERY_REQUIRED")
        self.assertEqual(self.first.read_bytes(), output_first)
        self.assertTrue((self.storage.root / f".{('c' * 32)}.stage").exists())

    def test_recovery_bytes_malformed_marker_and_valid_id_spoof_are_retained(self) -> None:
        """Verify recovery bytes, malformed markers, and spoofed stages are retained."""
        def crash(phase: str) -> None:
            """Raise SyntheticCrash once recovery evidence has been staged."""
            if phase == "RECOVERY_EVIDENCE_STAGED":
                raise SyntheticCrash()

        with self.assertRaises(SyntheticCrash):
            self.publisher(crash).publish("d" * 32, SHA, FINGERPRINT, self.targets("d" * 32))
        stage = self.storage.root / f".{('d' * 32)}.stage"
        self.assertTrue((stage / "recovery").exists())
        self.assertEqual(self.publisher().inspect_recovery()["state"], "RECOVERY_REQUIRED")

        # Add a malformed marker stage and a plausible spoofed stage
        malformed = self.storage.create_stage("e" * 32)
        (malformed / "plan.json").write_text("{bad", encoding="utf-8")
        spoof = self.storage.create_stage("f" * 32)
        (spoof / "unexpected.bin").write_bytes(b"spoof")
        result = self.publisher().inspect_recovery()
        self.assertEqual(result["state"], "RECOVERY_REQUIRED")
        self.assertTrue(malformed.exists())
        self.assertTrue(spoof.exists())

    def test_output_marker_with_invalid_digest_or_path_is_retained(self) -> None:
        """Verify output markers with invalid digests or paths are retained."""
        def crash(phase: str) -> None:
            """Raise SyntheticCrash right after the output marker is written."""
            if phase == "PREPUBLICATION_MARKER":
                raise SyntheticCrash()

        # Tamper one marker field per case and require retention
        for migration_id, field, value in (
            ("7" * 32, "staged_sha256", "not-a-digest"),
            ("8" * 32, "target_relative", "../escape.json"),
        ):
            with self.subTest(field=field), self.assertRaises(SyntheticCrash):
                self.publisher(crash).publish(
                    migration_id, SHA, FINGERPRINT, self.targets(migration_id),
                )
            stage = self.storage.root / f".{migration_id}.stage"
            marker = json.loads((stage / "plan.json").read_text(encoding="utf-8"))
            marker["destinations"][0][field] = value
            (stage / "plan.json").write_text(json.dumps(marker), encoding="utf-8")
            self.assertEqual(self.publisher().inspect_recovery()["state"], "RECOVERY_REQUIRED")
            self.assertTrue(stage.exists())

    def test_forged_destination_role_identity_and_inventory_are_retained(self) -> None:
        """Verify forged destination roles, identities, and inventories are retained."""
        def crash(phase: str) -> None:
            """Raise SyntheticCrash right after the output marker is written."""
            if phase == "PREPUBLICATION_MARKER":
                raise SyntheticCrash()

        # Forge one destination field per case and require retention
        cases = (
            (1, 1, "target_relative", "data/arbitrary.cfg"),
            (2, 0, "target_relative", "config/manager-copy.json"),
            (3, 1, "target_relative", "data/profiles/first-copy.json"),
            (4, 3, "target_relative", f"data/migrations/reports/{('4' * 32)}-copy.json"),
            (5, 1, "role", "SETTINGS"),
            (6, 2, "target_relative", "data/profiles/first.json"),
            (10, 3, "label", "b" * 32),
            (11, 1, "label", "wrong-profile"),
        )
        for number, index, field, value in cases:
            migration_id = f"{number:032x}"
            with self.subTest(field=field, value=value), self.assertRaises(SyntheticCrash):
                self.publisher(crash).publish(
                    migration_id, SHA, FINGERPRINT,
                    self.targets_with_settings(migration_id),
                )
            stage = self.storage.root / f".{migration_id}.stage"
            marker_path = stage / "plan.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["destinations"][index][field] = value
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            result = self.publisher().inspect_recovery()
            self.assertEqual(result["state"], "RECOVERY_REQUIRED")
            self.assertTrue(stage.exists())

    def test_fresh_composition_blocks_orphan_but_keeps_queries_available(self) -> None:
        """Verify a fresh composition blocks on an orphan stage but keeps queries working."""
        # Leave an orphan stage that no journal can claim
        stage = self.storage.create_stage("9" * 32)
        (stage / "plan.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        composition = build_composition(self.root)
        try:
            self.assertIsNotNone(composition.operations.recovery_block)
            response = composition.host_bridge.dispatch({
                "contract_version": 1, "request_id": "orphan-query",
                "method": "get_application_snapshot", "parameters": {},
            })
            self.assertTrue(response["success"])
        finally:
            composition.operations.shutdown(2)

    def test_external_index_marker_requires_exact_role_path_and_position(self) -> None:
        """Verify an external index marker is accepted only at its exact role, path, and position."""
        def crash(phase: str) -> None:
            """Raise SyntheticCrash right after the prepublication marker is written."""
            if phase == "PREPUBLICATION_MARKER":
                raise SyntheticCrash()

        mutations = (
            ("target_relative", "data/migrations/legacy-backup-index-copy.json"),
            ("label", "Other index"),
            ("role", "PROFILE"),
        )
        genuine_id = "f" * 32
        with self.assertRaises(SyntheticCrash):
            self.publisher(crash).publish(
                genuine_id, SHA, FINGERPRINT, self.targets_with_index(genuine_id),
            )
        self.assertEqual(self.publisher().inspect_recovery()["state"], "READY")
        for number, (field, value) in enumerate(mutations, 12):
            migration_id = f"{number:032x}"
            with self.assertRaises(SyntheticCrash):
                self.publisher(crash).publish(
                    migration_id, SHA, FINGERPRINT, self.targets_with_index(migration_id),
                )
            stage = self.storage.root / f".{migration_id}.stage"
            marker_path = stage / "plan.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["destinations"][-2][field] = value
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            self.assertEqual(self.publisher().inspect_recovery()["state"], "RECOVERY_REQUIRED")
            self.assertTrue(stage.exists())

if __name__ == "__main__":
    unittest.main()
