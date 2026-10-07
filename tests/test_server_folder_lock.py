"""Server-folder lock (A13) across processes: shared readers, a writer that waits and refuses, kill release."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folder_lock_fixtures import (  # noqa: E402
    body_and_gate_free,
    finish,
    held_reader,
    kill,
    spawn_reader,
    tree_hashes,
    writer_for,
)
from dayz_serverman.adapters.windows import server_folder_lock  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import (  # noqa: E402
    NO_FOLDER_LOCK,
    FolderLockFile,
    FolderLockUnsupported,
    FolderReader,
    ServerFolderBusy,
)
from dayz_serverman.adapters.windows.shared_files import rename_directory  # noqa: E402


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class ServerFolderLockTests(unittest.TestCase):
    """The reader and writer sides of the lock file, with a second process where the design asks for one."""

    def setUp(self) -> None:
        """Create a manager data folder with the lock file and a small server folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_folder_lock_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.lock_path = self.base / "data" / "server-folders.lock"
        self.lock_path.parent.mkdir()
        FolderLockFile.create(self.lock_path).close()
        self.mod = self.base / "DayZ" / "@Mod"
        self.mod.mkdir(parents=True)
        (self.mod / "mod.cpp").write_text("name = \"Mod\";\n", encoding="utf-8")

    def writer(self):
        """Return a writer on its own handle, closed at cleanup."""
        writer = writer_for(self.lock_path)
        self.addCleanup(writer.close)
        return writer

    def test_readers_share(self) -> None:
        """A reader of this process runs while a child process holds the reader side."""
        child = spawn_reader(self.lock_path, "hold")
        self.addCleanup(kill, child)
        started = time.monotonic()
        with held_reader(self.lock_path):
            self.assertLess(time.monotonic() - started, 0.5)
        finish(child)

    def test_writer_waits_for_a_held_reader_then_works(self) -> None:
        """The writer closes the gate, waits for the reader inside, then runs its step."""
        child = spawn_reader(self.lock_path, "hold_for", "0.6")
        self.addCleanup(kill, child)
        started = time.monotonic()
        with self.writer().exclusive(5):
            waited = time.monotonic() - started
            rename_directory(self.mod, self.mod.with_name("@Mod.recovery"))
        self.assertGreaterEqual(waited, 0.3)
        self.assertLess(waited, 5)
        self.assertTrue(self.mod.with_name("@Mod.recovery").is_dir())
        self.assertEqual(child.stdout.readline().strip(), "released")
        finish(child)

    def test_writer_refuses_after_its_bound_and_the_tree_is_unchanged(self) -> None:
        """A reader longer than the bound refuses the step before it runs."""
        before = tree_hashes(self.base / "DayZ")
        child = spawn_reader(self.lock_path, "hold")
        self.addCleanup(kill, child)
        started = time.monotonic()
        with self.assertRaises(ServerFolderBusy) as raised:
            with self.writer().exclusive(0.4):
                rename_directory(self.mod, self.mod.with_name("@Mod.recovery"))
        self.assertGreaterEqual(time.monotonic() - started, 0.4)
        self.assertEqual(raised.exception.code, "CONTROL_CONFLICT")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.safe_message,
                         "Another DayZ-ServerMan process is reading the server files. Nothing was changed.")
        self.assertEqual(tree_hashes(self.base / "DayZ"), before)
        finish(child)
        # The refused writer left the gate open: the lock is free again
        self.assertTrue(body_and_gate_free(self.lock_path))

    def test_a_looping_reader_cannot_keep_the_writer_out(self) -> None:
        """Overlapping reads of two handles: the gate stops new reads, so the writer gets in quickly."""
        child = spawn_reader(self.lock_path, "loop")
        self.addCleanup(kill, child)
        started = time.monotonic()
        for _ in range(5):
            with self.writer().exclusive(2):
                pass
        self.assertLess(time.monotonic() - started, 5)
        finish(child)

    def test_a_killed_reader_releases_at_once(self) -> None:
        """Windows releases the reader's lock when its process is terminated."""
        child = spawn_reader(self.lock_path, "hold")
        kill(child)
        started = time.monotonic()
        with self.writer().exclusive(2):
            pass
        self.assertLess(time.monotonic() - started, 0.5)

    def test_a_reader_refuses_while_a_writer_holds_the_gate(self) -> None:
        """A new observer call waits for the bound, then refuses with CONTROL_CONFLICT."""
        reader = FolderReader(FolderLockFile.create(self.lock_path))
        self.addCleanup(reader.close)
        with self.writer().exclusive(1):
            with self.assertRaises(ServerFolderBusy) as raised:
                with reader.shared(0.2):
                    self.fail("the reader must not enter")
        self.assertEqual(raised.exception.code, "CONTROL_CONFLICT")
        # After the writer, the reader enters again
        with reader.shared(1):
            pass

    def test_the_observer_never_creates_the_lock_file(self) -> None:
        """open_existing returns None for a missing file or folder and creates nothing."""
        missing = self.base / "other" / "data" / "server-folders.lock"
        self.assertIsNone(FolderLockFile.open_existing(missing))
        self.assertFalse((self.base / "other").exists())
        self.lock_path.unlink()
        self.assertIsNone(FolderLockFile.open_existing(self.lock_path))
        self.assertFalse(self.lock_path.exists())

    def test_a_nested_writer_scope_is_a_programming_error(self) -> None:
        """Same thread: RuntimeError; another thread of the process: busy after its bound."""
        writer = self.writer()
        outcome: list[BaseException] = []
        with writer.exclusive(1):
            with self.assertRaises(RuntimeError):
                with writer.exclusive(1):
                    pass

            def other() -> None:
                """Try the same writer from a second thread."""
                try:
                    with writer.exclusive(0.2):
                        pass
                except BaseException as error:
                    outcome.append(error)
            thread = threading.Thread(target=other)
            thread.start()
            thread.join(5)
        self.assertIsInstance(outcome[0], ServerFolderBusy)
        # The scope was released: the writer works again
        with writer.exclusive(1):
            pass

    def test_lock_file_ex_errors_other_than_a_conflict_are_unsupported(self) -> None:
        """Error 33 means busy; any other error (network and synced folders) is FolderLockUnsupported."""
        reader = FolderReader(FolderLockFile.create(self.lock_path))
        self.addCleanup(reader.close)
        with mock.patch.object(server_folder_lock, "_lock_file_ex", return_value=1):
            with self.assertRaises(FolderLockUnsupported):
                with reader.shared(1):
                    pass

    def test_no_folder_lock_runs_both_sides_at_once(self) -> None:
        """The default where nothing is wired: no wait and no refusal."""
        with NO_FOLDER_LOCK.exclusive(0):
            with NO_FOLDER_LOCK.shared(0):
                pass


if __name__ == "__main__":
    unittest.main()
