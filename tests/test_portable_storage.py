"""Portable layout and versioned JSON storage tests for manager-owned data."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import (  # noqa: E402
    RecordState,
    RecordUnavailable,
    RevisionConflict,
)
from dayz_serverman.repositories.json_store import VersionedJsonRepository  # noqa: E402
from dayz_serverman.repositories.paths import (  # noqa: E402
    PortablePaths,
    normalize_external_path,
    normalize_manager_relative,
)


class PortablePathsTests(unittest.TestCase):
    """Contract: manager paths derive from an explicit root, not the working directory."""
    def test_explicit_root_is_independent_of_working_directory(self) -> None:
        """Resolve an explicit root independently of the current working directory."""
        with tempfile.TemporaryDirectory(prefix="serverman_root_") as root_text:
            with tempfile.TemporaryDirectory(prefix="serverman_cwd_") as cwd_text:
                previous = Path.cwd()
                # Build the composition from a different working directory
                try:
                    os.chdir(cwd_text)
                    composition = build_composition(Path(root_text) / "Manager Root")
                finally:
                    os.chdir(previous)
            # The resolved root must not depend on the captured cwd
            self.assertEqual(composition.paths.root, (Path(root_text) / "Manager Root").resolve())
            self.assertFalse(str(composition.paths.root).startswith(cwd_text))

    def test_source_layout_resolves_project_root_without_cwd(self) -> None:
        """Resolve the project root from a source anchor without the cwd."""
        # Anchor the layout at the composition module inside the source tree
        anchor = PROJECT_ROOT / "src" / "dayz_serverman" / "composition.py"
        paths = PortablePaths.from_source(anchor)
        self.assertEqual(paths.root, PROJECT_ROOT.resolve())

    def test_layout_contains_every_manager_owned_directory(self) -> None:
        """Create every manager-owned directory under a relocated root."""
        # Provision the layout under a unicode root path
        with tempfile.TemporaryDirectory(prefix="Server Man ünicode ") as root_text:
            paths = PortablePaths.from_root(Path(root_text) / "Moved Manager")
            paths.create_layout()
            expected = (
                paths.config,
                paths.profiles,
                paths.logs,
                paths.operations,
                paths.migrations,
                paths.webview2,
                paths.backups,
                paths.backup_recovery,
                paths.backup_originals,
                paths.docs,
            )
            # Every declared directory exists and relative reporting is stable
            self.assertTrue(all(path.is_dir() for path in expected))
            self.assertEqual(paths.relative(paths.backups), "backups")
            self.assertEqual(paths.ui_preferences.parent, paths.data)
            self.assertEqual(paths.schedules.parent, paths.data)

    def test_relocation_rebuilds_all_paths_from_new_root(self) -> None:
        """Rebuild all derived paths from a new root after relocation."""
        # Build two layouts from different roots
        first = PortablePaths.from_root(Path(r"D:\Alpha Manager"))
        second = PortablePaths.from_root(Path(r"E:\Moved ü Manager"))
        # Relative layout stays identical while absolute roots differ
        self.assertEqual(first.relative(first.profiles), second.relative(second.profiles))
        self.assertNotEqual(first.profiles, second.profiles)

    def test_external_path_must_be_absolute_and_is_normalized(self) -> None:
        """Normalize external paths and reject relative ones."""
        # A relative external path must be rejected
        with self.assertRaises(ValueError):
            normalize_external_path("relative/server")
        # An absolute path is normalized without traversal parts
        normalized = normalize_external_path(r"D:\Servers\DayZ\..\DayZ")
        self.assertTrue(Path(normalized).is_absolute())
        self.assertNotIn("..", Path(normalized).parts)

    def test_manager_relative_path_rejects_escape(self) -> None:
        """Reject manager-relative escapes and normalize separators."""
        # Windows separators normalize to manager-relative form
        self.assertEqual(normalize_manager_relative(r"data\profiles\one.json"), "data/profiles/one.json")
        # A parent-directory escape must be rejected
        with self.assertRaises(ValueError):
            normalize_manager_relative("../legacy/profile.json")


class JsonRepositoryTests(unittest.TestCase):
    """Contract: versioned JSON saves are atomic, reversible, and evidence-preserving."""
    def setUp(self) -> None:
        """Point the repository at a temporary configuration file."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_json_")
        self.path = Path(self.temporary.name) / "config" / "manager.json"
        self.repository = VersionedJsonRepository(self.path)

    def tearDown(self) -> None:
        """Remove the temporary storage."""
        self.temporary.cleanup()

    def test_create_and_update_use_revisions_and_deterministic_utf8(self) -> None:
        """Save with revisions and deterministic UTF-8 field ordering."""
        # Create then update so revisions advance from zero
        created = self.repository.save({"name": "Szerver ő", "enabled": True}, None)
        updated = self.repository.save({"name": "Szerver ő", "enabled": False}, 0)
        self.assertEqual((created.revision, updated.revision), (0, 1))
        # The file keeps deterministic field order and unicode content
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Szerver ő", text)
        self.assertLess(text.index('"enabled"'), text.index('"name"'))
        self.assertEqual(self.repository.load(), updated)

    def test_atomic_replace_uses_same_directory_temporary_file(self) -> None:
        """Stage the atomic replace beside the target on the same directory."""
        # Capture replace calls while the save runs
        real_replace = os.replace
        calls: list[tuple[Path, Path]] = []

        def recording_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
            """Record the replace endpoints, then perform the real replace."""
            calls.append((Path(source), Path(target)))
            real_replace(source, target)

        with patch("dayz_serverman.repositories.json_store.os.replace", recording_replace):
            self.repository.save({"value": 1}, None)
        # The single swap must use a temporary file beside the target
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].parent, calls[0][1].parent)
        self.assertEqual(calls[0][1], self.path.resolve())

    def test_stale_revision_does_not_change_authoritative_record(self) -> None:
        """Refuse a stale revision write and keep the stored record."""
        # A stale revision must be refused without touching the stored value
        self.repository.save({"value": "old"}, None)
        with self.assertRaises(RevisionConflict):
            self.repository.save({"value": "new"}, 99)
        self.assertEqual(self.repository.load().fields["value"], "old")

    def test_corrupt_record_is_classified_and_preserved(self) -> None:
        """Classify a corrupt record and refuse to overwrite it."""
        # Write a corrupt document directly
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken", encoding="utf-8")
        before = self.path.read_bytes()
        # Inspection classifies it and saves refuse to overwrite it
        inspection = self.repository.inspect()
        self.assertEqual(inspection.state, RecordState.CORRUPT)
        with self.assertRaises(RecordUnavailable):
            self.repository.save({"replacement": True}, None)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn(self.path.resolve(), inspection.evidence)

    def test_future_schema_is_classified_and_preserved(self) -> None:
        """Classify a future schema version and refuse destructive writes."""
        # Write a document with a newer schema version
        self.path.parent.mkdir(parents=True)
        original = {"schema_version": 2, "revision": 4, "value": "future"}
        self.path.write_text(json.dumps(original), encoding="utf-8")
        # Inspection classifies it while the original bytes survive
        inspection = self.repository.inspect()
        self.assertEqual(inspection.state, RecordState.FUTURE_SCHEMA)
        with self.assertRaises(RecordUnavailable):
            self.repository.save({"replacement": True}, 4)
        self.assertEqual(json.loads(self.path.read_text()), original)

    def test_interrupted_temporary_file_is_non_destructive_evidence(self) -> None:
        """Treat an interrupted temporary file as evidence and refuse writes."""
        # Plant an interrupted temporary file beside the target
        self.path.parent.mkdir(parents=True)
        temporary = self.path.parent / f".{self.path.name}.interrupted.tmp"
        temporary.write_text('{"schema_version": 1}', encoding="utf-8")
        # Inspection reports the temp file and writes stay blocked
        inspection = self.repository.inspect()
        self.assertEqual(inspection.state, RecordState.INTERRUPTED_WRITE)
        self.assertEqual(inspection.evidence, (temporary.resolve(),))
        with self.assertRaises(RecordUnavailable):
            self.repository.save({"value": "new"}, None)
        self.assertTrue(temporary.exists())

    def test_failed_replace_leaves_temp_evidence_and_old_record(self) -> None:
        """Keep temp evidence and the old record when the replace fails."""
        self.repository.save({"value": "old"}, None)
        # Force the replace to fail after the temporary file is written
        with patch(
            "dayz_serverman.repositories.json_store.os.replace",
            side_effect=OSError("injected replace failure"),
        ):
            with self.assertRaises(OSError):
                self.repository.save({"value": "new"}, 0)
        # Inspection reports the interrupted write with the old record intact
        inspection = self.repository.inspect()
        self.assertEqual(inspection.state, RecordState.INTERRUPTED_WRITE)
        self.assertEqual(inspection.document.fields["value"], "old")
        self.assertEqual(len(inspection.evidence), 1)


if __name__ == "__main__":
    unittest.main()
