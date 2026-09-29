"""Canonical validation for local Windows external-reference roots."""

from __future__ import annotations

import hashlib
import ntpath
import re
import unicodedata
from pathlib import Path

from .backup_verification import path_has_reparse


# Match a canonical drive-letter root prefix
DRIVE_ROOT = re.compile(r"^[A-Za-z]:\\")
# Windows device, UNC, and extended-length prefixes that are not valid roots
DEVICE_PREFIXES = ("\\\\", "//", "\\?\\", "\\.\\")
# Characters Windows forbids inside file or directory names
INVALID_SEGMENT_CHARACTERS = frozenset('<>"|?*')
# DOS device names that remain reserved on Windows
RESERVED_SEGMENTS = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


class ExternalRootError(ValueError):
    """Raised when an external reference root is not a safe local Windows path."""
    pass


def validate_external_root(
    value: object, *, require_existing: bool, require_canonical: bool = True,
) -> str:
    """Return one canonical path after lexical checks, then optional filesystem checks."""
    # Reject non-string, empty, and embedded-NUL values up front
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ExternalRootError("External source root is invalid.")
    # Normalize separators, then require a local absolute drive path
    normalized = unicodedata.normalize("NFC", value.replace("/", "\\"))
    if normalized.startswith(DEVICE_PREFIXES) or DRIVE_ROOT.match(normalized) is None:
        raise ExternalRootError("External source root must be a local absolute Windows path.")
    # Reject alternate data streams, unsafe segments, and reserved device names
    if ":" in normalized[2:]:
        raise ExternalRootError("External source root must not contain an alternate stream.")
    segments = normalized[3:].split("\\") if len(normalized) > 3 else []
    if any(
        part in ("", ".", "..") or part.endswith((" ", "."))
        or any(ord(character) < 32 or character in INVALID_SEGMENT_CHARACTERS
               for character in part)
        or part.split(".", 1)[0].casefold() in RESERVED_SEGMENTS
        for part in segments
    ):
        raise ExternalRootError("External source root contains an unsafe segment.")
    # Canonicalize the drive letter and separators for identity stability
    canonical = normalized[0].upper() + normalized[1:]
    canonical = ntpath.normpath(canonical)
    if require_canonical and value != canonical:
        raise ExternalRootError("External source root is not canonical.")
    # Apply filesystem checks only when the path already exists
    path = Path(canonical)
    if path.exists():
        if not path.is_dir() or path_has_reparse(path):
            raise ExternalRootError("External source root is not a safe local directory.")
    elif require_existing:
        raise ExternalRootError("External source root does not exist.")
    return canonical


def external_root_identity(canonical_root: str) -> str:
    """Return the stable identity digest for a canonical external root."""
    # Fold case and normalize so the same root always hashes identically
    payload = unicodedata.normalize("NFC", canonical_root).casefold().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
