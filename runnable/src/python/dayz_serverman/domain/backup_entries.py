"""Backup manifest entries, their path rules and the restore-support classification (moved from `backups.py` in 10.3)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .profiles import ProfileValidationError, validate_relative_path

if TYPE_CHECKING:
    # Only a type hint; `backups.py` imports this module
    from .backups import BackupManifest


# Lowercase SHA-256 hex digest
SHA256 = re.compile(r"[0-9a-f]{64}")
# Restore-support classifications reported separately from backup integrity
RESTORE_COMPATIBLE = "COMPATIBLE"
RESTORE_PENDING_RUNTIME = "PENDING_RUNTIME_PROFILE_SUPPORT"
RESTORE_LEGACY = "LEGACY_PROFILE_SCHEMA"


class BackupManifestError(ValueError):
    """Raised when a backup manifest violates its persisted contract."""
    pass


def normalized_entry_path(value: object) -> str:
    """Return the validated forward-slash form of a backup entry path."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise BackupManifestError("backup entry path is invalid")
    # Normalize to NFC so path comparisons stay stable across writers
    value = unicodedata.normalize("NFC", value)
    try:
        validate_relative_path(value, "backup entry")
    except ProfileValidationError as error:
        raise BackupManifestError("backup entry path contains unsafe Windows segments") from error
    path = PurePosixPath(value)
    # Reject absolute or traversing entries before they reach a manifest
    if path.is_absolute() or ".." in path.parts or any(part in ("", ".") for part in path.parts):
        raise BackupManifestError("backup entry path must be normalized and relative")
    # Control characters are never valid inside persisted entry paths
    if any(any(ord(character) < 32 for character in part) for part in path.parts):
        raise BackupManifestError("backup entry path contains a control character")
    return path.as_posix()


def entry_path_key(value: str) -> tuple[str, str]:
    """Return the one Windows-safe manifest ordering key."""
    return (value.casefold(), value)


@dataclass(frozen=True)
class ManifestEntry:
    """One verified file entry recorded in a backup manifest."""

    path: str
    size: int
    sha256: str

    @classmethod
    def parse(cls, value: object) -> ManifestEntry:
        """Parse and validate one manifest entry object."""
        if not isinstance(value, dict) or set(value) != {"path", "size", "sha256"}:
            raise BackupManifestError("manifest entry fields are invalid")
        # Sizes are non-negative file lengths; digests are lowercase SHA-256
        size = value["size"]
        digest = value["sha256"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise BackupManifestError("manifest entry size is invalid")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise BackupManifestError("manifest entry checksum is invalid")
        return cls(normalized_entry_path(value["path"]), size, digest)

    def to_dict(self) -> dict[str, object]:
        """Return the entry as its persisted JSON object."""
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


def restore_compatibility(manifest: BackupManifest) -> tuple[str, str]:
    """Classify restore support separately from verified backup integrity."""
    # Legacy backups predate the profile-v2 runtime mapping
    if manifest.schema_version == 1:
        return (
            RESTORE_LEGACY,
            "This legacy backup cannot be restored with a profile-v2 context.",
        )
    # Entries outside the mapped trees cannot be restored. An older build refuses the `editor/` entries of schema 4
    # here and at its schema check (A10, D18). This build maps them only with schema 4 (10.10); the parse admits
    # them only there, in the layout of the editor field.
    # Imported here, because `backup_editor_manifest.py` imports this module
    from .backup_editor_manifest import EDITOR_PREFIX, EDITOR_SCHEMA_VERSION
    editor = {EDITOR_PREFIX} if manifest.schema_version >= EDITOR_SCHEMA_VERSION else set()
    mapped = {"payload", "runtime-profile"} | editor
    if any(entry.path.split("/", 1)[0] not in mapped for entry in manifest.entries):
        return (
            RESTORE_PENDING_RUNTIME,
            "This backup contains a target form that the current restore mapper does not support.",
        )
    return (RESTORE_COMPATIBLE, "This backup is compatible with the current restore mapper.")
