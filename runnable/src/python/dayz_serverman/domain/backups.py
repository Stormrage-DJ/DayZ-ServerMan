"""Versioned verified-backup manifest records."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from .profiles import ProfileValidationError, validate_profile_id, validate_relative_path


# Manifest schema version written for new backups
BACKUP_SCHEMA_VERSION = 2
# Backup identifier: letters, digits, dots, underscores, or hyphens
BACKUP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
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


@dataclass(frozen=True)
class BackupManifest:
    """Verified backup manifest binding a profile snapshot to its entries."""

    backup_id: str
    profile_id: str
    profile_revision: int
    settings_revision: int
    created_at: str
    entries: tuple[ManifestEntry, ...]
    manifest_digest: str = ""
    schema_version: int = BACKUP_SCHEMA_VERSION
    content_digest_algorithm: str = "SHA-256"
    semantic_profile_digest: str | None = None
    runtime_profile: str | None = None

    @classmethod
    def parse(cls, value: object) -> BackupManifest:
        """Parse and validate a persisted manifest object."""
        common_fields = {
            "schema_version", "backup_id", "profile_id", "profile_revision",
            "settings_revision", "created_at", "entries",
            "content_digest_algorithm", "manifest_digest",
        }
        if not isinstance(value, dict):
            raise BackupManifestError("backup manifest fields are invalid")
        # Only the two published schema versions are accepted
        version = value.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool) or version not in (1, 2):
            raise BackupManifestError("backup manifest schema is unsupported")
        # Version 2 adds the semantic digest and runtime profile fields
        fields = common_fields if version == 1 else common_fields | {
            "semantic_profile_digest", "runtime_profile",
        }
        if set(value) != fields:
            raise BackupManifestError("backup manifest fields are invalid")
        # Validate identity, revisions, and the UTC timestamp
        backup_id = value["backup_id"]
        if not isinstance(backup_id, str) or BACKUP_ID.fullmatch(backup_id) is None:
            raise BackupManifestError("backup identifier is invalid")
        revisions = (value["profile_revision"], value["settings_revision"])
        if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in revisions):
            raise BackupManifestError("backup revisions are invalid")
        created_at = value["created_at"]
        if not isinstance(created_at, str) or not _is_utc(created_at):
            raise BackupManifestError("backup timestamp must be UTC")
        # Entries must be present, canonically ordered, and case-insensitively unique
        raw_entries = value["entries"]
        if not isinstance(raw_entries, list) or not raw_entries:
            raise BackupManifestError("backup manifest entries are required")
        entries = tuple(ManifestEntry.parse(item) for item in raw_entries)
        paths = [item.path for item in entries]
        folded = [item.casefold() for item in paths]
        if paths != sorted(paths, key=entry_path_key) or len(folded) != len(set(folded)):
            raise BackupManifestError("backup entries must use Windows-safe ordering and uniqueness")
        # Digest metadata must name SHA-256 and carry a valid digest
        algorithm = value["content_digest_algorithm"]
        digest = value["manifest_digest"]
        if algorithm != "SHA-256" or not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise BackupManifestError("backup digest metadata is invalid")
        semantic_digest = None
        runtime_profile = None
        # Version 2 carries the semantic profile digest and runtime identity
        if version == 2:
            semantic_digest = value["semantic_profile_digest"]
            runtime_profile = value["runtime_profile"]
            if not isinstance(semantic_digest, str) or SHA256.fullmatch(semantic_digest) is None:
                raise BackupManifestError("semantic profile digest is invalid")
            if not isinstance(runtime_profile, str) or not runtime_profile:
                raise BackupManifestError("runtime profile identity is invalid")
            try:
                runtime_profile = validate_relative_path(runtime_profile, "runtime_profile")
            except ProfileValidationError as error:
                raise BackupManifestError("runtime profile identity is invalid") from error
        return cls(
            backup_id=backup_id,
            profile_id=validate_profile_id(value["profile_id"]),
            profile_revision=revisions[0],
            settings_revision=revisions[1],
            created_at=created_at,
            entries=entries,
            manifest_digest=digest,
            schema_version=version,
            semantic_profile_digest=semantic_digest,
            runtime_profile=runtime_profile,
        )

    def unsigned_dict(self) -> dict[str, Any]:
        """Return the manifest fields covered by the manifest digest."""
        result = {
            "schema_version": self.schema_version,
            "backup_id": self.backup_id,
            "profile_id": self.profile_id,
            "profile_revision": self.profile_revision,
            "settings_revision": self.settings_revision,
            "created_at": self.created_at,
            "entries": [entry.to_dict() for entry in self.entries],
            "content_digest_algorithm": self.content_digest_algorithm,
        }
        # Version 2 signs the two extra profile identity fields
        if self.schema_version == 2:
            result["semantic_profile_digest"] = self.semantic_profile_digest
            result["runtime_profile"] = self.runtime_profile
        return result

    def signed(self) -> BackupManifest:
        """Return a copy carrying a freshly computed manifest digest."""
        return replace(self, manifest_digest=manifest_digest(self.unsigned_dict()))

    def to_dict(self) -> dict[str, Any]:
        """Return the complete persisted manifest object."""
        return {**self.unsigned_dict(), "manifest_digest": self.manifest_digest}


def manifest_digest(value: dict[str, Any]) -> str:
    """Return the canonical SHA-256 digest of a manifest mapping."""
    # Serialize with sorted keys and tight separators so writers agree
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def restore_compatibility(manifest: BackupManifest) -> tuple[str, str]:
    """Classify restore support separately from verified backup integrity."""
    # Legacy backups predate the profile-v2 runtime mapping
    if manifest.schema_version == 1:
        return (
            RESTORE_LEGACY,
            "This legacy backup cannot be restored with a profile-v2 context.",
        )
    # Entries outside the payload trees cannot be mapped yet
    if any(entry.path.split("/", 1)[0] not in {"payload", "runtime-profile"}
           for entry in manifest.entries):
        return (
            RESTORE_PENDING_RUNTIME,
            "This backup contains a target form that the current restore mapper does not support.",
        )
    return (RESTORE_COMPATIBLE, "This backup is compatible with the current restore mapper.")


def _is_utc(value: str) -> bool:
    """Return whether a timestamp text is an explicit zero-offset UTC instant."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    # Require an explicit timezone whose offset is exactly zero
    return parsed.tzinfo is not None and parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0
