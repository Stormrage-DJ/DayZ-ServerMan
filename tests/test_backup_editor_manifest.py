"""Schema 4 manifests: the editor field, the inventory check, older builds and schema 3 compatibility (10.9, D18)."""

from __future__ import annotations

import hashlib
import json
import unittest
import zipfile
from typing import Any
from unittest.mock import patch

from dayz_serverman.domain.backups import (
    BackupManifest, BackupManifestError, RESTORE_COMPATIBLE, RESTORE_PENDING_RUNTIME, restore_compatibility,
)
from dayz_serverman.repositories.backup_archives import (
    BackupArchiveError, FutureBackupArchive, read_archive_manifest, verify_archive,
)
from dayz_serverman.repositories.backups import BackupStorageError
from tests.backup_editor_fixtures import PREVIOUS, VANILLA, EditorBackupFixture, rewrite_archive, sha
from tests.backup_older_build import older_parse_accepts, older_restore_compatibility


ASSOCIATION = "editor/association/"


def entry(path: str, data: bytes) -> dict[str, Any]:
    """Return a manifest entry for bytes."""
    return {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def add(manifest: dict[str, Any], members: dict[str, bytes], path: str, data: bytes) -> None:
    """Add one member and its entry in manifest order."""
    members[path] = data
    manifest["entries"] = sorted([*manifest["entries"], entry(path, data)],
                                 key=lambda item: (item["path"].casefold(), item["path"]))


def drop(manifest: dict[str, Any], members: dict[str, bytes], path: str) -> None:
    """Remove one member and its entry."""
    members.pop(path)
    manifest["entries"] = [item for item in manifest["entries"] if item["path"] != path]


class EditorManifestTests(EditorBackupFixture):
    """Each broken inventory or field makes the archive invalid; older builds refuse schema 4 twice."""

    def setUp(self) -> None:
        """Create one schema 4 backup of the full fixture."""
        super().setUp()
        self.write_all()
        self.path = self.archive(self.create())

    def assertInvalid(self, change) -> None:
        """Rewrite the archive with a change and require that reading or verification refuses it."""
        rewrite_archive(self.path, change)
        with self.assertRaises((BackupManifestError, BackupArchiveError)):
            verify_archive(self.path, read_archive_manifest(self.path))
        with self.assertRaises(BackupStorageError) as caught:
            self.storage.verified_manifest(self.backups, self.path.stem, "main")
        self.assertEqual(caught.exception.code, "BACKUP_INTEGRITY_FAILED")

    def test_the_rewrite_helper_keeps_a_valid_archive_valid(self) -> None:
        rewrite_archive(self.path, lambda manifest, members: None)
        verify_archive(self.path, read_archive_manifest(self.path))

    def test_an_extra_content_entry_is_invalid(self) -> None:
        self.assertInvalid(lambda manifest, members: add(manifest, members, ASSOCIATION + "contents/" + sha(b"x"), b"x"))

    def test_a_missing_content_entry_is_invalid(self) -> None:
        self.assertInvalid(lambda manifest, members: drop(manifest, members, ASSOCIATION + "contents/" + sha(PREVIOUS)))

    def test_a_missing_original_content_is_invalid(self) -> None:
        path = f"editor/originals/{self.mission_key}/contents/{sha(VANILLA)}"
        self.assertInvalid(lambda manifest, members: drop(manifest, members, path))

    def test_an_entry_of_no_listed_record_is_invalid(self) -> None:
        self.assertInvalid(lambda manifest, members: add(manifest, members, ASSOCIATION + "ledger.json", b"{}"))
        self.setUp_again()
        other = f"editor/originals/{'f' * 32}/original.json"
        self.assertInvalid(lambda manifest, members: add(manifest, members, other, b"{}"))

    def test_a_listed_record_without_its_entry_is_invalid(self) -> None:
        self.assertInvalid(lambda manifest, members: drop(manifest, members, ASSOCIATION + "applied.json"))

    def test_a_content_name_must_be_its_digest(self) -> None:
        def rename(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
            """Store the bytes of one content entry under another digest name."""
            data = members[ASSOCIATION + "contents/" + sha(PREVIOUS)]
            drop(manifest, members, ASSOCIATION + "contents/" + sha(PREVIOUS))
            add(manifest, members, ASSOCIATION + "contents/" + "0" * 64, data)
        self.assertInvalid(rename)

    def test_a_field_that_disagrees_with_the_records_is_invalid(self) -> None:
        def field(change):
            """Return a change of the editor field only."""
            return lambda manifest, members: change(manifest["editor"])
        cases = [
            lambda editor: editor["originals"][0].update(display_path="C:\\elsewhere"),
            lambda editor: editor.update(profile_id="other"),
            lambda editor: editor["association"].update(records=["applied", "plan", "baseline"]),
            lambda editor: editor["association"].update(mission_key="b" * 32),
            lambda editor: editor.update(version=2),
            lambda editor: editor.update(extra=True),
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertInvalid(field(case))
                self.setUp_again()

    def test_a_record_of_another_association_is_invalid(self) -> None:
        def other(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
            """Replace the applied record by one that names another profile."""
            self.write_records("second")
            data = (self.area / "profiles" / "second" / self.mission_key / "applied.json").read_bytes()
            drop(manifest, members, ASSOCIATION + "applied.json")
            add(manifest, members, ASSOCIATION + "applied.json", data)
        self.assertInvalid(other)

    def test_the_manifest_digest_covers_the_editor_field(self) -> None:
        def unsigned(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
            """Change the field after signing: the digest no longer matches."""
            manifest["editor"]["originals"][0]["display_path"] = "C:\\elsewhere"
        signed = read_archive_manifest(self.path).to_dict()
        unsigned(signed, {})
        changed = BackupManifest.parse(signed)
        self.assertNotEqual(changed.manifest_digest, changed.signed().manifest_digest)
        with zipfile.ZipFile(self.path) as source:
            members = {info.filename: source.read(info) for info in source.infolist()}
        members["manifest.json"] = json.dumps(signed).encode("utf-8")
        self.path.unlink()
        with zipfile.ZipFile(self.path, "x") as target:
            for name, data in members.items():
                target.writestr(name, data)
        with self.assertRaises(BackupManifestError):
            read_archive_manifest(self.path)

    def test_editor_entries_need_schema_4(self) -> None:
        def schema_3(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
            """Keep the editor entries but declare schema 3 without the editor field."""
            manifest["schema_version"] = 3
            manifest.pop("editor")
        self.assertInvalid(schema_3)
        self.setUp_again()
        # Also an editor prefix in another letter case
        def upper(manifest: dict[str, Any], members: dict[str, bytes]) -> None:
            """Declare schema 3 and add one entry below Editor/."""
            schema_3(manifest, members)
            for item in list(manifest["entries"]):
                if item["path"].startswith("editor/"):
                    drop(manifest, members, item["path"])
            add(manifest, members, "Editor/notes.txt", b"x")
        self.assertInvalid(upper)

    def test_older_builds_refuse_schema_4_twice(self) -> None:
        manifest = read_archive_manifest(self.path)
        # An older build reads at most schema 3, so its archive reader reports a future schema
        with patch("dayz_serverman.repositories.backup_archives.BACKUP_SCHEMA_VERSION", 3):
            with self.assertRaises(FutureBackupArchive):
                read_archive_manifest(self.path)
        raw = json.loads(json.dumps(manifest.to_dict()))
        self.assertEqual(raw["schema_version"], 4)
        # The frozen older checks (tests/backup_older_build.py): the parse refuses schema 4, and the restore mapper
        # knows only the payload trees, so the editor prefix is pending
        self.assertFalse(older_parse_accepts(raw))
        self.assertEqual(older_restore_compatibility(manifest),
                         (RESTORE_PENDING_RUNTIME,
                          "This backup contains a target form that the current restore mapper does not support."))
        # This build restores schema 4 (10.10)
        self.assertEqual(restore_compatibility(manifest)[0], RESTORE_COMPATIBLE)
        # A newer schema than 4 is a future schema for this build
        rewrite_archive(self.path, lambda manifest, members: manifest.update(schema_version=5))
        with self.assertRaises(FutureBackupArchive):
            read_archive_manifest(self.path)

    def test_schema_3_archives_keep_their_compatibility(self) -> None:
        service = self.backup_service()
        manifest = self.storage.verified_manifest(self.backups, self.create(service)["backup_id"], "main")
        self.assertEqual(manifest.schema_version, 3)
        self.assertEqual(restore_compatibility(manifest)[0], "COMPATIBLE")
        self.assertEqual(BackupManifest.parse(manifest.to_dict()), manifest)
        self.assertEqual(set(manifest.to_dict()), {
            "schema_version", "backup_id", "profile_id", "profile_revision", "settings_revision", "created_at",
            "entries", "content_digest_algorithm", "manifest_digest", "semantic_profile_digest", "runtime_profile",
            "reconstruction", "directories"})

    def setUp_again(self) -> None:
        """Start a new archive after a destructive rewrite."""
        self.path.unlink()
        self.path = self.archive(self.create())


if __name__ == "__main__":
    unittest.main()
