"""A13 conditional backup exemption (Architect ruling 2026-10-08 00:34:47, 2.3 residual).

`list_backups` and `list_backup_catalog` run without the reader side only while the effective
backup root lies outside the DayZ root; at the DayZ root or below it they take the reader side.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_fixtures import PROFILE_ID, populate  # noqa: E402
from dayz_serverman import session_observer  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile, FolderWriter  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.runner import run  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402
from dayz_serverman.session import FOLDER_LOCK_FILE  # noqa: E402
from dayz_serverman.session_observer import BACKUP_ROOT_METHODS, backup_root_in_dayz_root  # noqa: E402


class FakeSettings:
    """A settings service with a DayZ root and an effective backup root."""

    def __init__(self, dayz_root: str | None, backup_root: str, failing: bool = False) -> None:
        """Keep the two roots; `failing` makes the load raise."""
        self.dayz_root, self.root, self.failing = dayz_root, backup_root, failing

    def load(self) -> SimpleNamespace:
        """Return the settings record, or fail like an unreadable settings file."""
        if self.failing:
            raise OSError("settings cannot be read")
        return SimpleNamespace(dayz_root=self.dayz_root)

    def backup_root(self, _settings: object) -> Path:
        """Return the effective backup root."""
        return Path(self.root)


class DecisionTests(unittest.TestCase):
    """The decision from the settings of each call: canonical, case-insensitive, lexical."""

    def test_inside_and_outside(self) -> None:
        """The DayZ root itself and any folder below it are inside; a sibling with the same prefix is not."""
        dayz = r"D:\Games\DayZ Root"
        cases = {
            r"D:\Games\DayZ Root": True, r"d:\games\dayz root\serverman\main\backups": True,
            r"D:\Games\DayZ Root\.\mpmissions\..\keys\backups": True, r"D:\Games\DayZ Root2\backups": False,
            r"D:\Manager\backups": False, r"E:\Games\DayZ Root\backups": False,
        }
        for backup_root, expected in cases.items():
            with self.subTest(backup_root=backup_root):
                self.assertEqual(backup_root_in_dayz_root(FakeSettings(dayz, backup_root)), expected)

    def test_drive_root_as_dayz_root(self) -> None:
        """A DayZ root at a drive root holds every folder of that drive."""
        self.assertTrue(backup_root_in_dayz_root(FakeSettings("D:\\", r"D:\backups")))
        self.assertFalse(backup_root_in_dayz_root(FakeSettings("D:\\", r"E:\backups")))

    def test_no_dayz_root_and_unreadable_settings(self) -> None:
        """Without a DayZ root nothing is swapped (exempt); unreadable settings take the reader side."""
        self.assertFalse(backup_root_in_dayz_root(FakeSettings(None, r"D:\backups")))
        self.assertTrue(backup_root_in_dayz_root(FakeSettings(r"D:\DayZ", r"E:\b", failing=True)))

    def test_the_two_methods(self) -> None:
        """The condition applies to the two backup reads only."""
        self.assertEqual(BACKUP_ROOT_METHODS, {"list_backups", "list_backup_catalog"})


@unittest.skipUnless(os.name == "nt", "the server-folder lock is a Windows lock")
class HeldWriterTests(unittest.TestCase):
    """`backup list` with an owner's writer side held: exempt with the default root, waits inside the DayZ root."""

    def setUp(self) -> None:
        """Populate a manager root and a DayZ root; create the lock file as an owner session would."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_backup_root_")
        self.addCleanup(temporary.cleanup)
        self.manager, self.dayz = populate(Path(temporary.name))

    def cli(self, *arguments: str) -> tuple[int, dict]:
        """Run one command line in JSON mode and return its code and document."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run([*arguments, "--json"], self.manager, stdout=stdout, stderr=stderr, interrupts=Interrupts())
        return code, json.loads(stdout.getvalue())

    def set_backup_root(self, folder: Path) -> None:
        """Save a custom backup root, as the Settings page would."""
        folder.mkdir(parents=True, exist_ok=True)
        composition = build_composition(self.manager)
        try:
            current = composition.settings.load()
            composition.settings.save(SettingsInput(
                dayz_root=current.dayz_root, dayz_executable=current.dayz_executable,
                custom_backup_root=str(folder)), current.revision)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def with_writer_held(self, check) -> None:
        """Run `check` while a writer side is held and observer calls wait only 0.3 s."""
        lock_file = FolderLockFile.create(self.manager / "data" / FOLDER_LOCK_FILE)
        writer, entered, leave = FolderWriter(lock_file), threading.Event(), threading.Event()

        def hold() -> None:
            """Hold the writer side until the check ends."""
            with writer.exclusive(5):
                entered.set()
                leave.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(entered.wait(5))
            with patch.object(session_observer, "READER_WAIT_SECONDS", 0.3):
                check()
        finally:
            leave.set()
            holder.join(10)
            lock_file.close()

    def test_default_backup_root_runs_exempt(self) -> None:
        """The default root is in the manager root, outside the DayZ root: both reads run."""
        def check() -> None:
            """Both backup reads answer while the writer side is held."""
            self.assertEqual(self.cli("backup", "list", "--profile", PROFILE_ID)[0], 0)
            self.assertEqual(self.cli("backup", "list", "--all")[0], 0)
        self.with_writer_held(check)

    def test_backup_root_in_a_generated_profile_folder_waits_and_exits_3(self) -> None:
        """A custom root below the DayZ root takes the reader side: both reads refuse with exit 3."""
        self.set_backup_root(self.dayz / "serverman" / PROFILE_ID / "backups")

        def check() -> None:
            """Each read waits for the reader side, then refuses; an unrelated exempt read still runs."""
            for arguments in (("backup", "list", "--profile", PROFILE_ID), ("backup", "list", "--all")):
                with self.subTest(arguments=arguments):
                    code, document = self.cli(*arguments)
                    self.assertEqual((code, document["error"]["code"]), (3, "CONTROL_CONFLICT"))
            self.assertEqual(self.cli("profile", "list")[0], 0)
        self.with_writer_held(check)
        # Without a held writer side the same reads run
        self.assertEqual(self.cli("backup", "list", "--profile", PROFILE_ID)[0], 0)


if __name__ == "__main__":
    unittest.main()
