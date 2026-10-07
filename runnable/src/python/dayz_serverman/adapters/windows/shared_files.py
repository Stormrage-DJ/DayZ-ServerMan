"""Shared-read file opener and owner replaces that open readers never block (A12)."""

from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import wintypes
from pathlib import Path
from time import monotonic, sleep
from typing import IO, Any


# CreateFileW access rights, sharing, disposition and flags
GENERIC_READ = 0x80000000
DELETE = 0x00010000
SYNCHRONIZE = 0x00100000
SHARE_ALL = 0x1 | 0x2 | 0x4
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# SetFileInformationByHandle class and flags of the POSIX-semantics rename
FILE_RENAME_INFO_EX = 22
FILE_RENAME_FLAG_REPLACE_IF_EXISTS = 0x1
FILE_RENAME_FLAG_POSIX_SEMANTICS = 0x2
# Windows errors that mean the volume refuses POSIX rename semantics
POSIX_REFUSED = frozenset({1, 50, 87, 124})
# Windows errors of an access or sharing conflict that a retry can outlast
SHARING_ERRORS = frozenset({5, 32})
# Waits of replace_file after the first try (310 ms in all)
REPLACE_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16)
# Poll interval and bound of rename_directory
RENAME_RETRY_SECONDS = 0.05
RENAME_RETRY_LIMIT = 1.0


class _RenameInfoHeader(ctypes.Structure):
    """Fixed part of FILE_RENAME_INFO; the file name follows at FileName."""

    _fields_ = (
        ("Flags", wintypes.DWORD),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    )


def _kernel32() -> Any:
    """Return a private, typed kernel32 binding so other modules keep their own signatures."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Type the three calls of this module once per binding
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


# The binding exists only on Windows; other hosts use the builtin calls
_KERNEL32 = _kernel32() if os.name == "nt" else None


def _windows_error(code: int, path: str, other: str | None = None) -> OSError:
    """Build the OSError subclass that Python itself raises for a Windows error code."""
    # Python maps winerror 2 and 3 to FileNotFoundError and 5 and 32 to PermissionError
    return OSError(0, ctypes.FormatError(code).strip(), path, code, other)


def _create_file(path: str, access: int, flags: int) -> int:
    """Open a handle with read, write and delete sharing, or raise the mapped OSError."""
    handle = _KERNEL32.CreateFileW(path, access, SHARE_ALL, None, OPEN_EXISTING, flags, None)
    if handle in (None, INVALID_HANDLE_VALUE):
        raise _windows_error(ctypes.get_last_error(), path)
    return handle


def open_shared(
    path: str | os.PathLike[str], mode: str = "rb", encoding: str | None = None,
    errors: str | None = None, newline: str | None = None,
) -> IO[Any]:
    """Open a file for reading so that owners can still replace, rename or delete it."""
    if mode not in ("rb", "r"):
        raise ValueError("open_shared supports only the modes 'rb' and 'r'")
    # Other hosts have no sharing modes; the builtin opener behaves as before
    if _KERNEL32 is None:
        return open(path, mode, encoding=encoding, errors=errors, newline=newline)
    name = os.fsdecode(os.fspath(path))
    handle = _create_file(name, GENERIC_READ, FILE_ATTRIBUTE_NORMAL)
    # Wrap the handle as a read-only binary C runtime descriptor
    try:
        import msvcrt

        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        _KERNEL32.CloseHandle(handle)
        raise
    # Return a normal Python file object; the text wrapper decodes as Path.open would
    if mode == "rb":
        return open(descriptor, "rb")
    return open(descriptor, "r", encoding=encoding, errors=errors, newline=newline)


def read_bytes_shared(path: str | os.PathLike[str]) -> bytes:
    """Read a whole file through the shared-read opener."""
    with open_shared(path) as stream:
        return stream.read()


def read_text_shared(path: str | os.PathLike[str], encoding: str | None, errors: str | None = None) -> str:
    """Read a whole text file through the shared-read opener."""
    with open_shared(path, "r", encoding=encoding, errors=errors) as stream:
        return stream.read()


def _posix_replace(source: str, target: str) -> None:
    """Rename source over target by handle with POSIX semantics."""
    # Open the source itself (not a link target), with delete access and full sharing
    flags = FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
    handle = _create_file(source, DELETE | SYNCHRONIZE, flags)
    try:
        # FILE_RENAME_INFO: flags, no root directory, then the absolute target name
        name = os.path.abspath(target)
        size = _RenameInfoHeader.FileName.offset + (len(name) + 1) * ctypes.sizeof(wintypes.WCHAR)
        buffer = ctypes.create_string_buffer(max(size, ctypes.sizeof(_RenameInfoHeader)))
        header = _RenameInfoHeader.from_buffer(buffer)
        header.Flags = FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS
        header.RootDirectory = None
        header.FileNameLength = len(name) * ctypes.sizeof(wintypes.WCHAR)
        wide = ctypes.create_unicode_buffer(name)
        ctypes.memmove(ctypes.addressof(buffer) + _RenameInfoHeader.FileName.offset, wide, header.FileNameLength)
        # Swap the name; open readers with delete sharing keep the old bytes
        if not _KERNEL32.SetFileInformationByHandle(handle, FILE_RENAME_INFO_EX, buffer, len(buffer)):
            raise _windows_error(ctypes.get_last_error(), source, target)
    finally:
        _KERNEL32.CloseHandle(handle)


def replace_file(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Replace target with the closed source file; open shared readers never make it fail."""
    source_name = os.fsdecode(os.fspath(source))
    target_name = os.fsdecode(os.fspath(target))
    posix = _KERNEL32 is not None
    delays = iter(REPLACE_RETRY_DELAYS)
    # Try the POSIX path first, fall back once the volume refuses it, retry sharing conflicts
    while True:
        try:
            if posix:
                try:
                    _posix_replace(source_name, target_name)
                    return
                except OSError as error:
                    if getattr(error, "winerror", None) not in POSIX_REFUSED:
                        raise
                    posix = False
            os.replace(source_name, target_name)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in SHARING_ERRORS:
                raise
            delay = next(delays, None)
            if delay is None:
                raise
            sleep(delay)


def rename_directory(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Rename a folder to a new name, retrying sharing conflicts for a bounded time."""
    deadline = monotonic() + RENAME_RETRY_LIMIT
    # A failed try changes nothing, so trying again is safe
    while True:
        try:
            os.rename(source, target)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in SHARING_ERRORS or monotonic() >= deadline:
                raise
            sleep(RENAME_RETRY_SECONDS)


def write_bytes_atomically(path: Path, payload: bytes, temporary_suffix: str = ".tmp") -> None:
    """Write bytes through a staging file beside the target and one atomic replace."""
    # Ensure the destination folder exists before staging
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}{temporary_suffix}")
    try:
        # Write, flush, and fsync the staging file before the swap
        with open(temporary, "xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # The atomic replacement keeps readers on either complete file
        replace_file(temporary, path)
    finally:
        # Remove the staging file when any step above failed
        temporary.unlink(missing_ok=True)
