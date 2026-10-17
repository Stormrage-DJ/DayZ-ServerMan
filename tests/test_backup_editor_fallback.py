"""A corrupt editor record does not refuse the profile backup: it is made at schema 3 and says so (10.9).

Product Owner decision of 2026-10-16 16:47:54 in evidence 10.9.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_serverman.application.backups import EDITOR_RECORDS_OMITTED, editor_records_notice
from dayz_serverman.repositories import backup_editor_records
from dayz_serverman.repositories.backups import BackupStorageError
from tests.backup_editor_fixtures import BANDITS, PREVIOUS, EditorBackupFixture, sha


# The Product Owner text, word for word
TEXT = ("The backup was created without the map editor records of this profile, because they are not valid. "
        "A restore of this backup does not bring them back.")


class CorruptEditorRecordTests(EditorBackupFixture):
    """Each corrupt record kind and content gives a schema 3 backup with the notice; a read failure refuses."""

    def corrupt_files(self) -> dict[str, tuple[Path, bytes | None]]:
        """Return each damage case: the file and its new bytes, None to remove it."""
        association = self.association_folder()
        mission, runtime = self.area / "originals" / self.mission_key, self.area / "originals" / self.runtime_key
        return {
            "plan": (association / "plan.json", b"{not json"),
            "plan document": (association / "plan.json", b'{"schema_version": 1, "revision": 0, "plan": {}}'),
            "baseline": (association / "baseline.json", b"{not json"),
            "applied": (association / "applied.json", b'{"schema_version": 1, "revision": 0}'),
            "mission original": (mission / "original.json", b"[]"),
            "runtime original": (runtime / "original.json", b"{not json"),
            "association content": (association / "files" / sha(PREVIOUS), b"changed"),
            "original content": (runtime / "files" / sha(BANDITS), None),
        }

    def test_each_corrupt_record_kind_gives_schema_3_with_the_notice(self) -> None:
        self.write_all()
        for name, (path, data) in self.corrupt_files().items():
            with self.subTest(case=name):
                saved = path.read_bytes()
                if data is None:
                    path.unlink()
                else:
                    path.write_bytes(data)
                result = self.create()
                manifest = self.storage.verified_manifest(self.backups, result["backup_id"], "main")
                self.assertEqual(manifest.schema_version, 3)
                self.assertIsNone(manifest.editor)
                self.assertFalse(any(entry.path.startswith("editor/") for entry in manifest.entries))
                self.assertIs(result["editor_records_omitted"], True)
                self.assertEqual(result["restore_compatibility"], "COMPATIBLE")
                # The damaged record stays as it was; the backup changes no editor file
                self.assertEqual(path.exists(), data is not None)
                path.write_bytes(saved)
                (self.backups / f"{result['backup_id']}.zip").unlink()
        # With every record repaired, the next backup carries them again
        result = self.create()
        self.assertIs(result["editor_records_omitted"], False)
        self.assertEqual(self.storage.verified_manifest(self.backups, result["backup_id"], "main").schema_version, 4)

    def test_valid_records_and_no_records_give_no_notice(self) -> None:
        self.assertIs(self.create()["editor_records_omitted"], False)
        self.assertIs(self.create(self.backup_service())["editor_records_omitted"], False)

    def test_a_read_failure_still_refuses(self) -> None:
        self.write_all()
        real = backup_editor_records.read_bytes_shared

        def denied(path: Path) -> bytes:
            """Deny the read of the baseline only."""
            if path.name == "baseline.json":
                raise PermissionError("denied")
            return real(path)

        with patch.object(backup_editor_records, "read_bytes_shared", denied):
            with self.assertRaises(BackupStorageError) as caught:
                self.create()
        self.assertEqual(caught.exception.code, "BACKUP_SOURCE_INVALID")
        self.assertEqual(list(self.backups.glob("*.zip")), [])

    def test_a_plan_that_changes_during_the_read_still_refuses(self) -> None:
        self.write_all()
        reads = []
        real = backup_editor_records.read_bytes_shared

        def changing(path: Path) -> bytes:
            """Return other bytes at the second read of the plan."""
            data = real(path)
            if path.name == "plan.json":
                reads.append(path)
                return data + b" " * (len(reads) - 1)
            return data

        with patch.object(backup_editor_records, "read_bytes_shared", changing):
            with self.assertRaises(BackupStorageError) as caught:
                self.create()
        self.assertEqual(caught.exception.code, "BACKUP_SOURCE_CHANGED")


class NoticeTests(unittest.TestCase):
    """The notice follows the result of a create and the backup result of a stop or restart with backup."""

    def test_the_text_is_the_product_owner_text(self) -> None:
        self.assertEqual(EDITOR_RECORDS_OMITTED, TEXT)

    def test_the_notice_follows_a_backup_that_left_records_out(self) -> None:
        omitted, kept = {"editor_records_omitted": True}, {"editor_records_omitted": False}
        cases = {
            "backup create": (omitted, TEXT),
            "stop with backup": ({"backup": omitted}, TEXT),
            "backup with records": (kept, None),
            "stop without backup": ({"backup": None}, None),
            "a text instead of true": ({"editor_records_omitted": "yes"}, None),
            "no result": (None, None),
        }
        for name, (result, expected) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(editor_records_notice(result), expected)


if __name__ == "__main__":
    unittest.main()
