"""Instance lock of a manager root (A5): two holders, release, a killed holder, unsupported folders, holder file."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"
sys.path.insert(0, str(PYTHON_ROOT))

from dayz_serverman.adapters.windows.instance_holder import (  # noqa: E402
    HolderInfo,
    read_holder,
    write_holder,
)
from dayz_serverman.adapters.windows.instance_lock import (  # noqa: E402
    InstanceActive,
    InstanceLock,
    InstanceLockUnsupported,
    root_mutex_name,
)
from dayz_serverman.application.activity_wording import leaks_identifier  # noqa: E402
from dayz_serverman.session import GUI_UNSUPPORTED, gui_refusal_text  # noqa: E402

# A child process that holds the lock until it is killed
HOLDER_CHILD = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from dayz_serverman.adapters.windows.instance_lock import InstanceLock
lock = InstanceLock(Path(sys.argv[1]), sys.argv[2]).acquire(True)
print("ready", flush=True)
time.sleep(120)
"""


def refusing_locking(code: int):
    """Return a locking seam that fails every lock try with the given errno."""
    def locking(_fd: int, _mode: int, _count: int) -> None:
        """Fail like a folder whose file system answers with this error."""
        raise OSError(code, os.strerror(code))
    return locking


@unittest.skipUnless(os.name == "nt", "the instance lock is a Windows lock")
class InstanceLockTests(unittest.TestCase):
    """The byte-range lock and the root mutex exclude a second holder of one manager root."""

    def setUp(self) -> None:
        """Create a temporary data folder and a mutex name that no other test uses."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_lock_")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "instance.lock"
        self.name = root_mutex_name(Path(temporary.name) / uuid.uuid4().hex)

    def holder(self, **seams) -> InstanceLock:
        """Return a lock on the test file whose release is registered for cleanup."""
        lock = InstanceLock(self.path, self.name, **seams)
        self.addCleanup(lock.close)
        return lock

    def test_two_holders_in_one_process_exclude_each_other(self) -> None:
        """The second holder is refused with InstanceActive: errno 13 of the held byte (S3 repeated)."""
        first = self.holder().acquire(True)
        self.assertTrue(first.byte_range_supported)
        with self.assertRaises(InstanceActive) as raised:
            self.holder().acquire(True)
        self.assertIsNone(raised.exception.holder)
        # The refusal kept the first lock intact
        with self.assertRaises(InstanceActive):
            self.holder().acquire(False)

    def test_release_after_close(self) -> None:
        """After close, a new holder gets both locks at once."""
        first = self.holder().acquire(True)
        first.close()
        first.close()
        second = self.holder().acquire(True)
        self.assertTrue(second.byte_range_supported)

    def test_release_after_terminate_of_a_child_holder(self) -> None:
        """Windows releases the byte of a killed holder; the next holder gets it at once."""
        child = subprocess.Popen(
            [sys.executable, "-c", HOLDER_CHILD, str(self.path), self.name, str(PYTHON_ROOT)],
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(child.wait, 10)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        child.stdout.close()
        with self.assertRaises(InstanceActive):
            self.holder().acquire(True)
        child.kill()
        child.wait(10)
        # Measure the time until the lock is free again
        started = time.monotonic()
        while True:
            try:
                self.holder().acquire(True)
                break
            except InstanceActive:
                self.assertLess(time.monotonic() - started, 2.0)
                time.sleep(0.005)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_unsupported_byte_range_lock_refuses_a_command_and_lets_the_window_run_on_the_mutex(self) -> None:
        """Any error but errno 13: a command refuses, the window holds the root mutex alone."""
        with self.assertRaises(InstanceLockUnsupported):
            self.holder(locking=refusing_locking(errno.EINVAL)).acquire(True)
        window = self.holder(locking=refusing_locking(errno.EINVAL)).acquire(False)
        self.assertFalse(window.byte_range_supported)
        # A second window in this process is refused through the root mutex
        with self.assertRaises(InstanceActive):
            self.holder(locking=refusing_locking(errno.EINVAL)).acquire(False)

    def test_a_failing_mutex_as_well_is_unsupported_for_the_window_too(self) -> None:
        """Neither lock works: the window cannot run either."""
        with self.assertRaises(InstanceLockUnsupported):
            self.holder(locking=refusing_locking(errno.EINVAL),
                        create_mutex=lambda _name: (None, False)).acquire(False)
        # A working byte-range lock is enough even when the mutex fails
        lock = self.holder(create_mutex=lambda _name: (None, False)).acquire(True)
        self.assertTrue(lock.byte_range_supported)

    def test_errno_13_is_a_holder_and_never_unsupported(self) -> None:
        """A held byte is InstanceActive, also through the seam."""
        with self.assertRaises(InstanceActive):
            self.holder(locking=refusing_locking(errno.EACCES)).acquire(False)

    def test_root_mutex_name_folds_case_and_keeps_apart_from_the_installation_mutex(self) -> None:
        """Equal spellings share a name; the root- part differs from the installation mutex."""
        upper = root_mutex_name(Path(r"C:\Manager\Root"))
        lower = root_mutex_name(Path(r"c:\manager\root"))
        self.assertEqual(upper, lower)
        self.assertTrue(upper.startswith("Local\\DayZ-ServerMan-root-"))
        self.assertEqual(len(upper.rsplit("-", 1)[1]), 32)


class HolderFileTests(unittest.TestCase):
    """The holder file names a live holder only, and the window's refusal names it in words."""

    def setUp(self) -> None:
        """Create a temporary data folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_holder_")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "instance-holder.json"

    def test_holder_file_content_and_live_read(self) -> None:
        """Schema 1 with the exact fields; read back while this process lives."""
        write_holder(self.path, "command", "server start", created_ns=lambda _pid: 123456789)
        document = json.loads(self.path.read_bytes())
        self.assertEqual(set(document), {"schema_version", "holder", "command", "pid", "process_created_ns",
                                         "started_at"})
        self.assertEqual((document["schema_version"], document["holder"], document["command"], document["pid"]),
                         (1, "command", "server start", os.getpid()))
        holder = read_holder(self.path, created_ns=lambda _pid: 123456789)
        self.assertEqual(holder, HolderInfo("command", "server start", os.getpid(), document["started_at"]))
        self.assertEqual(holder.to_details()["command"], "server start")
        # No staging file stays behind
        self.assertEqual([item.name for item in self.path.parent.iterdir()], ["instance-holder.json"])

    def test_dead_or_reused_pid_and_invalid_files_name_no_holder(self) -> None:
        """Another creation time, no process, or an invalid file: the sentence names nobody."""
        write_holder(self.path, "window", created_ns=lambda _pid: 5)
        self.assertIsNone(read_holder(self.path, created_ns=lambda _pid: 6))
        self.assertIsNone(read_holder(self.path, created_ns=lambda _pid: None))
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_holder(self.path))
        self.path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        self.assertIsNone(read_holder(self.path))
        self.assertIsNone(read_holder(self.path.with_name("missing.json")))

    @unittest.skipUnless(os.name == "nt", "process creation times are read from Windows")
    def test_real_creation_time_of_this_process_is_read_back(self) -> None:
        """Without seams, the live process that wrote the file is named."""
        write_holder(self.path, "window")
        self.assertEqual(read_holder(self.path).holder, "window")

    def test_window_refusal_text(self) -> None:
        """5.3: no holder, another window, or a command."""
        base = ("DayZ-ServerMan is already active for this folder{}. Close it, or wait until the command has "
                "finished, then start DayZ-ServerMan again.")
        self.assertEqual(gui_refusal_text(None), base.format(""))
        # QF-24: the way out is worded by the kind of holder
        self.assertEqual(gui_refusal_text(HolderInfo("window", None, 7, "t")),
                         "DayZ-ServerMan is already active for this folder in another window. Close the other "
                         "window, then start DayZ-ServerMan again.")
        self.assertEqual(gui_refusal_text(HolderInfo("command", "server start", 7, "t")),
                         "DayZ-ServerMan is already active for this folder (command: server start). Wait until the "
                         "command has finished, then start DayZ-ServerMan again.")
        # Operator text: no raw identifier in any variant (5.3)
        for text in (gui_refusal_text(None), gui_refusal_text(HolderInfo("window", None, 7, "t")),
                     gui_refusal_text(HolderInfo("command", "server start", 7, "t")), GUI_UNSUPPORTED):
            self.assertFalse(leaks_identifier(text), text)


if __name__ == "__main__":
    unittest.main()
