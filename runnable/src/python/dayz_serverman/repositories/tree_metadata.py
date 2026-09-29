"""Lightweight, content-free tree identity for fast-path cache checks."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
import unicodedata
from pathlib import Path
from ctypes import wintypes

from .backup_verification import is_reparse


class TreeMetadataError(RuntimeError):
    """Raised when a tree cannot be safely fingerprinted."""
    pass


def has_alternate_stream(path: Path) -> bool:
    """Return True when a file carries a non-default NTFS stream."""
    # Alternate data streams exist only on Windows
    if os.name != "nt":
        return False

    class StreamData(ctypes.Structure):
        """Mirror of the Win32 stream enumeration record."""
        _fields_ = [("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296)]

    data = StreamData()
    # Bind the kernel32 entry points used for stream enumeration
    kernel = ctypes.windll.kernel32
    kernel.FindFirstStreamW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(StreamData), wintypes.DWORD,
    )
    kernel.FindFirstStreamW.restype = wintypes.HANDLE
    kernel.FindNextStreamW.argtypes = (wintypes.HANDLE, ctypes.POINTER(StreamData))
    kernel.FindNextStreamW.restype = wintypes.BOOL
    kernel.FindClose.argtypes = (wintypes.HANDLE,)
    handle = kernel.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    if handle == wintypes.HANDLE(-1).value:
        # Error 2 or 38 means the file or its stream set is absent
        if kernel.GetLastError() in (2, 38):
            return False
        raise TreeMetadataError("file streams could not be inspected")
    try:
        while True:
            # The default data stream is not an alternate stream
            if data.name != "::$DATA":
                return True
            if not kernel.FindNextStreamW(handle, ctypes.byref(data)):
                break
    finally:
        kernel.FindClose(handle)
    return False


def tree_metadata_digest(root: Path) -> str:
    """Return a digest over the tree's paths, sizes, and modification times."""
    # Refuse roots that are missing, linked, or streamed
    if not root.is_dir() or is_reparse(root) or has_alternate_stream(root):
        raise TreeMetadataError("tree root is missing or unsafe")
    entries: list[tuple[object, ...]] = []
    identities: set[str] = set()
    # Walk without following links so linked directories cannot recurse
    for parent, directories, files in os.walk(root, followlinks=False):
        base = Path(parent)
        if is_reparse(base) or has_alternate_stream(base):
            raise TreeMetadataError("tree contains an unsafe directory")
        # Collect directories first, then files with size and mtime
        for name in directories:
            child = base / name
            relative = _entry(child, root, identities, directory=True)
            entries.append(("D", relative))
        for name in files:
            child = base / name
            relative = _entry(child, root, identities, directory=False)
            state = child.stat()
            entries.append(("F", relative, state.st_size, state.st_mtime_ns))
    # Sort deterministically so the digest is stable across runs
    entries.sort(key=lambda value: (str(value[1]).casefold(), str(value[1]), value[0]))
    # Hash the compact JSON projection of the entries
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entry(path: Path, root: Path, identities: set[str], *, directory: bool) -> str:
    """Return the normalized relative path of one tree entry."""
    # Reject colon names, links, and streamed files before they enter the digest
    if ":" in path.name or is_reparse(path) or has_alternate_stream(path):
        raise TreeMetadataError("tree contains an unsafe entry")
    if directory and not path.is_dir():
        raise TreeMetadataError("tree directory changed")
    if not directory and not path.is_file():
        raise TreeMetadataError("tree file changed")
    # Normalize the relative path so equivalent names hash the same
    relative = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
    # Case-folded duplicates are collisions on Windows filesystems
    identity = relative.casefold()
    if identity in identities:
        raise TreeMetadataError("tree has a path identity collision")
    identities.add(identity)
    return relative
