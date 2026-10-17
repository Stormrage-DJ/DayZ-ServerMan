"""Leaf 10.6: the export output-path adapter, its shared-file steps and the protected roots, in temporary folders."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.adapters.windows import shared_files  # noqa: E402
from dayz_serverman.cli import map_files  # noqa: E402
from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli.commands.map_transfer import protected_roots  # noqa: E402
from dayz_serverman.cli.exit_codes import FAILED, NOT_CONFIRMED, USAGE  # noqa: E402
from dayz_serverman.cli.output import CliFailure, Echo, TypeText, line_text  # noqa: E402

TEXT = '{\n  "format": "x",\n  "name": "Zone ä"\n}\n'


class OutputCheckTests(unittest.TestCase):
    """`check_output` against a protected root in a temporary folder."""

    def setUp(self) -> None:
        """Create a protected root and a free sibling whose name starts with the root name."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_map_files_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "Manager"
        self.sibling = self.base / "Manager Two"
        for folder in (self.root / "data", self.sibling):
            folder.mkdir(parents=True)

    def refusal(self, typed: Path, overwrite: bool = False) -> CliFailure:
        """Return the refusal of one output path."""
        with self.assertRaises(CliFailure) as raised:
            map_files.check_output(str(typed), [self.root], overwrite)
        return raised.exception

    def test_a_free_folder_gives_the_target_in_the_resolved_folder(self) -> None:
        """A sibling whose name only starts like the root is free; the target is in the resolved folder."""
        target = map_files.check_output(str(self.sibling / "plan.json"), [self.root], False)
        self.assertEqual((target.typed, target.path), (str(self.sibling / "plan.json"),
                                                       self.sibling.resolve() / "plan.json"))

    def test_the_root_and_every_folder_below_it_are_protected(self) -> None:
        """Exit 2 with the protected-folder text, in another case too; the path shows as typed."""
        for typed in (self.root / "plan.json", self.root / "data" / "plan.json",
                      Path(str(self.root / "data").upper()) / "plan.json"):
            with self.subTest(path=typed):
                failure = self.refusal(typed, overwrite=True)
                self.assertEqual((failure.code, failure.exit_code), ("USAGE", USAGE))
                self.assertEqual(line_text(failure.message), words.EXPORT_PROTECTED.format(path=typed))
                self.assertIn(Echo(str(typed)), failure.message.parts)

    def test_the_order_of_the_path_refusals(self) -> None:
        """A missing folder, then a folder or link, then the protected check, then an existing file."""
        failure = self.refusal(self.root / "missing" / "plan.json")
        self.assertEqual(line_text(failure.message),
                         words.EXPORT_NO_FOLDER.format(path=self.root / "missing" / "plan.json"))
        failure = self.refusal(self.root / "data")
        self.assertEqual(line_text(failure.message), words.EXPORT_FOLDER_OR_LINK.format(path=self.root / "data"))
        existing = self.sibling / "plan.json"
        existing.write_bytes(b"old")
        failure = self.refusal(existing)
        self.assertEqual((failure.code, failure.exit_code), ("CONFIRMATION_REQUIRED", NOT_CONFIRMED))
        self.assertIn(TypeText("--overwrite"), failure.message.parts)
        self.assertEqual(map_files.check_output(str(existing), [self.root], True).path, existing.resolve())

    def test_protected_roots_name_the_manager_root_the_backup_folder_and_the_dayz_root(self) -> None:
        """The three roots of DC-3; a DayZ root that is not set adds nothing."""
        backups = self.base / "Backups"

        def session(dayz_root):
            """Return a session whose settings name the backup folder and the DayZ root."""
            settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=dayz_root),
                                       backup_root=lambda _current: backups)
            return SimpleNamespace(paths=SimpleNamespace(root=self.root), composition=SimpleNamespace(settings=settings))

        self.assertEqual(protected_roots(session(str(self.base / "DayZ"))), (self.root, backups, self.base / "DayZ"))
        self.assertEqual(protected_roots(session(None)), (self.root, backups))


class WriteTests(unittest.TestCase):
    """`write_export` and the shared-file steps that it uses."""

    def setUp(self) -> None:
        """Create the output folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_map_write_")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.path = self.folder / "plan.json"
        self.target = map_files.ExportTarget("plan.json", self.path)

    def failure(self, overwrite: bool = False) -> CliFailure:
        """Return the failure of one write."""
        with self.assertRaises(CliFailure) as raised:
            map_files.write_export(self.target, TEXT, overwrite)
        return raised.exception

    def test_the_bytes_are_the_utf8_text_and_the_answer_names_size_and_digest(self) -> None:
        """No staging file stays; with --overwrite an existing file is replaced."""
        written = map_files.write_export(self.target, TEXT, False)
        self.assertEqual(self.path.read_bytes(), TEXT.encode("utf-8"))
        self.assertEqual((written.path, written.size), (self.path, len(TEXT.encode("utf-8"))))
        self.assertEqual(len(written.sha256), 64)
        self.path.write_bytes(b"old")
        map_files.write_export(self.target, TEXT, True)
        self.assertEqual(self.path.read_bytes(), TEXT.encode("utf-8"))
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_a_check_that_differs_removes_the_file_and_exits_1(self) -> None:
        """A different read-back: the file and the staging file are gone; the text names the path."""
        with patch.object(map_files, "read_bytes_shared", return_value=b"other"):
            failure = self.failure()
        self.assertEqual((failure.code, failure.exit_code), ("STORAGE_FAILURE", FAILED))
        self.assertEqual(line_text(failure.message), words.EXPORT_NOT_WRITTEN.format(path="plan.json"))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_an_os_error_before_the_rename_keeps_an_existing_file(self) -> None:
        """A staging or replace failure exits 1, removes the staging file and never touches the old file."""
        with patch.object(map_files, "stage_bytes", side_effect=PermissionError(13, "denied")):
            self.assertEqual(self.failure().exit_code, FAILED)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.path.write_bytes(b"old")
        with patch.object(map_files, "replace_file", side_effect=PermissionError(13, "denied")):
            self.assertEqual(self.failure(overwrite=True).exit_code, FAILED)
        self.assertEqual((self.path.read_bytes(), list(self.folder.iterdir())), (b"old", [self.path]))

    def test_rename_new_file_refuses_an_existing_target(self) -> None:
        """The no-replace rename of the adapter raises FileExistsError and changes neither file."""
        staged = shared_files.stage_bytes(self.folder, "plan.json", b"new")
        self.path.write_bytes(b"old")
        with self.assertRaises(FileExistsError):
            shared_files.rename_new_file(staged, self.path)
        self.assertEqual((self.path.read_bytes(), staged.read_bytes()), (b"old", b"new"))
        shared_files.remove_file(staged)
        shared_files.remove_file(staged)
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_stage_bytes_creates_no_folder(self) -> None:
        """A missing folder fails, and nothing is created."""
        with self.assertRaises(FileNotFoundError):
            shared_files.stage_bytes(self.folder / "missing", "plan.json", b"x")
        self.assertEqual(list(self.folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
