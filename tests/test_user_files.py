"""Leaf 9.1: the wording-free user-file steps of `adapters/windows/user_files.py`, in temporary folders."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.adapters.windows import shared_files, user_files  # noqa: E402
from dayz_serverman.adapters.windows.user_files import UserFileError  # noqa: E402

PAYLOAD = '{\n  "name": "Zone ä"\n}\n'.encode("utf-8")


class Folders(unittest.TestCase):
    """A protected root, a free sibling whose name starts with the root name, and a junction into the root."""

    def setUp(self) -> None:
        """Create the folders in a temporary folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_user_files_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root, self.free = self.base / "Manager", self.base / "Manager Two"
        for folder in (self.root / "data", self.free):
            folder.mkdir(parents=True)

    def reason(self, call) -> str:
        """Return the closed reason of one refused call."""
        with self.assertRaises(UserFileError) as raised:
            call()
        return raised.exception.reason


class TargetTests(Folders):
    """`checked_target`: the order of the refusals and the protected roots."""

    def test_a_free_folder_gives_the_target_in_the_resolved_folder(self) -> None:
        """A sibling whose name only starts like the root is free."""
        self.assertEqual(user_files.checked_target(str(self.free / "plan.json"), [self.root], False),
                         self.free / "plan.json")

    def test_the_order_of_the_refusals(self) -> None:
        """A missing folder, a folder or link, a protected root (any case), then an existing file."""
        check = user_files.checked_target
        self.assertEqual(self.reason(lambda: check(str(self.free / "missing" / "x.json"), [], True)), "no_folder")
        self.assertEqual(self.reason(lambda: check(str(self.root / "data"), [], True)), "folder_or_link")
        for typed in (self.root / "x.json", Path(str(self.root / "data").upper()) / "x.json"):
            with self.subTest(path=typed):
                self.assertEqual(self.reason(lambda: check(str(typed), [self.root], True)), "protected")
        existing = self.free / "plan.json"
        existing.write_bytes(b"old")
        self.assertEqual(self.reason(lambda: check(str(existing), [self.root], False)), "exists")
        self.assertEqual(check(str(existing), [self.root], True), existing)

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows feature")
    def test_a_junction_into_a_protected_root_is_protected(self) -> None:
        """The parent folder is resolved first, so a junction cannot bypass the check; a junction target is a link."""
        import _winapi
        link = self.free / "link"
        _winapi.CreateJunction(str(self.root / "data"), str(link))
        self.assertEqual(self.reason(lambda: user_files.checked_target(str(link / "x.json"), [self.root], True)),
                         "protected")
        self.assertEqual(self.reason(lambda: user_files.checked_target(str(link), [], True)), "folder_or_link")


class WriteTests(Folders):
    """`write_file` and the shared-file steps that it takes."""

    def test_the_bytes_are_written_and_checked(self) -> None:
        """No staging file stays; overwrite replaces an existing file; the answer names size and digest."""
        path = self.free / "plan.json"
        written = user_files.write_file(path, PAYLOAD, False)
        self.assertEqual((path.read_bytes(), written.size, len(written.sha256)), (PAYLOAD, len(PAYLOAD), 64))
        path.write_bytes(b"old")
        user_files.write_file(path, PAYLOAD, True)
        self.assertEqual((path.read_bytes(), list(self.free.iterdir())), (PAYLOAD, [path]))

    def test_a_file_that_appears_before_the_rename_stays(self) -> None:
        """Without overwrite, a file of another program is never replaced, and the staging file goes."""
        path, original = self.free / "plan.json", shared_files.stage_bytes

        def stage_then_appear(*arguments):
            """Stage the bytes, then let another program create the target."""
            staged = original(*arguments)
            path.write_bytes(b"theirs")
            return staged

        with patch.object(shared_files, "stage_bytes", stage_then_appear):
            self.assertEqual(self.reason(lambda: user_files.write_file(path, PAYLOAD, False)), "exists")
        self.assertEqual((path.read_bytes(), list(self.free.iterdir())), (b"theirs", [path]))

    def test_a_failed_step_leaves_no_new_file(self) -> None:
        """A staging, replace or read-back failure is not_written; an old file stays when the replace fails."""
        path = self.free / "plan.json"
        with patch.object(shared_files, "stage_bytes", side_effect=PermissionError(13, "denied")):
            self.assertEqual(self.reason(lambda: user_files.write_file(path, PAYLOAD, True)), "not_written")
        self.assertEqual(list(self.free.iterdir()), [])
        with patch.object(shared_files, "read_bytes_shared", return_value=b"other"):
            self.assertEqual(self.reason(lambda: user_files.write_file(path, PAYLOAD, True)), "not_written")
        self.assertEqual(list(self.free.iterdir()), [])
        path.write_bytes(b"old")
        with patch.object(shared_files, "replace_file", side_effect=PermissionError(13, "denied")):
            self.assertEqual(self.reason(lambda: user_files.write_file(path, PAYLOAD, True)), "not_written")
        self.assertEqual((path.read_bytes(), list(self.free.iterdir())), (b"old", [path]))

    def test_a_caller_can_pass_its_own_steps(self) -> None:
        """The steps argument names the module whose step functions the write takes."""
        calls = []

        class Steps:
            """The shared steps, with a recorded staging step."""

            stage_bytes = staticmethod(lambda *arguments: calls.append("stage") or shared_files.stage_bytes(*arguments))
            rename_new_file, replace_file = staticmethod(shared_files.rename_new_file), staticmethod(shared_files.replace_file)
            read_bytes_shared, remove_file = staticmethod(shared_files.read_bytes_shared), staticmethod(shared_files.remove_file)

        user_files.write_file(self.free / "plan.json", PAYLOAD, False, steps=Steps)
        self.assertEqual(calls, ["stage"])


class ReadTests(Folders):
    """`read_text`: at most the limit plus one byte, and the three read reasons."""

    def test_the_limit_and_the_reasons(self) -> None:
        """A file of exactly the limit is read; one byte more, a missing file and non-UTF-8 bytes are refused."""
        path = self.free / "plan.json"
        path.write_bytes(b'{"a": 123}')
        self.assertEqual(user_files.read_text(path, 10), '{"a": 123}')
        self.assertEqual(self.reason(lambda: user_files.read_text(path, 9)), "too_large")
        self.assertEqual(self.reason(lambda: user_files.read_text(self.free / "missing.json", 10)), "unreadable")
        path.write_bytes(b"\xff\xfe")
        self.assertEqual(self.reason(lambda: user_files.read_text(path, 10)), "not_utf8")

    def test_the_read_stops_after_the_limit_plus_one_byte(self) -> None:
        """The opener's stream is read once with the limit plus one byte."""
        sizes = []

        class Stream:
            """A stream that records the size of each read."""

            def __enter__(self):
                """Return the stream."""
                return self

            def __exit__(self, *_arguments):
                """Close nothing."""
                return False

            def read(self, size):
                """Record the size and give eleven bytes."""
                sizes.append(size)
                return b"x" * 11

        self.assertEqual(self.reason(lambda: user_files.read_text(Path("x"), 10, opener=lambda _path: Stream())),
                         "too_large")
        self.assertEqual(sizes, [11])

    def test_the_error_is_no_os_error_and_has_a_closed_reason(self) -> None:
        """A refusal never looks like a failed file step; an unknown reason is a programming error."""
        self.assertFalse(issubclass(UserFileError, OSError))
        self.assertEqual(user_files.REASONS, {"no_folder", "folder_or_link", "protected", "exists", "not_written",
                                              "unreadable", "too_large", "not_utf8"})
        with self.assertRaises(ValueError):
            UserFileError("other")


if __name__ == "__main__":
    unittest.main()
