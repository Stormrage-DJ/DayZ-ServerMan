"""Windows subprocess launch adapter with retained process handles."""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

from ...application.lifecycle_ports import LaunchReceipt, LaunchRequest


# Environment variables DayZ needs to start; everything else is withheld from the child
ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)


class WindowsProcessLauncher:
    """Launch DayZ subprocesses and retain their handles for later control."""

    def __init__(self) -> None:
        """Start with no retained process handles."""
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def launch(self, request: LaunchRequest) -> LaunchReceipt:
        """Start the requested process with a filtered environment and return its receipt."""
        # Copy only allowlisted variables so secrets and unrelated state stay out
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in ENVIRONMENT_ALLOWLIST
        }
        environment["DAYZ_SERVERMAN_LAUNCH_TOKEN"] = request.launch_token
        # Create the log destination directory on demand
        request.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Merge both streams into one append-only log file
        with request.log_path.open("ab") as output:
            process = subprocess.Popen(
                list(request.argv),
                cwd=str(request.working_directory),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                shell=False,
                close_fds=True,
            )
        # Keep the live handle behind an unguessable token
        handle_token = secrets.token_urlsafe(24)
        self._processes[handle_token] = process
        # Record the creation time so process-id reuse can be detected later
        creation_time = _query_creation_time(process.pid)
        return LaunchReceipt(process.pid, creation_time, handle_token)

    def retains_handle(self, handle_token: str, pid: int) -> bool:
        """Report whether the token still owns a live process and drop dead handles."""
        process = self._processes.get(handle_token)
        # An unknown token or a mismatched id means the handle is not ours
        if process is None or process.pid != pid:
            return False
        # A running process keeps its handle; an exited one is forgotten
        if process.poll() is None:
            return True
        self._processes.pop(handle_token, None)
        return False

    def release_handle(self, handle_token: str) -> None:
        """Forget the retained handle for a token."""
        self._processes.pop(handle_token, None)


def _query_creation_time(pid: int) -> int | None:
    """Return the Windows process creation time in nanoseconds, or None when unavailable."""
    # Creation times come from Win32 only; other hosts report no identity
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    # Bind argument types explicitly so ctypes marshals the FILETIME structures correctly
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    filetime_pointer = ctypes.POINTER(wintypes.FILETIME)
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        filetime_pointer,
        filetime_pointer,
        filetime_pointer,
        filetime_pointer,
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    # 0x1000 is PROCESS_QUERY_LIMITED_INFORMATION; a missing process yields no identity
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        # All four time fields are requested; only creation time is used
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            return None
        creation = values[0]
        # FILETIME stores 100 ns ticks in two halves; scale to nanoseconds for comparison
        return ((creation.dwHighDateTime << 32) | creation.dwLowDateTime) * 100
    finally:
        kernel32.CloseHandle(handle)
