"""Owner and observer sessions: method classes, refused submits, the window's refusal, lock fallback, one root."""

from __future__ import annotations

import errno
import functools
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_fixtures import seed_dayz, tree_hashes  # noqa: E402
from test_starter_bootstrap import load_starter  # noqa: E402
from dayz_serverman import __main__ as module_main  # noqa: E402
from dayz_serverman import composition as composition_module  # noqa: E402
from dayz_serverman.adapters.windows import server_folder_lock  # noqa: E402
from dayz_serverman.adapters.windows.instance_lock import (  # noqa: E402
    InstanceActive,
    InstanceLock,
    InstanceLockUnsupported,
)
from dayz_serverman.adapters.windows.server_folder_lock import NO_FOLDER_LOCK, FolderWriter  # noqa: E402
from dayz_serverman.application.operations.models import QueueUnavailable  # noqa: E402
from dayz_serverman.bridge_composition import (  # noqa: E402
    OBSERVER_READ_METHODS,
    OWNER_ONLY_METHODS,
    READER_EXEMPT_METHODS,
)
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.host import gui_main  # noqa: E402
from dayz_serverman.repositories.paths import PortablePaths  # noqa: E402
from dayz_serverman.session import GUI_UNSUPPORTED, open_owner_session  # noqa: E402
from dayz_serverman.session_observer import open_observer_session  # noqa: E402


def unsupported_locks(path: Path, mutex_name: str, *, failing_mutex: bool = False) -> InstanceLock:
    """Return an instance lock on a folder whose file system refuses byte-range locks."""
    def locking(_fd: int, _mode: int, _count: int) -> None:
        """Fail like a synced or network folder."""
        raise OSError(errno.EINVAL, "byte-range locks are unsupported")
    seams = {"create_mutex": lambda _name: (None, False)} if failing_mutex else {}
    return InstanceLock(path, mutex_name, locking=locking, **seams)


class MethodClassTests(unittest.TestCase):
    """The observer facade and the method classes of 4.3."""

    def setUp(self) -> None:
        """Create a disposable manager root."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_sessions_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Manager"

    def test_classes_cover_the_owner_allowlist_and_the_observer_facade_holds_only_reads(self) -> None:
        """Disjoint classes whose union is the owner allowlist; 21 reads; exemptions are reads."""
        owner = build_composition(self.root)
        self.addCleanup(owner.operations.shutdown, 2)
        self.assertEqual(len(OBSERVER_READ_METHODS), 21)
        self.assertEqual(OBSERVER_READ_METHODS & OWNER_ONLY_METHODS, frozenset())
        self.assertEqual(OBSERVER_READ_METHODS | OWNER_ONLY_METHODS, owner.bridge.allowed_methods)
        self.assertLessEqual(READER_EXEMPT_METHODS, OBSERVER_READ_METHODS)
        session = open_observer_session(self.root)
        self.addCleanup(session.close)
        self.assertEqual(session.composition.bridge.allowed_methods, OBSERVER_READ_METHODS)

    def test_an_observer_refuses_every_submit_and_every_owner_method(self) -> None:
        """The lane is closed; an owner method is not dispatchable; the runner refuses it as a programming error."""
        session = open_observer_session(self.root)
        self.addCleanup(session.close)
        with self.assertRaises(QueueUnavailable) as raised:
            session.composition.operations.submit("SAVE_SETTINGS", lambda _context: {})
        self.assertEqual(raised.exception.details["reason"], "SHUTTING_DOWN")
        answer = session.composition.bridge.dispatch(
            {"contract_version": 1, "request_id": "x", "method": "save_settings", "parameters": {}})
        self.assertEqual(answer["error"]["code"], "INVALID_REQUEST")
        with self.assertRaises(ValueError):
            session.call("save_settings", {})
        self.assertFalse(self.root.exists())


class WindowSessionTests(unittest.TestCase):
    """The window takes the instance lock (5.2) and refuses a second instance (P6/OD2)."""

    def setUp(self) -> None:
        """Create a manager root and a DayZ root."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_window_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "Manager"
        seed_dayz(self.base / "DayZ")

    def open_window_session(self, **options):
        """Open an owner session as the window and close it at cleanup."""
        session = open_owner_session(self.root, "window", require_byte_range_lock=False, **options)
        self.addCleanup(session.close, 5)
        return session

    @unittest.skipUnless(os.name == "nt", "the instance lock is a Windows lock")
    def test_a_second_window_is_refused_with_the_holder_named(self) -> None:
        """Native message with the 5.3 text and exit 3; the smoke form prints one JSON line; no launch."""
        self.open_window_session()
        launched: list[object] = []
        with patch.object(gui_main, "show_native_error") as shown:
            code = gui_main.run_window(self.root, launch=lambda *args, **kwargs: launched.append(args))
        self.assertEqual(code, 3)
        self.assertIn("is already active for this folder in another window", shown.call_args.args[0])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(gui_main.run_window(self.root, smoke=True, launch=launched.append), 3)
        self.assertEqual(json.loads(output.getvalue())["error"], "INSTANCE_ACTIVE")
        self.assertEqual(launched, [])
        # A command is refused the same way, and names the window
        with self.assertRaises(InstanceActive) as raised:
            open_owner_session(self.root, "command", "server start", require_byte_range_lock=True)
        self.assertEqual(raised.exception.holder.holder, "window")

    @unittest.skipUnless(os.name == "nt", "the server-folder lock is a Windows lock")
    def test_owner_session_holds_a_writer_side_and_closes_quickly(self) -> None:
        """A supported folder composes a FolderWriter; close returns at once with an idle build check."""
        session = open_owner_session(self.root, "window", require_byte_range_lock=False)
        self.assertIsInstance(session.writer, FolderWriter)
        self.assertTrue((self.root / "data" / "server-folders.lock").is_file())
        started = time.monotonic()
        session.close(5)
        self.assertLess(time.monotonic() - started, 2)
        self.open_window_session()

    def test_unsupported_byte_range_lock_window_runs_on_the_mutex_and_a_command_refuses(self) -> None:
        """QF-4 ruling: the window starts, logs and has no writer side; a second window and a command refuse."""
        session = self.open_window_session(lock_factory=unsupported_locks)
        self.assertIs(session.writer, NO_FOLDER_LOCK)
        self.assertTrue((self.root / "data" / "server-folders.lock").is_file())
        log = self.root / "data" / "logs" / "manager.jsonl"
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        warning = [item for item in events if item["event"] == "instance_lock.unsupported_folder"]
        self.assertEqual([item["level"] for item in warning], ["WARNING"])
        with self.assertRaises(InstanceActive):
            open_owner_session(self.root, "window", require_byte_range_lock=False, lock_factory=unsupported_locks)
        before = tree_hashes(self.root, self.base / "DayZ")
        session.close(5)
        after_close = tree_hashes(self.root, self.base / "DayZ")
        with self.assertRaises(InstanceLockUnsupported):
            open_owner_session(self.root, "command", "server start", require_byte_range_lock=True,
                               lock_factory=unsupported_locks)
        self.assertEqual(tree_hashes(self.root, self.base / "DayZ"), after_close)
        self.assertEqual(set(after_close) - set(before), set())

    def test_a_failing_mutex_as_well_shows_the_native_message_and_returns_1(self) -> None:
        """5.2: no exception escapes; the window shows the unsupported text."""
        failing = functools.partial(unsupported_locks, failing_mutex=True)
        opener = functools.partial(open_owner_session, lock_factory=failing)
        with patch.object(gui_main, "open_owner_session", opener), \
                patch.object(gui_main, "show_native_error") as shown:
            code = gui_main.run_window(self.root, launch=lambda *args, **kwargs: 0)
        self.assertEqual(code, 1)
        shown.assert_called_once_with(GUI_UNSUPPORTED)

    @unittest.skipUnless(os.name == "nt", "the server-folder lock is a Windows lock")
    def test_observer_with_unsupported_lock_file_ex_refuses_reader_calls_and_runs_exempt_calls(self) -> None:
        """3.2 "Unsupported folder": exit 1 class for a reader-side call; an exempt call still runs."""
        self.open_window_session()
        session = open_observer_session(self.root)
        self.addCleanup(session.close)
        with patch.object(server_folder_lock, "_lock_file_ex", return_value=1):
            with self.assertRaises(InstanceLockUnsupported):
                session.call("get_server_status", {})
            self.assertEqual(session.call("list_profiles", {}), [])


class OneRootTests(unittest.TestCase):
    """The lock files and the composition name one folder (3.1 "Root", QF-7)."""

    def setUp(self) -> None:
        """Create a disposable base folder and a recording launch."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_root_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.launched: list[tuple[Path, bool]] = []

    def launch(self, composition, **_options) -> int:
        """Record the composition root and whether its instance lock is held, as the host would run."""
        try:
            InstanceLock(composition.paths.data / "instance.lock", "Local\\unused-" + os.urandom(4).hex()).acquire(
                True).close()
            held = False
        except InstanceActive:
            held = True
        self.launched.append((composition.paths.root, held))
        return 0

    @unittest.skipUnless(os.name == "nt", "the instance lock is a Windows lock")
    def test_manager_root_option_and_source_layout_name_one_folder(self) -> None:
        """__main__ --manager-root and no root (source layout) lock the folder that the composition uses."""
        root = self.base / "Given Root"
        with patch.object(gui_main, "launch_application", self.launch):
            self.assertEqual(module_main.main(["--manager-root", str(root)]), 0)
            source_root = self.base / "Source Layout"
            with patch.object(PortablePaths, "from_source",
                              side_effect=lambda _anchor: PortablePaths.from_root(source_root)) as anchored:
                self.assertEqual(module_main.main([]), 0)
        self.assertEqual(anchored.call_args.args[0], Path(composition_module.__file__))
        self.assertEqual(self.launched, [(root.resolve(), True), (source_root.resolve(), True)])

    def test_the_starter_passes_its_application_root(self) -> None:
        """The starter hands APPLICATION_ROOT to the window path, which locks and composes that folder."""
        starter = load_starter()
        with patch.object(starter, "_ensure_runtime"), patch.object(starter, "_prepare_imports"), \
                patch.object(gui_main, "run_window", return_value=0) as run:
            self.assertEqual(starter.main([]), 0)
        self.assertEqual(run.call_args.args[0], starter.APPLICATION_ROOT)
        self.assertEqual(run.call_args.kwargs["frontend_root"], starter.FRONTEND_ROOT)


if __name__ == "__main__":
    unittest.main()
