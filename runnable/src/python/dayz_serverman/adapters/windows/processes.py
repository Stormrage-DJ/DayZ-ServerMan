"""Read-only Windows process inventory for DayZ reconciliation."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from ...domain.lifecycle import InventorySnapshot, ProcessObservation


# Win32 error raised when a toolhelp snapshot has no more entries to return
ERROR_NO_MORE_FILES = 18
# Win32 sentinel returned when snapshot creation fails
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# Access right needed to read a process image name and timing information
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Toolhelp flag selecting the system process list for enumeration
TH32CS_SNAPPROCESS = 0x00000002


class _ProcessEntry32W(ctypes.Structure):
    """ctypes mirror of the Windows PROCESSENTRY32W structure."""
    _fields_ = (
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    )


class WindowsProcessInventory:
    """Enumerate candidate DayZ processes read-only for reconciliation."""

    def __init__(self) -> None:
        """Refuse non-Windows hosts and bind the kernel32 signatures."""
        # Toolhelp snapshots exist only on Windows
        if os.name != "nt":
            raise OSError("Windows process inventory requires Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def candidates(self, expected_executable: str) -> InventorySnapshot:
        """Return candidate processes for one expected executable path."""
        # Match on the file name so full-path spelling differences do not hide candidates
        expected_name = Path(expected_executable).name.casefold()
        observations: list[ProcessObservation] = []
        unreadable: set[int] = set()
        for pid in self._candidate_process_ids(expected_name):
            try:
                observations.append(self._observe(pid))
            except (OSError, PermissionError):
                # An exiting process can become unreadable after the Toolhelp snapshot.
                unreadable.add(pid)
        complete = not unreadable
        if unreadable:
            # Prove absence with a new snapshot; unreadable live or new candidates still block.
            remaining = set(self._candidate_process_ids(expected_name))
            observed = {item.pid for item in observations}
            complete = not (remaining & unreadable) and remaining <= observed
        return InventorySnapshot(tuple(observations), complete)

    def _candidate_process_ids(self, expected_name: str) -> tuple[int, ...]:
        """Return process ids whose image name matches the expected executable."""
        handle = self._kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        # A failed snapshot means enumeration cannot proceed at all
        if int(handle) == INVALID_HANDLE_VALUE:
            raise OSError("process inventory could not be created")
        identifiers: list[int] = []
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            # Clear the error before the walk so completion is distinguishable
            ctypes.set_last_error(0)
            available = self._kernel32.Process32FirstW(handle, ctypes.byref(entry))
            # Walk every entry, collecting the matching process ids
            while available:
                if entry.szExeFile.casefold() == expected_name:
                    identifiers.append(int(entry.th32ProcessID))
                available = self._kernel32.Process32NextW(handle, ctypes.byref(entry))
            # Only the end-of-list error is expected; anything else leaves the scan incomplete
            if ctypes.get_last_error() not in (0, ERROR_NO_MORE_FILES):
                raise OSError("process inventory is incomplete")
        finally:
            # Release the snapshot handle on every path
            self._kernel32.CloseHandle(handle)
        return tuple(identifiers)

    def _observe(self, pid: int) -> ProcessObservation:
        """Build one observation from a live process id."""
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        # A null handle means the process vanished or access was denied
        if not handle:
            raise PermissionError("process identity is unavailable")
        try:
            # 32768 UTF-16 units cover any Windows executable path
            capacity = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(capacity.value)
            if not self._kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(capacity)
            ):
                raise OSError("process executable is unavailable")
            # Capture creation time alongside the path so reuse can be detected
            creation_ns = _creation_time_ns(self._kernel32, handle)
            return ProcessObservation(pid, buffer.value, creation_ns)
        finally:
            # Always release the queried handle
            self._kernel32.CloseHandle(handle)

    def _configure_signatures(self) -> None:
        """Declare kernel32 argument and result types for safe ctypes calls."""
        # Predeclare pointer types shared by more than one signature
        entry_pointer = ctypes.POINTER(_ProcessEntry32W)
        filetime_pointer = ctypes.POINTER(wintypes.FILETIME)
        # Argument and result types for every kernel32 call used by this adapter
        signatures = {
            "CreateToolhelp32Snapshot": ((wintypes.DWORD, wintypes.DWORD), wintypes.HANDLE),
            "Process32FirstW": ((wintypes.HANDLE, entry_pointer), wintypes.BOOL),
            "Process32NextW": ((wintypes.HANDLE, entry_pointer), wintypes.BOOL),
            "OpenProcess": ((wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
            "QueryFullProcessImageNameW": ((wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)), wintypes.BOOL),
            "GetProcessTimes": ((wintypes.HANDLE, filetime_pointer, filetime_pointer, filetime_pointer, filetime_pointer), wintypes.BOOL),
            "CloseHandle": ((wintypes.HANDLE,), wintypes.BOOL),
        }
        # Apply each signature to the bound function
        for name, (arguments, result) in signatures.items():
            function = getattr(self._kernel32, name)
            function.argtypes = arguments
            function.restype = result


def _creation_time_ns(kernel32: object, handle: int) -> int | None:
    """Return the process creation time in nanoseconds, or None when unreadable."""
    # Four FILETIME slots are required even though only creation time is used
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None
    ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    # FILETIME ticks are 100 ns units; scale to nanoseconds for comparison
    return ticks * 100
