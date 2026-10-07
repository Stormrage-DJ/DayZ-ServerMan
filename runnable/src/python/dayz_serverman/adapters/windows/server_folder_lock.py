"""Reader-writer lock over the server-folder swaps of one manager root (A13), on data/server-folders.lock.

A reader takes the gate and the body shared, releases the gate and keeps the
body for one call. A writer closes the gate exclusively, so no new reader
enters, then waits for the readers inside and takes the body exclusively.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ...domain.lifecycle import LifecycleFailure


# Windows constants of the lock-file calls (winbase.h, winerror.h)
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
LOCKFILE_FAIL_IMMEDIATELY = 0x1
LOCKFILE_EXCLUSIVE_LOCK = 0x2
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_LOCK_VIOLATION = 33
# The two byte ranges: the gate at offset 0 and the body at offset 1
GATE = 0
BODY = 1
# Poll of a reader and of a writer's body wait, and of a writer's gate wait
BODY_POLL_SECONDS = 0.05
GATE_POLL_SECONDS = 0.01
# Host messages of the two refusals
WRITER_BUSY = "Another DayZ-ServerMan process is reading the server files. Nothing was changed."
READER_BUSY = "The server files are being changed by another DayZ-ServerMan."


class ServerFolderBusy(LifecycleFailure):
    """The other side of the lock stayed held for the whole bound; nothing was changed."""

    def __init__(self, message: str = WRITER_BUSY) -> None:
        """Use the code CONTROL_CONFLICT, which the GUI and the CLI already word."""
        super().__init__("CONTROL_CONFLICT", message, retryable=True)


class FolderLockUnsupported(OSError):
    """LockFileEx failed with an error other than a lock conflict (network and synced folders)."""


class _Kernel:
    """Typed kernel32 bindings of the lock file, created once per process."""

    def __init__(self) -> None:
        """Bind CreateFileW, LockFileEx, UnlockFileEx and CloseHandle with exact types."""
        from ctypes import wintypes

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class Overlapped(ctypes.Structure):
            """OVERLAPPED with the offset fields that select the locked byte."""

            _fields_ = [
                ("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE),
            ]

        self.overlapped = Overlapped
        self.invalid = wintypes.HANDLE(-1).value
        self.kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.LockFileEx.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        )
        self.kernel32.LockFileEx.restype = wintypes.BOOL
        self.kernel32.UnlockFileEx.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
        )
        self.kernel32.UnlockFileEx.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel32.CloseHandle.restype = wintypes.BOOL


_KERNEL: _Kernel | None = None


def _kernel() -> _Kernel:
    """Return the process-wide typed bindings, creating them on first use."""
    global _KERNEL
    if _KERNEL is None:
        _KERNEL = _Kernel()
    return _KERNEL


def _lock_file_ex(handle: Any, flags: int, offset: int) -> int:
    """Try one LockFileEx on the byte at offset without waiting; return 0 or the Windows error code."""
    kernel = _kernel()
    overlapped = kernel.overlapped(Offset=offset)
    if kernel.kernel32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
        return 0
    return ctypes.get_last_error()


def _unlock_file_ex(handle: Any, offset: int) -> None:
    """Release the lock on the byte at offset; an error of a closing handle is ignored."""
    kernel = _kernel()
    kernel.kernel32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(kernel.overlapped(Offset=offset)))


class FolderLockFile:
    """One handle on the lock file; owners open it for read and write, observers for read."""

    def __init__(self, handle: Any, path: Path) -> None:
        """Keep the open handle and the path it names."""
        self._handle = handle
        self.path = path

    @classmethod
    def create(cls, path: Path) -> FolderLockFile:
        """Open or create the lock file, as an owner session does at session start."""
        return cls(cls._open(path, GENERIC_READ | GENERIC_WRITE, OPEN_ALWAYS), path)

    @classmethod
    def open_existing(cls, path: Path) -> FolderLockFile | None:
        """Open the lock file for read without creating it; return None when no owner created it yet."""
        try:
            return cls(cls._open(path, GENERIC_READ, OPEN_EXISTING), path)
        except FileNotFoundError:
            return None

    @staticmethod
    def _open(path: Path, access: int, disposition: int) -> Any:
        """Open a handle with read, write and delete sharing, or raise the mapped OSError."""
        if os.name != "nt":
            raise FolderLockUnsupported("the server-folder lock needs Windows")
        kernel = _kernel()
        handle = kernel.kernel32.CreateFileW(
            str(path), access, FILE_SHARE_ALL, None, disposition, FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle is None or handle == kernel.invalid:
            code = ctypes.get_last_error()
            if code in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
                raise FileNotFoundError(code, "server-folder lock file is missing", str(path))
            raise OSError(None, "server-folder lock file could not be opened", str(path), code)
        return handle

    def try_lock(self, offset: int, exclusive: bool) -> bool:
        """Try one lock without waiting: True when taken, False when another handle holds the byte."""
        flags = LOCKFILE_FAIL_IMMEDIATELY | (LOCKFILE_EXCLUSIVE_LOCK if exclusive else 0)
        code = _lock_file_ex(self._handle, flags, offset)
        if code == 0:
            return True
        if code == ERROR_LOCK_VIOLATION:
            return False
        raise FolderLockUnsupported(None, "byte-range locks are unsupported in this folder", str(self.path), code)

    def unlock(self, offset: int) -> None:
        """Release the byte at offset."""
        _unlock_file_ex(self._handle, offset)

    def close(self) -> None:
        """Close the handle, which releases every lock that it still holds; a second call does nothing."""
        handle, self._handle = self._handle, None
        if handle is not None:
            _kernel().kernel32.CloseHandle(handle)


class _Side:
    """Bounded polling of one byte range, shared by the reader and the writer."""

    def __init__(
        self, lock_file: FolderLockFile, *,
        clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Keep the lock file handle and the clock and sleep that a test may replace."""
        self.lock_file = lock_file
        self._clock = clock
        self._sleep = sleep

    def _take(self, offset: int, exclusive: bool, deadline: float, poll: float, message: str) -> None:
        """Retry the byte until taken; raise ServerFolderBusy when the deadline passes first."""
        while not self.lock_file.try_lock(offset, exclusive):
            if self._clock() >= deadline:
                raise ServerFolderBusy(message)
            self._sleep(poll)

    def close(self) -> None:
        """Close the lock file handle."""
        self.lock_file.close()


class FolderReader(_Side):
    """Observer side: one shared hold of the body per bridge call."""

    @contextmanager
    def shared(self, bound_seconds: float) -> Iterator[None]:
        """Run the block with the body held shared; refuse when a writer keeps the gate for the bound."""
        deadline = self._clock() + bound_seconds
        self._take(GATE, False, deadline, BODY_POLL_SECONDS, READER_BUSY)
        try:
            self._take(BODY, False, deadline, BODY_POLL_SECONDS, READER_BUSY)
        finally:
            # Release the gate at once, so a writer can close it while this call runs
            self.lock_file.unlock(GATE)
        try:
            yield
        finally:
            self.lock_file.unlock(BODY)


class FolderWriter(_Side):
    """Owner side: the exclusive hold of one swap step, through its commit or rollback."""

    def __init__(self, lock_file: FolderLockFile, **kwargs: Any) -> None:
        """Track the thread that holds the scope, so a nested scope is found."""
        super().__init__(lock_file, **kwargs)
        self._state = threading.Lock()
        self._owner: int | None = None

    @contextmanager
    def exclusive(self, bound_seconds: float) -> Iterator[None]:
        """Run the block with the gate and the body held exclusively; refuse after the bound."""
        deadline = self._clock() + bound_seconds
        self._enter(deadline)
        try:
            # Close the gate first, so no new reader enters while the readers inside finish
            self._take(GATE, True, deadline, GATE_POLL_SECONDS, WRITER_BUSY)
            try:
                self._take(BODY, True, deadline, BODY_POLL_SECONDS, WRITER_BUSY)
            except BaseException:
                self.lock_file.unlock(GATE)
                raise
            try:
                yield
            finally:
                self.lock_file.unlock(BODY)
                self.lock_file.unlock(GATE)
        finally:
            with self._state:
                self._owner = None

    def _enter(self, deadline: float) -> None:
        """Claim the scope for this thread; a nested scope is a programming error."""
        current = threading.get_ident()
        while True:
            with self._state:
                if self._owner == current:
                    raise RuntimeError("a server-folder writer scope is not re-entrant")
                if self._owner is None:
                    self._owner = current
                    return
            # Another thread of this process holds the scope: it is busy like any other holder
            if self._clock() >= deadline:
                raise ServerFolderBusy(WRITER_BUSY)
            self._sleep(GATE_POLL_SECONDS)


class _NoFolderLock:
    """Default where no lock is wired (tests, unsupported folders): both sides run the block at once."""

    @contextmanager
    def shared(self, bound_seconds: float) -> Iterator[None]:
        """Run the block without a lock."""
        yield

    @contextmanager
    def exclusive(self, bound_seconds: float) -> Iterator[None]:
        """Run the block without a lock."""
        yield

    def close(self) -> None:
        """Nothing to close."""
        return


# The one instance of the lock-free default
NO_FOLDER_LOCK = _NoFolderLock()
