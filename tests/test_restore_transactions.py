"""Restore transaction tests for publication faults, cancellation, and journals."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.domain.backups import BackupManifest, ManifestEntry, entry_path_key  # noqa: E402
from dayz_serverman.repositories.restore_journal import RestoreJournalRepository  # noqa: E402
from dayz_serverman.repositories.restore_journal import RestoreJournalError  # noqa: E402
from dayz_serverman.repositories.restore_paths import RestorePathError, safe_root  # noqa: E402
from dayz_serverman.repositories.restore_storage import RestoreStorage, RestoreStorageError  # noqa: E402


def checksum(value: bytes) -> str:
    """Return the SHA-256 hex digest of the given bytes."""
    return hashlib.sha256(value).hexdigest()


class RestoreTransactionTests(unittest.TestCase):
    """Restore storage contracts across publication, cancellation, and crash boundaries."""
    def setUp(self) -> None:
        """Build a dayz tree, snapshot payload, and signed manifest fixture."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_restore_transaction_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "DáyZ"
        self.snapshot = self.root / "snapshot"
        self.recovery = self.root / "manager" / "backups" / "recovery"
        self.recovery.mkdir(parents=True)
        entries: list[ManifestEntry] = []
        self.old: dict[str, bytes | None] = {
            "Config/a.cfg": b"old-a",
            "Config/b.cfg": b"old-b",
            "Config/c.cfg": None,
        }
        for relative, old in self.old.items():
            new = f"new:{relative}".encode()
            source = self.snapshot / "payload" / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(new)
            target = self.dayz / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if old is not None:
                target.write_bytes(old)
            entries.append(ManifestEntry(f"payload/{relative}", len(new), checksum(new)))
        ordered = tuple(sorted(entries, key=lambda item: entry_path_key(item.path)))
        self.manifest = BackupManifest(
            "fixture-backup", "main", 3, 4, "2026-09-25T12:00:00.000Z", ordered,
        ).signed()
        self.journals = RestoreJournalRepository(self.root / "manager" / "data" / "journals")

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def assert_old_state(self) -> None:
        """Assert every live target still holds its pre-restore content."""
        for relative, old in self.old.items():
            target = self.dayz / relative
            if old is None:
                self.assertFalse(target.exists(), relative)
            else:
                self.assertEqual(target.read_bytes(), old, relative)

    def test_each_publication_group_fault_compensates_in_reverse(self) -> None:
        """A fault in any publication group compensates previously restored files."""
        # Failing each publication group in turn must roll everything back
        for fail_index in range(3):
            with self.subTest(group=fail_index):
                def fault(phase: str, index: int, stop: int = fail_index) -> None:
                    """Fail after the publication of the selected group index."""
                    if phase == "AFTER_PUBLISH" and index == stop:
                        raise OSError("synthetic group interruption")
                operation = f"fault-{fail_index}"
                with self.assertRaisesRegex(RestoreStorageError, "prior files were restored"):
                    RestoreStorage(fault_hook=fault).restore(
                        self.snapshot, self.manifest, self.dayz, self.recovery,
                        self.journals, operation, lambda _phase, _percent: None,
                    )
                self.assert_old_state()
                journal = self.journals.load(self.journals.retired_path_for(operation))
                self.assertEqual(journal.phase, "ROLLED_BACK")
                self.assertTrue(journal.resolved)
                self.assertFalse((self.recovery / operation).exists())
                self.assertFalse(any(self.dayz.rglob("*.restore-stage")))

    def test_space_copy_path_and_network_failures_do_not_mutate_live_targets(self) -> None:
        """Space, copy, and path failures never mutate live targets."""
        # A full disk stops the restore before any publication
        storage = RestoreStorage(disk_usage=lambda _path: SimpleNamespace(free=0))
        with self.assertRaisesRegex(RestoreStorageError, "free space"):
            storage.restore(
                self.snapshot, self.manifest, self.dayz, self.recovery,
                self.journals, "no-space", lambda _phase, _percent: None,
            )
        self.assert_old_state()
        self.assertFalse((self.recovery / "no-space").exists())
        self.assertFalse(any(self.dayz.rglob("*.restore-stage")))

        # A copy failure surfaces without touching live targets
        with patch(
            "dayz_serverman.repositories.restore_storage.copy_verified",
            side_effect=OSError("synthetic copy failure"),
        ):
            with self.assertRaises(OSError):
                RestoreStorage().restore(
                    self.snapshot, self.manifest, self.dayz, self.recovery,
                    self.journals, "copy-failure", lambda _phase, _percent: None,
                )
        self.assert_old_state()
        self.assertFalse((self.recovery / "copy-failure").exists())
        self.assertFalse(any(self.dayz.rglob("*.restore-stage")))
        # Network roots are rejected as unsafe restore roots
        with self.assertRaises(RestorePathError):
            safe_root(Path(r"\\server\share"))
        unsafe = BackupManifest(
            "unsafe", "main", 3, 4, "2026-09-25T12:00:00.000Z",
            (ManifestEntry("payload/Config/a.cfg:ads", 1, "a" * 64),),
        ).signed()
        with self.assertRaises(RestorePathError):
            RestoreStorage().targets(self.snapshot, unsafe, self.dayz)

    def test_runtime_mapping_rejects_unknown_prefix_and_target_overlap(self) -> None:
        """Runtime mapping rejects unknown prefixes and overlapping targets."""
        # Mods outside the runtime mapping prefixes are rejected
        unknown = BackupManifest(
            "unknown", "main", 3, 4, "2026-09-25T12:00:00.000Z",
            (ManifestEntry("mods/@unsafe/file.pbo", 1, "a" * 64),),
        ).signed()
        with self.assertRaises(RestorePathError):
            RestoreStorage().targets(self.snapshot, unknown, self.dayz, "profiles\\main")

        contents = b"same"
        entries = (
            ManifestEntry("payload/profiles/main/state.json", 4, checksum(contents)),
            ManifestEntry("runtime-profile/state.json", 4, checksum(contents)),
        )
        for entry in entries:
            path = self.snapshot / entry.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        (self.dayz / "profiles" / "main").mkdir(parents=True, exist_ok=True)
        # The same content mapped into two roots is an overlap
        overlap = BackupManifest(
            "overlap", "main", 3, 4, "2026-09-25T12:00:00.000Z", entries,
        ).signed()
        with self.assertRaisesRegex(RestoreStorageError, "overlap"):
            RestoreStorage().targets(self.snapshot, overlap, self.dayz, "profiles\\main")

    def test_startup_reconciles_multiple_published_groups_in_reverse(self) -> None:
        """Startup reconciliation rolls back interrupted published groups."""
        def interrupt(phase: str, index: int) -> None:
            """Interrupt after the second publication and before compensation."""
            if (phase == "AFTER_PUBLISH" and index == 1) or phase == "BEFORE_COMPENSATE":
                raise OSError("synthetic interrupted compensation")

        with self.assertRaises(RestoreStorageError) as raised:
            RestoreStorage(fault_hook=interrupt).restore(
                self.snapshot, self.manifest, self.dayz, self.recovery,
                self.journals, "startup-reconcile", lambda _phase, _percent: None,
            )
        self.assertTrue(raised.exception.recovery_required)
        self.assertTrue(self.journals.path_for("startup-reconcile").exists())
        # A fresh inspection reconciles the journal and rolls the targets back
        result = RestoreStorage().inspect(self.journals, self.dayz, self.recovery)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["diagnostics"], [])
        self.assert_old_state()
        journal = self.journals.load(self.journals.retired_path_for("startup-reconcile"))
        self.assertEqual(journal.phase, "ROLLED_BACK")
        self.assertTrue(journal.resolved)
        self.assertFalse((self.recovery / "startup-reconcile").exists())
        self.assertFalse(any(self.dayz.rglob("*.restore-stage")))

    def test_cancel_at_each_prepublication_point_removes_all_artifacts(self) -> None:
        """Cancellation at each pre-publication point removes every artifact."""
        # Every cancellation point before publication must leave no artifact behind
        points = (("VERIFY_SOURCE", 1), ("STAGE_TARGETS", 1), ("STAGE_TARGETS", 2),
                  ("STAGE_TARGETS", 3),
                  ("PREPARE_RECOVERY", 1), ("WRITE_JOURNAL", 1))
        for offset, (phase, occurrence) in enumerate(points):
            with self.subTest(phase=phase, occurrence=occurrence):
                operation = f"cancel-{offset}"
                seen = 0
                def cancel(current: str, _percent: int) -> None:
                    """Raise on the selected occurrence of the watched phase."""
                    nonlocal seen
                    if current == phase:
                        seen += 1
                        if seen == occurrence:
                            raise RuntimeError("synthetic cancellation")
                with self.assertRaisesRegex(RuntimeError, "synthetic cancellation"):
                    RestoreStorage().restore(
                        self.snapshot, self.manifest, self.dayz, self.recovery,
                        self.journals, operation, cancel,
                    )
                self.assert_old_state()
                self.assertFalse((self.recovery / operation).exists())
                self.assertFalse(any(self.dayz.rglob("*.restore-stage")))
                self.assertFalse(self.journals.path_for(operation).exists())

    def test_committed_flags_require_proven_target_digests(self) -> None:
        """Committing requires proven target digests after an interrupted retirement."""
        def stop_retirement(phase: str) -> None:
            """Interrupt the retirement so the committed journal stays active."""
            if phase == "BEFORE_RETIRE":
                raise OSError("synthetic retirement interruption")
        journals = RestoreJournalRepository(self.journals.root, retire_hook=stop_retirement)
        with self.assertRaises(RestoreStorageError) as raised:
            RestoreStorage().restore(
                self.snapshot, self.manifest, self.dayz, self.recovery,
                journals, "mixed-committed", lambda _phase, _percent: None,
            )
        self.assertTrue(raised.exception.recovery_required)
        # Diverging the live target after publication demands recovery
        (self.dayz / "Config" / "a.cfg").write_bytes(b"old-a")
        result = RestoreStorage().inspect(self.journals, self.dayz, self.recovery)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["diagnostics"][0]["code"], "RECOVERY_REQUIRED")

    def test_cross_field_impossible_journal_is_rejected(self) -> None:
        """A cross-field impossible journal is rejected at load time."""
        def stop_retirement(_phase: str) -> None:
            """Interrupt retirement to keep the journal available for editing."""
            raise OSError("synthetic retirement interruption")
        journals = RestoreJournalRepository(self.journals.root, retire_hook=stop_retirement)
        with self.assertRaises(RestoreStorageError):
            RestoreStorage().restore(
                self.snapshot, self.manifest, self.dayz, self.recovery,
                journals, "impossible", lambda _phase, _percent: None,
            )
        path = self.journals.path_for("impossible")
        # Rewrite the journal into a cross-field impossible state
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["phase"] = "PREPARED"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(RestoreJournalError):
            self.journals.load(path)
        self.assertTrue(RestoreStorage().inspect(self.journals, self.dayz, self.recovery)["blocked"])

    def test_retirement_crash_boundaries_reconcile_without_trusting_flags(self) -> None:
        """Crash boundaries around retirement reconcile without trusting flags."""
        # Both retirement crash boundaries must reconcile the journal state
        for phase in ("BEFORE_RETIRE", "AFTER_RETIRE"):
            with self.subTest(phase=phase):
                operation = f"retire-{phase.lower()}"
                def interrupt(current: str, expected: str = phase) -> None:
                    """Interrupt at the selected retirement boundary."""
                    if current == expected:
                        raise OSError("synthetic retirement crash")
                journals = RestoreJournalRepository(self.journals.root, retire_hook=interrupt)
                with self.assertRaises(RestoreStorageError) as raised:
                    RestoreStorage().restore(
                        self.snapshot, self.manifest, self.dayz, self.recovery,
                        journals, operation, lambda _phase, _percent: None,
                    )
                self.assertTrue(raised.exception.recovery_required)
                active = self.journals.path_for(operation)
                retired = self.journals.retired_path_for(operation)
                self.assertEqual((active.exists(), retired.exists()),
                                 (phase == "BEFORE_RETIRE", phase == "AFTER_RETIRE"))
                result = RestoreStorage().inspect(self.journals, self.dayz, self.recovery)
                self.assertFalse(result["blocked"])
                self.assertFalse(active.exists())
                self.assertTrue(retired.exists())

    def test_reparse_target_parent_is_rejected_when_supported(self) -> None:
        """Reparse-point target parents are rejected when links are available."""
        real = self.root / "real-target"
        real.mkdir()
        link = self.dayz / "Linked"
        # Create a real reparse point when the platform allows it
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlink creation is unavailable")
        source = self.snapshot / "payload" / "Linked" / "value.cfg"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"value")
        manifest = BackupManifest(
            "link", "main", 3, 4, "2026-09-25T12:00:00.000Z",
            (ManifestEntry("payload/Linked/value.cfg", 5, checksum(b"value")),),
        ).signed()
        with self.assertRaises(RestorePathError):
            RestoreStorage().targets(self.snapshot, manifest, self.dayz)

    def test_runtime_reparse_parent_is_rejected_when_supported(self) -> None:
        """Reparse-point runtime parents are rejected when links are available."""
        runtime = self.dayz / "profiles" / "main"
        runtime.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside-runtime"
        outside.mkdir()
        link = runtime / "linked"
        # Create a real reparse point when the platform allows it
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlink creation is unavailable")
        source = self.snapshot / "runtime-profile" / "linked" / "state.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"state")
        manifest = BackupManifest(
            "runtime-link", "main", 3, 4, "2026-09-25T12:00:00.000Z",
            (ManifestEntry("runtime-profile/linked/state.json", 5, checksum(b"state")),),
        ).signed()
        with self.assertRaises(RestorePathError):
            RestoreStorage().targets(self.snapshot, manifest, self.dayz, "profiles\\main")


if __name__ == "__main__":
    unittest.main()
