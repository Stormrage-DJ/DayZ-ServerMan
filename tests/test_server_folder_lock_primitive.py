"""Cross-process checks of the LockFileEx behaviour that the A13 server-folder lock assumes."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


# Windows constants of the lock-file calls (winbase.h, winerror.h)
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
LOCKFILE_FAIL_IMMEDIATELY = 0x1
LOCKFILE_EXCLUSIVE_LOCK = 0x2
ERROR_LOCK_VIOLATION = 33
# Byte ranges of the A13 design: the gate at offset 0, the body at offset 1
GATE = 0
BODY = 1
# Limit of check 4: the lock of a killed holder is free within this time
RELEASE_LIMIT_SECONDS = 0.100


class LockFile:
    """One handle on the lock file with non-blocking LockFileEx and UnlockFileEx on one-byte ranges."""

    def __init__(self, path: str) -> None:
        """Open the lock file the way an owner session does (read and write, full sharing)."""
        import ctypes
        from ctypes import wintypes

        # Bind the kernel32 calls with exact argument and result types
        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class Overlapped(ctypes.Structure):
            """OVERLAPPED with the offset fields that select the locked byte."""

            _fields_ = [
                ("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE),
            ]

        self._overlapped = Overlapped
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.LockFileEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        ]
        self._kernel32.LockFileEx.restype = wintypes.BOOL
        self._kernel32.UnlockFileEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
        ]
        self._kernel32.UnlockFileEx.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        # Open or create the file; an invalid handle is a hard failure of the test setup
        handle = self._kernel32.CreateFileW(
            path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_ALL, None, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle is None or handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed", path)
        self._handle = handle

    def lock(self, offset: int, exclusive: bool) -> int:
        """Try one lock on the byte at offset without waiting; return 0 or the Windows error code."""
        flags = LOCKFILE_FAIL_IMMEDIATELY | (LOCKFILE_EXCLUSIVE_LOCK if exclusive else 0)
        overlapped = self._overlapped(Offset=offset)
        if self._kernel32.LockFileEx(self._handle, flags, 0, 1, 0, self._ctypes.byref(overlapped)):
            return 0
        return self._ctypes.get_last_error()

    def unlock(self, offset: int) -> int:
        """Release the lock on the byte at offset; return 0 or the Windows error code."""
        overlapped = self._overlapped(Offset=offset)
        if self._kernel32.UnlockFileEx(self._handle, 0, 1, 0, self._ctypes.byref(overlapped)):
            return 0
        return self._ctypes.get_last_error()

    def close(self) -> None:
        """Close the handle, which releases every lock that it still holds."""
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def child_main(path: str) -> None:
    """Hold locks for the parent: read one command per line and answer with the result code."""
    locker = LockFile(path)
    # Report readiness, then serve "lock <offset> shared|exclusive", "unlock <offset>" and "quit"
    print("ready", flush=True)
    for line in sys.stdin:
        words = line.split()
        if not words or words[0] == "quit":
            break
        if words[0] == "lock":
            code = locker.lock(int(words[1]), words[2] == "exclusive")
        else:
            code = locker.unlock(int(words[1]))
        print(code, flush=True)
    locker.close()


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class ServerFolderLockPrimitiveTests(unittest.TestCase):
    """The five checks of detailed design 12.0, each across the parent and one child process."""

    def setUp(self) -> None:
        """Create a temporary lock file, the parent handle and a child process with its own handle."""
        # A fresh folder per test keeps the lock file free of earlier holders
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / "server-folders.lock")
        self.parent = LockFile(self.path)
        self.addCleanup(self.parent.close)
        # The child runs this file in isolated mode, so it needs only the standard library
        self.child = subprocess.Popen(
            [sys.executable, "-I", "-u", str(Path(__file__).resolve()), "--child", self.path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.stop_child)
        self.assertEqual(self.child.stdout.readline().strip(), "ready")

    def stop_child(self) -> None:
        """End the child process and close its pipes."""
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait(10)
        self.child.stdin.close()
        self.child.stdout.close()

    def child_call(self, command: str) -> int:
        """Send one command to the child and return its result code."""
        self.child.stdin.write(command + "\n")
        self.child.stdin.flush()
        return int(self.child.stdout.readline())

    def test_1_shared_against_shared(self) -> None:
        """Both processes hold a shared lock on the same byte at once."""
        self.assertEqual(self.child_call(f"lock {GATE} shared"), 0)
        self.assertEqual(self.parent.lock(GATE, exclusive=False), 0)
        # Both releases succeed, which shows that both locks were held at the same time
        self.assertEqual(self.parent.unlock(GATE), 0)
        self.assertEqual(self.child_call(f"unlock {GATE}"), 0)

    def test_2_exclusive_against_shared(self) -> None:
        """An exclusive try fails with ERROR_LOCK_VIOLATION while the other holds shared, then succeeds."""
        self.assertEqual(self.child_call(f"lock {GATE} shared"), 0)
        self.assertEqual(self.parent.lock(GATE, exclusive=True), ERROR_LOCK_VIOLATION)
        # After the shared holder unlocks, the exclusive try succeeds
        self.assertEqual(self.child_call(f"unlock {GATE}"), 0)
        self.assertEqual(self.parent.lock(GATE, exclusive=True), 0)
        self.assertEqual(self.parent.unlock(GATE), 0)

    def test_3_shared_against_exclusive(self) -> None:
        """A shared try fails with ERROR_LOCK_VIOLATION while the other holds exclusive, then succeeds."""
        self.assertEqual(self.child_call(f"lock {GATE} exclusive"), 0)
        self.assertEqual(self.parent.lock(GATE, exclusive=False), ERROR_LOCK_VIOLATION)
        # After the exclusive holder unlocks, the shared try succeeds
        self.assertEqual(self.child_call(f"unlock {GATE}"), 0)
        self.assertEqual(self.parent.lock(GATE, exclusive=False), 0)
        self.assertEqual(self.parent.unlock(GATE), 0)

    def test_4_release_on_kill(self) -> None:
        """After TerminateProcess of the holder, the other process gets the exclusive lock within 100 ms."""
        self.assertEqual(self.child_call(f"lock {GATE} exclusive"), 0)
        self.assertEqual(self.child_call(f"lock {BODY} shared"), 0)
        self.assertEqual(self.parent.lock(GATE, exclusive=True), ERROR_LOCK_VIOLATION)
        # Popen.kill calls TerminateProcess on Windows; time runs from the call to the first success
        started = time.perf_counter()
        self.child.kill()
        code = ERROR_LOCK_VIOLATION
        while code != 0 and time.perf_counter() - started < 1.0:
            code = self.parent.lock(GATE, exclusive=True)
        elapsed = time.perf_counter() - started
        self.assertEqual(code, 0)
        self.assertEqual(self.parent.lock(BODY, exclusive=True), 0)
        print(f"\nrelease on kill: exclusive lock after {elapsed * 1000:.1f} ms", file=sys.stderr)
        self.assertLessEqual(elapsed, RELEASE_LIMIT_SECONDS)

    def test_5_gate_and_body_are_independent(self) -> None:
        """A lock on the gate byte does not block the body byte, and the reverse."""
        self.assertEqual(self.child_call(f"lock {GATE} exclusive"), 0)
        self.assertEqual(self.parent.lock(BODY, exclusive=True), 0)
        self.assertEqual(self.parent.lock(GATE, exclusive=False), ERROR_LOCK_VIOLATION)
        # Swap the bytes: the child holds the body shared, the parent takes the gate exclusive
        self.assertEqual(self.parent.unlock(BODY), 0)
        self.assertEqual(self.child_call(f"unlock {GATE}"), 0)
        self.assertEqual(self.child_call(f"lock {BODY} shared"), 0)
        self.assertEqual(self.parent.lock(GATE, exclusive=True), 0)
        self.assertEqual(self.parent.lock(BODY, exclusive=True), ERROR_LOCK_VIOLATION)
        self.assertEqual(self.parent.unlock(GATE), 0)


if __name__ == "__main__":
    # The child mode serves the cross-process checks; otherwise run the tests
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        child_main(sys.argv[2])
    else:
        unittest.main()
