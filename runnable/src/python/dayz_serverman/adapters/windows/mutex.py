"""Installation-scoped Windows mutex adapter."""

from __future__ import annotations

import ctypes
import hashlib
import os
from contextlib import contextmanager
from ctypes import wintypes
from typing import Iterator

from ...domain.lifecycle import LifecycleFailure, canonical_process_path


# Win32 error raised when CreateMutexW reports an already existing named mutex
ERROR_ALREADY_EXISTS = 183


def installation_mutex_name(dayz_root: str) -> str:
    """Derive the installation-scoped mutex name from the canonical DayZ root."""
    # Normalize the root first so equivalent spellings share one mutex
    canonical = canonical_process_path(dayz_root)
    # Bind the name to the installation with a truncated SHA-256 digest
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return rf"Local\DayZ-ServerMan-{digest}"


class WindowsInstallationMutex:
    """Serialize manager control for one installation with a named kernel mutex."""

    @contextmanager
    def guard(self, dayz_root: str) -> Iterator[None]:
        """Hold the installation mutex for the duration of the block."""
        # The mutex exists only on Windows; refuse elsewhere
        if os.name != "nt":
            raise LifecycleFailure("CONTROL_CONFLICT", "Windows process control is unavailable.")
        # Resolve and type the kernel calls before use
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateMutexW
        create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        create.restype = wintypes.HANDLE
        close = kernel32.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        # Clear the last error so a pre-existing mutex is distinguishable
        ctypes.set_last_error(0)
        handle = create(None, False, installation_mutex_name(dayz_root))
        if not handle:
            raise LifecycleFailure(
                "CONTROL_CONFLICT",
                "The installation control mutex could not be acquired.",
                retryable=True,
            )
        # Compare before closing so the acquisition outcome is not lost
        already_exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if already_exists:
            close(handle)
            raise LifecycleFailure(
                "CONTROL_CONFLICT",
                "Another manager controls this DayZ installation.",
                retryable=True,
            )
        # Keep the handle alive across the block and release it on every path
        try:
            yield
        finally:
            close(handle)

