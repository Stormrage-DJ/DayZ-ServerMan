"""Security tests for reading backup archive manifests."""
from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.domain.backups import BackupManifest, ManifestEntry  # noqa: E402
from dayz_serverman.repositories.backup_archives import (  # noqa: E402
    BackupArchiveError,
    read_archive_manifest,
)


class BackupArchiveSecurityTests(unittest.TestCase):
    """Rejection contracts for unsafe members in backup ZIP archives."""
    def setUp(self) -> None:
        """Create a signed manifest with one payload entry."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_zip_security_")
        self.root = Path(self.temporary.name)
        self.entry = ManifestEntry(
            "payload/serverDZ.cfg", 1,
            "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
        )
        self.manifest = BackupManifest(
            "main_2026-09-28_16-21-51", "main", 3, 4,
            "2026-09-28T14:21:51.000Z", (self.entry,),
            semantic_profile_digest="a" * 64, runtime_profile="profiles\\main",
        ).signed()

    def tearDown(self) -> None:
        """Remove the temporary archive directory."""
        self.temporary.cleanup()

    def archive(self, *extra: tuple[zipfile.ZipInfo | str, bytes]) -> Path:
        """Build a backup ZIP with the manifest and optional extra members."""
        path = self.root / f"{self.manifest.backup_id}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(self.manifest.to_dict()))
            archive.writestr(self.entry.path, b"x")
            for name, content in extra:
                archive.writestr(name, content)
        return path

    def test_standard_zip_manifest_is_readable(self) -> None:
        """A standard ZIP with a signed manifest stays readable."""
        self.assertEqual(read_archive_manifest(self.archive()), self.manifest)

    def test_traversal_member_is_rejected(self) -> None:
        """Members that escape the archive root are rejected."""
        with self.assertRaises(BackupArchiveError):
            read_archive_manifest(self.archive(("../outside.txt", b"bad")))

    def test_symbolic_link_member_is_rejected(self) -> None:
        """Members marked as symbolic links are rejected."""
        link = zipfile.ZipInfo("payload/link")
        # Encode the member as a symbolic link in its external attributes
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(BackupArchiveError):
            read_archive_manifest(self.archive((link, b"target")))

    def test_windows_case_duplicate_is_rejected(self) -> None:
        """Member names that collide under Windows case folding are rejected."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            path = self.archive(("PAYLOAD/serverdz.cfg", b"x"))
        with self.assertRaises(BackupArchiveError):
            read_archive_manifest(path)


if __name__ == "__main__":
    unittest.main()
