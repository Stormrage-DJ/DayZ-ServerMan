"""Instance lock of one manager root (A5): a byte-range lock on data/instance.lock and a root mutex."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

try:
    import msvcrt
except ImportError:  # pragma: no cover - the manager runs on Windows only
    msvcrt = None  # type: ignore[assignment]


# Win32 error of CreateMutexW when the named mutex already exists
ERROR_ALREADY_EXISTS = 183
# Errno of a non-blocking msvcrt.locking try when another holder has the byte (S3)
HELD_ERRNO = errno.EACCES
# The one locked byte: offset 0, length 1
LOCKED_BYTES = 1

# Creates a named mutex without waiting: returns its handle (None when none was created) and whether it existed
MutexFactory = Callable[[str], tuple[Any, bool]]
# Closes a handle that the mutex factory returned
HandleCloser = Callable[[Any], None]


class InstanceActive(RuntimeError):
    """Another owner session holds this manager root; `holder` is filled from the holder file after a refusal."""

    def __init__(self, holder: object | None = None) -> None:
        """Store the holder details, which stay None when they cannot be shown."""
        self.holder = holder
        super().__init__("another DayZ-ServerMan is active for this manager folder")


class InstanceLockUnsupported(RuntimeError):
    """The manager folder cannot be protected against a second copy."""

    def __init__(self) -> None:
        """Use one fixed message; the operator sentence is chosen by the caller."""
        super().__init__("the manager folder cannot hold the instance lock")


def root_mutex_name(root: Path) -> str:
    """Return the name of the root mutex (fallback b) for a canonical manager root."""
    # Fold case and separators so equal spellings of one folder share one mutex
    canonical = os.path.normcase(os.path.normpath(str(root)))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    # The "root-" part keeps the name apart from the installation mutex of a DayZ root
    return rf"Local\DayZ-ServerMan-root-{digest}"


def create_named_mutex(name: str) -> tuple[Any, bool]:
    """Create the named mutex without waiting; return its handle or None, and whether it already existed."""
    # The mutex exists only on Windows; elsewhere no handle is created
    if os.name != "nt":
        return None, False
    from ctypes import wintypes

    # Bind the call with exact types, as adapters/windows/mutex.py does
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateMutexW
    create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    # Clear the last error so a pre-existing mutex is distinguishable
    ctypes.set_last_error(0)
    handle = create(None, False, name)
    return (handle or None), ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def close_handle(handle: Any) -> None:
    """Close a kernel handle; a missing handle is ignored."""
    if handle is None or os.name != "nt":
        return
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


class InstanceLock:
    """Hold the byte-range lock of the lock file and the root mutex for the life of an owner session."""

    def __init__(
        self, path: Path, mutex_name: str, *,
        locking: Callable[[int, int, int], None] | None = None,
        create_mutex: MutexFactory = create_named_mutex,
        close_mutex: HandleCloser = close_handle,
    ) -> None:
        """Store the lock file path, the mutex name and the kernel seams that a test may replace."""
        self.path = path
        self.mutex_name = mutex_name
        self._locking = locking if locking is not None else (msvcrt.locking if msvcrt else None)
        self._create_mutex = create_mutex
        self._close_mutex = close_mutex
        self._stream: BinaryIO | None = None
        self._mutex: Any = None
        # Set by acquire: whether the byte-range lock works in this folder
        self.byte_range_supported = False

    def acquire(self, require_byte_range_lock: bool) -> InstanceLock:
        """Take the byte-range lock, then the root mutex; raise InstanceActive or InstanceLockUnsupported."""
        # (a) The byte-range lock on the first byte of the lock file
        self.byte_range_supported = self._lock_byte_range()
        # (b) The root mutex, held by every owner session as well
        try:
            handle, existed = self._create_mutex(self.mutex_name)
        except OSError:
            handle, existed = None, False
        if existed:
            self._close_mutex(handle)
            self._release_byte_range()
            raise InstanceActive()
        # Neither lock works, or the caller needs the byte-range lock that this folder lacks
        if not self.byte_range_supported and (handle is None or require_byte_range_lock):
            self._close_mutex(handle)
            self._release_byte_range()
            raise InstanceLockUnsupported()
        self._mutex = handle
        return self

    def close(self) -> None:
        """Release the root mutex and the byte-range lock; a second call does nothing."""
        handle, self._mutex = self._mutex, None
        self._close_mutex(handle)
        self._release_byte_range()

    def _lock_byte_range(self) -> bool:
        """Lock the first byte; return False where the folder does not support it, raise when it is held."""
        if self._locking is None:
            return False
        # Create the file when missing; it is never read by any process
        try:
            stream = open(self.path, "a+b")
        except OSError:
            return False
        try:
            stream.seek(0)
            self._locking(stream.fileno(), msvcrt.LK_NBLCK if msvcrt else 2, LOCKED_BYTES)
        except OSError as error:
            stream.close()
            if error.errno == HELD_ERRNO:
                raise InstanceActive() from error
            # Any other error: network or synced folders that refuse byte-range locks
            return False
        self._stream = stream
        return True

    def _release_byte_range(self) -> None:
        """Unlock the byte and close the lock file, ignoring errors of a closing handle."""
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            # Unlock explicitly, so the next holder never waits for the close to release it
            stream.seek(0)
            self._locking(stream.fileno(), msvcrt.LK_UNLCK if msvcrt else 0, LOCKED_BYTES)
        except OSError:
            pass
        finally:
            stream.close()
