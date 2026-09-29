"""Recomputable semantic proofs for external legacy backup entries."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import PurePosixPath


# Canonical lowercase SHA-256 hex digest
SHA256 = re.compile(r"[0-9a-f]{64}")
# Canonical seconds-resolution UTC timestamp
UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
# Warning attached to references whose archive format is unknown
UNKNOWN_WARNING = "Archive format is not recognized; the reference is not restorable."


class LegacyBackupProofError(ValueError):
    """Raised when a legacy backup proof field is invalid."""
    pass


def normalize_backup_relative(value: object, *, require_canonical: bool = True) -> str:
    """Return the canonical relative path for a legacy backup reference."""
    # Reject non-string, empty, and Windows-style values
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise LegacyBackupProofError("Legacy backup relative path is invalid.")
    # Normalize, then reject absolute, empty, or special segments
    normalized = unicodedata.normalize("NFC", value)
    path = PurePosixPath(normalized)
    if (
        path.is_absolute() or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
        or any(any(ord(character) < 32 for character in part) for part in path.parts)
    ):
        raise LegacyBackupProofError("Legacy backup relative path is unsafe.")
    # Require the value to already be canonical when requested
    canonical = path.as_posix()
    if require_canonical and value != canonical:
        raise LegacyBackupProofError("Legacy backup relative path is not canonical.")
    return canonical


def backup_path_identity(relative_path: str) -> str:
    """Return the case-folded NFC identity used to compare backup paths."""
    return unicodedata.normalize("NFC", relative_path).casefold()


def backup_reference_id(source_root_identity: str, relative_path: str) -> str:
    """Return the stable reference identifier for a backup inside its source root."""
    # Bind the path identity to the source root so identical names stay distinct
    identity = backup_path_identity(relative_path)
    return hashlib.sha256(f"{source_root_identity}\0{identity}".encode("utf-8")).hexdigest()


def backup_format(relative_path: str) -> tuple[str, tuple[str, ...]]:
    """Return the archive format and warnings inferred from the path."""
    # Detect the format from the case-folded extension
    folded = relative_path.casefold()
    if folded.endswith(".zip"):
        return "ZIP", ()
    if folded.endswith(".7z"):
        return "7Z", ()
    if folded.endswith((".tar", ".tar.gz", ".tgz")):
        return "TAR", ()
    # Unknown extensions stay reference-only and carry an explicit warning
    return "UNKNOWN", (UNKNOWN_WARNING,)


def utc_timestamp(value: object | None = None) -> str:
    """Return the current UTC time, or validate the supplied timestamp."""
    # Stamp the current seconds-resolution UTC time when none was supplied
    if value is None:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Require an exact canonical timestamp string
    if not isinstance(value, str) or UTC_TIMESTAMP.fullmatch(value) is None:
        raise LegacyBackupProofError("Legacy backup verification time is invalid.")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise LegacyBackupProofError("Legacy backup verification time is invalid.") from error
    # Reject values that do not round-trip to their canonical form
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise LegacyBackupProofError("Legacy backup verification time is invalid.")
    return value


def is_sha256(value: object) -> bool:
    """Return True when the value is a lowercase SHA-256 hex digest."""
    return isinstance(value, str) and SHA256.fullmatch(value) is not None
