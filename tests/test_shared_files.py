"""Contract tests of the A12 shared-file module: opener, replace, folder rename and atomic write."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_serverman.adapters.windows import shared_files
from dayz_serverman.adapters.windows.shared_files import (
    open_shared,
    read_bytes_shared,
    read_text_shared,
    rename_directory,
    replace_file,
    write_bytes_atomically,
)


# The Windows-only checks need the sharing modes of CreateFileW
WINDOWS = os.name == "nt"


def windows_error(code: int) -> OSError:
    """Build the OSError that Windows raises for one error code."""
    return OSError(0, "injected", None, code)


class SharedFilesTestCase(unittest.TestCase):
    """Give each test a disposable folder."""

    def setUp(self) -> None:
        """Create the temporary folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_shared_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)


class OpenerTests(SharedFilesTestCase):
    """open_shared and its two read helpers behave as Path.open, read_bytes and read_text."""

    def test_opener_reads_bytes_and_text(self) -> None:
        """Binary and text modes return the file content; text decodes and translates newlines."""
        path = self.root / "record.json"
        path.write_bytes("﻿{\"name\": \"árvíz\"}\r\n".encode("utf-8"))
        with open_shared(path) as stream:
            self.assertEqual(stream.read(), path.read_bytes())
        self.assertEqual(read_bytes_shared(path), path.read_bytes())
        self.assertEqual(read_text_shared(path, "utf-8-sig"), path.read_text(encoding="utf-8-sig"))
        self.assertEqual(json.loads(read_text_shared(path, encoding="utf-8-sig")), {"name": "árvíz"})
        # A text stream with an explicit encoding reads as Path.open would
        with open_shared(path, "r", encoding="utf-8") as stream:
            self.assertEqual(stream.read(), path.read_text(encoding="utf-8"))

    def test_text_errors_argument_is_applied(self) -> None:
        """An invalid byte follows the errors argument as Path.read_text does."""
        path = self.root / "server.cfg"
        path.write_bytes(b"template = \xff;\n")
        self.assertEqual(read_text_shared(path, "utf-8", errors="replace"), path.read_text("utf-8", "replace"))
        with self.assertRaises(UnicodeDecodeError):
            read_text_shared(path, "utf-8")

    def test_missing_file_raises_file_not_found(self) -> None:
        """A missing file, or a missing folder on the way, raises FileNotFoundError as today."""
        for path in (self.root / "missing.json", self.root / "missing" / "record.json"):
            with self.subTest(path=path.name), self.assertRaises(FileNotFoundError):
                open_shared(path)
        with self.assertRaises(FileNotFoundError):
            read_bytes_shared(self.root / "missing.json")

    def test_a_folder_raises_permission_error(self) -> None:
        """Opening a folder for reading raises PermissionError, as the builtin opener does on Windows."""
        if not WINDOWS:
            self.skipTest("Windows error mapping")
        with self.assertRaises(PermissionError):
            open_shared(self.root)

    def test_write_modes_are_refused(self) -> None:
        """The opener is for reads only."""
        with self.assertRaises(ValueError):
            open_shared(self.root / "record.json", "wb")


@unittest.skipUnless(WINDOWS, "POSIX-semantics replace needs Windows")
class ReplaceTests(SharedFilesTestCase):
    """replace_file replaces under open shared readers and falls back or retries as designed."""

    def stage(self, name: str, payload: bytes) -> Path:
        """Write one closed staging file."""
        path = self.root / name
        path.write_bytes(payload)
        return path

    def test_replace_keeps_an_open_shared_reader_on_the_old_bytes(self) -> None:
        """The target gets the new bytes; a reader opened before keeps reading the old bytes to the end."""
        target = self.stage("record.json", b"old" * 1000)
        with open_shared(target) as reader:
            first = reader.read(10)
            replace_file(self.stage("record.tmp", b"new" * 1000), target)
            rest = reader.read()
        self.assertEqual(first + rest, b"old" * 1000)
        self.assertEqual(target.read_bytes(), b"new" * 1000)
        self.assertFalse((self.root / "record.tmp").exists())

    def test_replace_creates_a_missing_target(self) -> None:
        """A replace onto a new name works as os.replace does."""
        replace_file(self.stage("record.tmp", b"new"), self.root / "record.json")
        self.assertEqual((self.root / "record.json").read_bytes(), b"new")

    def test_missing_source_raises_file_not_found(self) -> None:
        """A missing source raises FileNotFoundError without retries."""
        with patch.object(shared_files, "sleep") as sleep, self.assertRaises(FileNotFoundError):
            replace_file(self.root / "missing.tmp", self.root / "record.json")
        sleep.assert_not_called()

    def test_posix_refusal_falls_back_to_os_replace(self) -> None:
        """Each Windows error that means 'POSIX semantics refused' uses os.replace."""
        for code in sorted(shared_files.POSIX_REFUSED):
            with self.subTest(winerror=code):
                target = self.stage("record.json", b"old")
                source = self.stage("record.tmp", b"new")
                with patch.object(shared_files, "_posix_replace", side_effect=windows_error(code)) as posix:
                    replace_file(source, target)
                posix.assert_called_once()
                self.assertEqual(target.read_bytes(), b"new")
                self.assertFalse(source.exists())

    def test_sharing_errors_are_retried_with_the_delays_then_raised(self) -> None:
        """Winerror 32 is tried six times with waits of 10, 20, 40, 80 and 160 ms, then raised."""
        target = self.stage("record.json", b"old")
        source = self.stage("record.tmp", b"new")
        with patch.object(shared_files, "_posix_replace", side_effect=windows_error(32)) as posix, \
                patch.object(shared_files, "sleep") as sleep:
            with self.assertRaises(PermissionError) as raised:
                replace_file(source, target)
        self.assertEqual(raised.exception.winerror, 32)
        self.assertEqual(posix.call_count, 6)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.01, 0.02, 0.04, 0.08, 0.16])
        self.assertEqual(target.read_bytes(), b"old")

    def test_a_retry_that_succeeds_returns_normally(self) -> None:
        """Two access refusals, then a success, end with the new bytes."""
        target = self.stage("record.json", b"old")
        source = self.stage("record.tmp", b"new")
        real = shared_files._posix_replace
        outcomes = [windows_error(5), windows_error(5)]

        def refused_twice(source_name: str, target_name: str) -> None:
            """Refuse the first two tries the way a plain reader handle does."""
            if outcomes:
                raise outcomes.pop(0)
            real(source_name, target_name)

        with patch.object(shared_files, "_posix_replace", refused_twice), patch.object(shared_files, "sleep") as sleep:
            replace_file(source, target)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(target.read_bytes(), b"new")

    def test_fallback_path_also_retries_sharing_errors(self) -> None:
        """After a POSIX refusal the os.replace path retries a sharing violation of a plain reader."""
        target = self.stage("record.json", b"old")
        source = self.stage("record.tmp", b"new")
        reader = open(target, "rb")
        # Close the plain reader after the second wait so a later fallback try succeeds
        waits: list[float] = []

        def wait(delay: float) -> None:
            """Record the wait and release the reader on the second one."""
            waits.append(delay)
            if len(waits) == 2:
                reader.close()

        with patch.object(shared_files, "_posix_replace", side_effect=windows_error(50)) as posix, \
                patch.object(shared_files, "sleep", wait):
            replace_file(source, target)
        posix.assert_called_once()
        self.assertEqual(waits, [0.01, 0.02])
        self.assertEqual(target.read_bytes(), b"new")

    def test_other_errors_are_raised_at_once(self) -> None:
        """An error outside both sets is raised without a fallback or a wait."""
        target = self.stage("record.json", b"old")
        source = self.stage("record.tmp", b"new")
        with patch.object(shared_files, "_posix_replace", side_effect=windows_error(17)), \
                patch.object(shared_files, "sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                replace_file(source, target)
        self.assertEqual(raised.exception.winerror, 17)
        sleep.assert_not_called()
        self.assertTrue(source.exists())


@unittest.skipUnless(WINDOWS, "folder handles block renames on Windows")
class RenameDirectoryTests(SharedFilesTestCase):
    """rename_directory keeps the os.rename meaning and retries a held handle for one second."""

    def make_folder(self) -> Path:
        """Create a folder with one file inside."""
        folder = self.root / "stage"
        folder.mkdir()
        (folder / "file.txt").write_bytes(b"content")
        return folder

    def test_rename_moves_the_folder(self) -> None:
        """A free folder is renamed at once."""
        folder = self.make_folder()
        rename_directory(folder, self.root / "target")
        self.assertEqual((self.root / "target" / "file.txt").read_bytes(), b"content")
        self.assertFalse(folder.exists())

    def test_a_held_handle_is_retried_for_one_second_then_raised(self) -> None:
        """A handle inside the folder makes each try fail with winerror 5; after 1 s the last error is raised."""
        folder = self.make_folder()
        with open_shared(folder / "file.txt"):
            started = time.monotonic()
            with self.assertRaises(PermissionError) as raised:
                rename_directory(folder, self.root / "target")
            elapsed = time.monotonic() - started
        self.assertEqual(raised.exception.winerror, 5)
        self.assertGreaterEqual(elapsed, shared_files.RENAME_RETRY_LIMIT)
        self.assertLess(elapsed, shared_files.RENAME_RETRY_LIMIT + 1.0)
        # A failed try changed nothing
        self.assertTrue((folder / "file.txt").is_file())
        self.assertFalse((self.root / "target").exists())

    def test_a_handle_closed_during_the_retries_lets_the_rename_succeed(self) -> None:
        """A reader that leaves within the bound does not fail the rename."""
        folder = self.make_folder()
        reader = open_shared(folder / "file.txt")
        timer = threading.Timer(0.2, reader.close)
        timer.start()
        self.addCleanup(timer.cancel)
        rename_directory(folder, self.root / "target")
        self.assertTrue((self.root / "target" / "file.txt").is_file())

    def test_an_existing_target_is_not_retried(self) -> None:
        """An existing target raises FileExistsError at once, an OSError as every caller catches."""
        folder = self.make_folder()
        (self.root / "target").mkdir()
        with patch.object(shared_files, "sleep") as sleep, self.assertRaises(FileExistsError):
            rename_directory(folder, self.root / "target")
        sleep.assert_not_called()


class AtomicWriteTests(SharedFilesTestCase):
    """write_bytes_atomically publishes whole files and leaves no staging file."""

    def test_write_creates_folders_and_leaves_no_temporary_file(self) -> None:
        """The payload lands in the target and the folder holds no other file."""
        path = self.root / "journals" / "record.json"
        write_bytes_atomically(path, b"first")
        write_bytes_atomically(path, b"second", ".migration.tmp")
        self.assertEqual(path.read_bytes(), b"second")
        self.assertEqual([entry.name for entry in path.parent.iterdir()], ["record.json"])

    def test_a_failed_replace_removes_the_staging_file_and_keeps_the_target(self) -> None:
        """The error reaches the caller, the old bytes stay and no staging file is left."""
        path = self.root / "record.json"
        path.write_bytes(b"old")
        with patch.object(shared_files, "replace_file", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                write_bytes_atomically(path, b"new")
        self.assertEqual(path.read_bytes(), b"old")
        self.assertEqual([entry.name for entry in self.root.iterdir()], ["record.json"])


if __name__ == "__main__":
    unittest.main()
