"""Installed build: manifest discovery, the field allowlist, flags, branch and ownership signals."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.server_build_fixtures import OWNER_MARKER, force_install, library_install, manifest, vdf
except ModuleNotFoundError:
    from server_build_fixtures import OWNER_MARKER, force_install, library_install, manifest, vdf

from dayz_serverman.domain.server_build import Ownership, ownership_class
from dayz_serverman.repositories import server_build_manifest
from dayz_serverman.repositories.server_build_manifest import MAX_MANIFEST_BYTES, read_installed


class ManifestReadTests(unittest.TestCase):
    """Detailed design 14.2 in temporary trees; no live installation is read."""

    def setUp(self) -> None:
        """Create the temporary root."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        """Remove the temporary root."""
        self.temporary.cleanup()

    def read(self, text: str | None = None, **overrides):
        """Read a library installation with the given manifest text or field overrides."""
        dayz = library_install(self.root, text if text is not None else manifest(**overrides))
        return read_installed(str(dayz), str(self.root / "SteamCMD"))

    def test_library_layout_reads_the_allowlisted_fields_only(self) -> None:
        """The evidence 6.1 shape gives the build, the public branch and a Steam client class."""
        read = self.read()
        self.assertIsNone(read.unknown_reason)
        self.assertEqual((read.build.build_id, read.build.target_build_id, read.build.state_flags,
                          read.build.branch, read.build.branch_change), (24570360, 24570360, 4, "public", False))
        self.assertEqual(read.signals.layout, "B")
        self.assertTrue(read.signals.steam_exe and read.signals.launcher_steam)
        self.assertIs(ownership_class(read), Ownership.STEAM_CLIENT)
        # The owner marker is in the file, but in no value that the reader returns
        self.assertNotIn(OWNER_MARKER, repr(read))

    def test_force_install_layout(self) -> None:
        """Candidate A needs no installdir match and gives the SteamCMD class."""
        dayz = force_install(self.root, manifest(installdir="anything", LauncherPath="C:\\x\\steamcmd.exe"))
        read = read_installed(str(dayz), None)
        self.assertEqual((read.unknown_reason, read.signals.layout), (None, "A"))
        self.assertIs(ownership_class(read), Ownership.STEAMCMD)

    def test_discovery_rows(self) -> None:
        """Unset folder, no record, both records, a mismatched installdir, a folder not below common."""
        self.assertEqual(read_installed(None, None).unknown_reason, "NO_DAYZ_FOLDER")
        self.assertEqual(read_installed("", None).unknown_reason, "NO_DAYZ_FOLDER")
        dayz = library_install(self.root, None)
        self.assertEqual(read_installed(str(dayz), None).unknown_reason, "NO_MANIFEST")
        (dayz.parent.parent / "appmanifest_223350.acf").write_text(manifest(installdir="Other"), encoding="utf-8")
        self.assertEqual(read_installed(str(dayz), None).unknown_reason, "NO_MANIFEST")
        (dayz.parent.parent / "appmanifest_223350.acf").write_text(manifest(installdir="dayzserver"), encoding="utf-8")
        self.assertIsNone(read_installed(str(dayz), None).unknown_reason)
        (dayz / "steamapps").mkdir()
        (dayz / "steamapps" / "appmanifest_223350.acf").write_text(manifest(), encoding="utf-8")
        self.assertEqual(read_installed(str(dayz), None).unknown_reason, "MANIFEST_AMBIGUOUS")
        loose = self.root / "loose" / "steamapps" / "games" / "DayZServer"
        loose.mkdir(parents=True)
        (loose.parent.parent / "appmanifest_223350.acf").write_text(manifest(), encoding="utf-8")
        self.assertEqual(read_installed(str(loose), None).unknown_reason, "NO_MANIFEST")

    def test_size_cap(self) -> None:
        """A manifest of exactly 64 KiB is read; one byte more is unreadable."""
        dayz = library_install(self.root, None)
        target = dayz.parent.parent / "appmanifest_223350.acf"
        raw = manifest().encode("utf-8")
        target.write_bytes(raw + b" " * (MAX_MANIFEST_BYTES - len(raw)))
        self.assertIsNone(read_installed(str(dayz), None).unknown_reason)
        target.write_bytes(raw + b" " * (MAX_MANIFEST_BYTES + 1 - len(raw)))
        self.assertEqual(read_installed(str(dayz), None).unknown_reason, "MANIFEST_UNREADABLE")

    def test_encoding_and_format_errors(self) -> None:
        """A byte order mark is accepted; bad UTF-8, VDF errors and a wrong app id are unreadable."""
        dayz = library_install(self.root, None)
        target = dayz.parent.parent / "appmanifest_223350.acf"
        target.write_bytes(b"\xef\xbb\xbf" + manifest().encode("utf-8"))
        self.assertIsNone(read_installed(str(dayz), None).unknown_reason)
        for raw in (b"\"AppState\" { \"appid\" \"\xff\" }", manifest().replace('"buildid"', '"BUILDID" "1" "buildid"', 1).encode(),
                    b"AppState { }", manifest(appid="221100").encode(), manifest(buildid={"x": "1"}).encode()):
            with self.subTest(raw=raw[:40]):
                target.write_bytes(raw)
                read = read_installed(str(dayz), None)
                self.assertEqual(read.unknown_reason, "MANIFEST_UNREADABLE")
                self.assertNotIn(OWNER_MARKER, repr(read))

    def test_build_id_bounds_and_target(self) -> None:
        """Build ids from 1 to 4,294,967,295; an absent or zero target is no target."""
        for value, reason in (("0", "MANIFEST_UNREADABLE"), ("1", None), ("4294967295", None),
                              ("4294967296", "MANIFEST_UNREADABLE"), ("12a", "MANIFEST_UNREADABLE")):
            with self.subTest(value=value):
                self.temporary.cleanup()
                self.setUp()
                self.assertEqual(self.read(buildid=value, TargetBuildID=None).unknown_reason, reason)
        for target, expected in ((None, None), ("0", None), ("24570360", 24570360), ("24600000", 24600000)):
            with self.subTest(target=target):
                self.temporary.cleanup()
                self.setUp()
                self.assertEqual(self.read(TargetBuildID=target).build.target_build_id, expected)

    def test_flags(self) -> None:
        """Not fully installed and no pending reason: unknown; a pending reason keeps the build."""
        for flags, reason in (("0", "NOT_INSTALLED"), ("1", "NOT_INSTALLED"), ("6", None), ("1024", None),
                              ("68", None), ("x", "MANIFEST_UNREADABLE")):
            with self.subTest(flags=flags):
                self.temporary.cleanup()
                self.setUp()
                self.assertEqual(self.read(StateFlags=flags).unknown_reason, reason)

    def test_branch_rules(self) -> None:
        """MountedConfig before UserConfig; absent or empty is public; different keys are a branch change."""
        cases = [
            ({"UserConfig": None, "MountedConfig": None}, ("public", False)),
            ({"MountedConfig": {"betakey": ""}}, ("public", False)),
            ({"UserConfig": {"betakey": "experimental_public"}, "MountedConfig": None}, ("experimental_public", False)),
            ({"UserConfig": {"betakey": "beta"}, "MountedConfig": {"betakey": "beta"}}, ("beta", False)),
            ({"UserConfig": {"betakey": "beta"}, "MountedConfig": {"language": "english"}}, ("public", True)),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                self.temporary.cleanup()
                self.setUp()
                read = self.read(**overrides)
                self.assertEqual((read.build.branch, read.build.branch_change), expected)
        self.temporary.cleanup()
        self.setUp()
        self.assertEqual(self.read(UserConfig={"betakey": "bad key"}).unknown_reason, "MANIFEST_UNREADABLE")

    def test_links_and_reparse_points_do_not_count(self) -> None:
        """A manifest that is a reparse point is not a manifest of this folder."""
        dayz = library_install(self.root, manifest())
        real = os.lstat

        class Reparse:
            """A regular-file stat result that carries the reparse attribute."""
            st_mode = stat.S_IFREG
            st_size = 10
            st_file_attributes = 0x400

        with patch.object(server_build_manifest.os, "lstat",
                          side_effect=lambda path: Reparse() if str(path).endswith(".acf") else real(path)):
            self.assertEqual(read_installed(str(dayz), None).unknown_reason, "NO_MANIFEST")
        # A symbolic link is not a regular file either
        link = dayz.parent.parent / "appmanifest_223350.acf"
        outside = self.root / "outside.acf"
        outside.write_text(manifest(), encoding="utf-8")
        link.unlink()
        try:
            link.symlink_to(outside)
        except OSError:
            return
        self.assertEqual(read_installed(str(dayz), None).unknown_reason, "NO_MANIFEST")

    def test_ownership_signals_of_the_library(self) -> None:
        """The configured SteamCMD root, a secondary-library marker and launcher names are seen."""
        dayz = library_install(self.root, manifest(LauncherPath="C:\\SteamCMD\\steamcmd.exe"), steam_exe=False)
        library = dayz.parent.parent.parent
        read = read_installed(str(dayz), str(library))
        self.assertTrue(read.signals.steamcmd_root and read.signals.launcher_steamcmd)
        self.assertIs(ownership_class(read), Ownership.STEAMCMD)
        (library / "libraryfolder.vdf").write_text(vdf({"libraryfolder": {
            "contentid": "1", "launcher": "E:\\Steam\\steam.exe"}}), encoding="utf-8")
        read = read_installed(str(dayz), None)
        self.assertTrue(read.signals.client_library)
        # A Steam client marker beside a SteamCMD launcher is a conflict
        self.assertIs(ownership_class(read), Ownership.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
