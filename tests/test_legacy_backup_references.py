"""Tests for legacy backup reference inventory, revalidation, and index integrity."""
from __future__ import annotations

import json
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.legacy_backups import LegacyBackupService  # noqa: E402
from dayz_serverman.repositories.legacy_backup_index import (  # noqa: E402
    LegacyBackupIndexError, LegacyBackupIndexRepository,
)
from dayz_serverman.repositories.external_root import (  # noqa: E402
    ExternalRootError, validate_external_root,
)
from dayz_serverman.repositories.legacy_source import (  # noqa: E402
    LegacySourceError, inspect_legacy_root,
)


class LegacyBackupReferenceTests(unittest.TestCase):
    """Reference inventory, revalidation, and index integrity contracts."""
    def setUp(self) -> None:
        """Create the manager data tree and the legacy source with backup folders."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_legacy_refs_")
        self.root = Path(self.temporary.name)
        self.manager = self.root / "Portable Mánager"
        self.data = self.manager / "data"
        self.data.mkdir(parents=True)
        self.legacy = self.root / "Légacy Source"
        (self.legacy / "dayz_server_manager").mkdir(parents=True)
        (self.legacy / "dayz_server_manager-profiles").mkdir()
        self.backups = self.legacy / "dayz_server_manager-backups"
        self.backups.mkdir()
        self.repository = LegacyBackupIndexRepository(
            self.data / "migrations" / "legacy-backup-index.json",
        )

    def tearDown(self) -> None:
        """Remove the temporary manager and legacy trees."""
        self.temporary.cleanup()

    def inventory(self):
        """Inspect the legacy root as the manager and return the inventory."""
        return inspect_legacy_root(str(self.legacy), self.manager, self.data)

    def test_nested_unicode_inventory_is_stable_sanitized_and_read_only(self) -> None:
        """Nested Unicode archives produce stable, sanitized, read-only references."""
        # Seed nested archives with stable bytes and timestamps
        nested = self.backups / "Máin Profile"
        nested.mkdir()
        archive = nested / "2026-09-26 snapshot.zip"
        archive.write_bytes(b"archive bytes")
        other = nested / "notes.bin"
        other.write_bytes(b"opaque")
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (archive, other)
        }
        # Repeated inspections must not disturb the source tree
        first, second = self.inventory(), self.inventory()
        self.assertEqual(first.source_digest, second.source_digest)
        self.assertEqual([item.relative_path for item in first.backups], [
            "Máin Profile/2026-09-26 snapshot.zip", "Máin Profile/notes.bin",
        ])
        self.assertEqual([item.format for item in first.backups], ["ZIP", "UNKNOWN"])
        # Public entries must not expose absolute source paths
        public = json.dumps([item.to_dict() for item in first.backups], ensure_ascii=False)
        self.assertNotIn(str(self.legacy), public)
        self.assertTrue(all(len(item.reference_id) == 64 for item in first.backups))
        for path, evidence in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), evidence)

    def test_revalidation_marks_changed_missing_and_preserves_reference_identity(self) -> None:
        """Revalidation flags changed and missing archives while reference ids stay stable."""
        changed = self.backups / "changed.zip"
        missing = self.backups / "missing.zip"
        changed.write_bytes(b"original")
        missing.write_bytes(b"remove me")
        candidate = self.repository.from_inventory(self.inventory())
        saved = self.repository.save(candidate, None)
        references = {item.relative_path: item.reference_id for item in saved.entries}
        # Mutate one archive and remove the other before revalidation
        changed.write_bytes(b"different")
        missing.unlink()
        result = LegacyBackupService(self.repository).revalidate(saved.revision)
        statuses = {item["relative_path"]: item["status"] for item in result["entries"]}
        self.assertEqual(statuses, {"changed.zip": "CHANGED", "missing.zip": "MISSING"})
        self.assertEqual(
            {item["relative_path"]: item["reference_id"] for item in result["entries"]},
            references,
        )
        self.assertEqual(changed.read_bytes(), b"different")

    def test_moved_root_remains_indexed_and_unavailable(self) -> None:
        """Moving the legacy root keeps references indexed but reports them missing."""
        (self.backups / "one.zip").write_bytes(b"one")
        saved = self.repository.save(self.repository.from_inventory(self.inventory()), None)
        # Relocate the whole backup folder before revalidation
        moved = self.root / "Moved legacy backups"
        self.backups.rename(moved)
        result = LegacyBackupService(self.repository).revalidate(saved.revision)
        self.assertEqual(result["entries"][0]["status"], "MISSING")
        persisted = self.repository.load_optional()
        self.assertEqual(persisted.entries[0].relative_path, "one.zip")
        self.assertEqual(moved.joinpath("one.zip").read_bytes(), b"one")

    def test_unreadable_archive_is_retained_as_diagnostic(self) -> None:
        """An archive that cannot be read is retained with a diagnostic status."""
        archive = self.backups / "restricted.zip"
        archive.write_bytes(b"archive")
        saved = self.repository.save(self.repository.from_inventory(self.inventory()), None)
        with patch(
            "dayz_serverman.application.legacy_backups.sha256_file",
            side_effect=PermissionError("synthetic access denied"),
        ):
            result = LegacyBackupService(self.repository).revalidate(saved.revision)
        self.assertEqual(result["entries"][0]["status"], "UNREADABLE")
        self.assertEqual(archive.read_bytes(), b"archive")

    def test_unicode_normalization_collision_fails_closed_when_host_allows_both(self) -> None:
        """Two archives that differ only by Unicode normalization must fail closed."""
        # Create the composed and decomposed name pair
        first = self.backups / "café.zip"
        second = self.backups / "café.zip"
        first.write_bytes(b"one")
        try:
            second.write_bytes(b"two")
        except OSError:
            self.skipTest("host filesystem does not permit the normalization pair")
        if len(list(self.backups.iterdir())) < 2:
            self.skipTest("host filesystem aliases normalized names")
        with self.assertRaises(LegacySourceError):
            self.inventory()

    def test_unsafe_link_and_normalized_collision_fail_closed(self) -> None:
        """A backup entry that is a link must fail closed during inspection."""
        outside = self.root / "outside.zip"
        outside.write_bytes(b"outside")
        link = self.backups / "linked.zip"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("link creation is unavailable")
        with self.assertRaises(LegacySourceError):
            self.inventory()

    def test_external_root_lexical_classes_fail_before_path_construction(self) -> None:
        """Lexically invalid external roots must be rejected before any path is built."""
        invalid = (
            "relative\\backups", r"\\server\share", r"\\?\C:\backups",
            r"C:\backups:file", r"C:\safe\..\backups", r"C:/not-canonical",
            r"C:\NUL\backups", r"C:\bad*name", "", None,
        )
        for value in invalid:
            with self.subTest(value=value), patch(
                "dayz_serverman.repositories.external_root.Path",
            ) as path_constructor, self.assertRaises(ExternalRootError):
                validate_external_root(value, require_existing=False)
            path_constructor.assert_not_called()

    def test_index_roundtrip_and_semantic_tamper_matrix(self) -> None:
        """Semantic tampering with the saved index must fail closed on load."""
        (self.backups / "a.zip").write_bytes(b"one")
        (self.backups / "b.bin").write_bytes(b"two")
        saved = self.repository.save(self.repository.from_inventory(self.inventory()), None)
        self.assertEqual(self.repository.load_optional(), saved)
        authoritative = json.loads(self.repository.path.read_text(encoding="utf-8"))
        # Every mutated document must be rejected on load
        cases = (
            ("root identity", lambda value: value.update(source_root_identity="0" * 64)),
            ("reference id", lambda value: value["entries"][0].update(reference_id="0" * 64)),
            ("boolean size", lambda value: value["entries"][0].update(size=True)),
            ("uppercase digest", lambda value: value["entries"][0].update(
                sha256=value["entries"][0]["sha256"].upper(),
            )),
            ("derived format", lambda value: value["entries"][0].update(format="7Z")),
            ("closed warning", lambda value: value["entries"][0].update(warnings=["other"])),
            ("unknown field", lambda value: value["entries"][0].update(extra="field")),
            ("timestamp", lambda value: value.update(last_verified_at="2026-01-01T00:00:00+00:00")),
            ("entry timestamp", lambda value: value["entries"][0].update(
                last_verified_at="2026-13-01T00:00:00Z",
            )),
            ("order", lambda value: value["entries"].reverse()),
        )
        for label, mutate in cases:
            document = copy.deepcopy(authoritative)
            mutate(document)
            self.repository.path.write_text(json.dumps(document), encoding="utf-8")
            with self.subTest(label=label), self.assertRaises(LegacyBackupIndexError):
                self.repository.load_optional()
        self.repository.path.write_text(json.dumps(authoritative), encoding="utf-8")
        self.assertEqual(self.repository.load_optional(), saved)

    def test_invalid_persisted_root_is_rejected_without_filesystem_access(self) -> None:
        """A lexically invalid persisted root must be rejected without path construction."""
        (self.backups / "one.zip").write_bytes(b"one")
        self.repository.save(self.repository.from_inventory(self.inventory()), None)
        document = json.loads(self.repository.path.read_text(encoding="utf-8"))
        document["source_root"] = "relative\\backups"
        self.repository.path.write_text(json.dumps(document), encoding="utf-8")
        with patch("dayz_serverman.repositories.external_root.Path") as path_constructor:
            with self.assertRaises(LegacyBackupIndexError):
                self.repository.load_optional()
        path_constructor.assert_not_called()

    def test_persisted_reparse_root_is_rejected_on_load_and_list(self) -> None:
        """A persisted root that is a reparse point must be rejected on load and list."""
        (self.backups / "one.zip").write_bytes(b"one")
        self.repository.save(self.repository.from_inventory(self.inventory()), None)
        with patch(
            "dayz_serverman.repositories.external_root.path_has_reparse",
            return_value=True,
        ):
            with self.assertRaises(LegacyBackupIndexError):
                self.repository.load_optional()
            with self.assertRaises(LegacyBackupIndexError):
                LegacyBackupService(self.repository).list()


if __name__ == "__main__":
    unittest.main()
