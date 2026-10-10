"""Scan test of the A12 choke point: no read, replace, rename or copy outside shared_files."""

from __future__ import annotations

import textwrap
import unittest

from tests.shared_file_scan_support import WHOLE_FILE, AllowEntry, allowlist_problems, scan_package, scan_source


# Reviewed allowlist: file, a text on the line, one A12 reason class (R-1 ruling), reason
ALLOWLIST = (
    AllowEntry("adapters/windows/shared_files.py", WHOLE_FILE, 1, "The choke point"),
    # QF-23: narrowed from whole-file entries to the one open line; server_folder_lock.py opens with CreateFileW
    AllowEntry("adapters/windows/instance_lock.py", 'open(self.path, "a+b")', 1, "A5 lock file; never read"),
    AllowEntry(
        "repositories/backup_archives.py", "archive.open(", 2,
        "A member of an archive that was opened with open_shared",
    ),
    AllowEntry(
        "repositories/legacy_source.py", "read_text(", 3,
        "Reads only the operator's legacy folder, which no manager process replaces or renames; 323 lines",
    ),
    AllowEntry(
        "repositories/legacy_source.py", 'path.open("rb")', 3,
        "Reads only the operator's legacy folder, which no manager process replaces or renames; 323 lines",
    ),
    AllowEntry(
        "repositories/migrations.py", "shutil.copyfile(source, target", 3,
        "Copies a legacy file; the source is in the legacy folder, which no manager process replaces or renames",
    ),
    AllowEntry(
        "repositories/profile_restore_preparation.py", "shutil.copytree(_target, recovery)", 4,
        "Owner-only recovery copy during PREPARING under the installation mutex; the instance lock admits one "
        "owner and observers write nothing, so no other process replaces or renames its source",
    ),
    AllowEntry(
        "repositories/journaled_publication.py", ".replace(", 5,
        "Runs only inside the A13 writer side; the exempt observer calls read only the manager root and the "
        "backup folder; 250 lines",
    ),
)


class SharedFileScanTests(unittest.TestCase):
    """Every file read, replace and rename of the package goes through shared_files."""

    def test_package_has_no_unlisted_site_and_no_stale_entry(self) -> None:
        """The package scan and the reviewed allowlist agree exactly."""
        findings, lines = scan_package()
        self.assertEqual(allowlist_problems(findings, ALLOWLIST, lines), [])

    def test_patterns_are_findings(self) -> None:
        """Each pattern of the design table is reported outside the choke point."""
        source = textwrap.dedent('''
            import os, shutil, zipfile
            def write(path, other):
                os.replace(path, other)
                os.rename(path, other)
                shutil.move(path, other)
                path.replace(other)
                path.rename(other)
                open(path)
                open(path, "r+b")
                path.open(mode)
                path.read_text(encoding="utf-8")
                path.read_bytes()
                zipfile.ZipFile(path)
                shutil.copy2(path, other)
        ''')
        patterns = [finding.pattern for finding in scan_source("repositories/sample.py", source)]
        self.assertEqual(patterns, [
            "os.replace", "os.rename", "shutil.move", ".replace(x)", ".rename(x)", "open", "open",
            ".open", ".read_text", ".read_bytes", "ZipFile", "shutil.copy2",
        ])

    def test_imported_and_aliased_move_names_are_findings(self) -> None:
        """QF-23: a bare imported move or copy name and an aliased os or shutil module are reported."""
        source = textwrap.dedent('''
            import os as system
            import shutil as files
            import os
            from os import replace, getcwd
            from shutil import copyfile as copy_one
            def write(path, other):
                system.replace(path, other)
                replace(path, other)
        ''')
        patterns = [finding.pattern for finding in scan_source("repositories/sample.py", source)]
        self.assertEqual(patterns, ["import os as system", "import shutil as files", "from os import replace",
                                    "from shutil import copyfile"])

    def test_writes_and_string_replaces_are_not_findings(self) -> None:
        """Write modes, str.replace, datetime.replace and a ZipFile over open_shared pass."""
        source = textwrap.dedent('''
            def write(path, text, moment):
                with open(path, "xb") as stream, path.open("a", encoding="utf-8") as log:
                    pass
                text.replace("a", "b")
                moment.replace(tzinfo=None)
                with open_shared(path) as stream, zipfile.ZipFile(stream, "r") as archive:
                    pass
                zipfile.ZipFile(path, "x")
        ''')
        self.assertEqual(scan_source("repositories/sample.py", source), [])

    def test_a_non_asset_read_in_host_is_a_finding(self) -> None:
        """Only the bundled-asset shape of host/assets.py is excluded; other host reads are findings."""
        assets = textwrap.dedent('''
            def compose_shell_html(frontend_root):
                root = frontend_root.resolve(strict=True)
                document = (root / "index.html").read_text(encoding="utf-8")
                scripts = "".join((root / name).read_text(encoding="utf-8") for name in ("a.js", "b.js"))
                other = (frontend_root / "index.html").read_text(encoding="utf-8")
                names = list_names()
                more = "".join((root / name).read_text(encoding="utf-8") for name in names)
                return document
        ''')
        lines = [finding.line for finding in scan_source("host/assets.py", assets)]
        self.assertEqual(lines, [6, 8])
        # The same asset shape in any other host module is a finding
        runtime = [finding.line for finding in scan_source("host/runtime.py", assets)]
        self.assertEqual(runtime, [4, 5, 6, 8])

    def test_a_rebound_root_is_not_excluded(self) -> None:
        """A root name that is bound twice is not the resolved parameter of the rule."""
        assets = textwrap.dedent('''
            def compose_shell_html(frontend_root):
                root = frontend_root.resolve(strict=True)
                root = elsewhere()
                return (root / "index.html").read_text(encoding="utf-8")
        ''')
        self.assertEqual(len(scan_source("host/assets.py", assets)), 1)

    def test_an_entry_without_a_reason_class_or_with_a_stale_line_fails(self) -> None:
        """The allowlist stays reviewed: a missing class and a text no longer on a line are problems."""
        source = "def read(path):\n    return path.read_bytes()\n"
        findings = scan_source("repositories/sample.py", source)
        lines = {"repositories/sample.py": source.splitlines()}
        good = AllowEntry("repositories/sample.py", "path.read_bytes()", 3, "Legacy folder")
        self.assertEqual(allowlist_problems(findings, (good,), lines), [])
        # An entry without an A12 reason class is reported, although it still matches its line
        missing = AllowEntry("repositories/sample.py", "path.read_bytes()", None, "Legacy folder")
        problems = allowlist_problems(findings, (missing,), lines)
        self.assertEqual(len(problems), 1)
        self.assertIn("without an A12 reason class", problems[0])
        # A class outside the five of A12 counts as missing
        outside = AllowEntry("repositories/sample.py", "path.read_bytes()", 6, "Other")
        self.assertIn("without an A12 reason class", allowlist_problems(findings, (outside,), lines)[0])
        # An entry whose text is on no line is stale, and the finding it meant is unlisted
        stale = AllowEntry("repositories/sample.py", "path.read_text()", 3, "Legacy folder")
        problems = allowlist_problems(findings, (stale,), lines)
        self.assertTrue(any(problem.startswith("stale allowlist entry") for problem in problems), problems)
        self.assertTrue(any("repositories/sample.py:2" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
