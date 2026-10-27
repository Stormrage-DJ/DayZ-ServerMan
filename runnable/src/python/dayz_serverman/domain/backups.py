"""Versioned verified-backup manifest records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

# The entries, their path rules and the restore classification live in their own module (10.3);
# they are re-exported for existing importers
from .backup_entries import (  # noqa: F401
    RESTORE_COMPATIBLE, RESTORE_LEGACY, RESTORE_PENDING_RUNTIME, SHA256, BackupManifestError, ManifestEntry,
    entry_path_key, normalized_entry_path, restore_compatibility,
)
from .backup_editor_manifest import EDITOR_SCHEMA_VERSION, is_editor_entry, parse_editor_field
from .profiles import ProfileValidationError, validate_profile_id, validate_relative_path


# Newest manifest schema this build reads; a backup is written at schema 4 only with editor records (D18)
BACKUP_SCHEMA_VERSION = EDITOR_SCHEMA_VERSION
# Backup identifier: letters, digits, dots, underscores, or hyphens
BACKUP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


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
    schema_version: int = 2
    content_digest_algorithm: str = "SHA-256"
    semantic_profile_digest: str | None = None
    runtime_profile: str | None = None
    reconstruction: dict | None = None
    directories: tuple[str, ...] = ()
    # The `editor` field of schema 4: the carried association records and originals
    editor: dict | None = None

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
        # Only the published schema versions are accepted
        version = value.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool) or version not in (1, 2, 3, 4):
            raise BackupManifestError("backup manifest schema is unsupported")
        # Version 2 adds the semantic digest and runtime profile fields
        fields = common_fields if version == 1 else common_fields | {
            "semantic_profile_digest", "runtime_profile",
        }
        # Schema 4 has the schema 3 fields and the editor field
        if version >= 3:
            fields |= {"reconstruction", "directories"}
        if version == EDITOR_SCHEMA_VERSION:
            fields |= {"editor"}
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
        if version >= 2:
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
        # The editor prefix is accepted only with schema 4, and there only in the layout of its field
        editor = None
        if version == EDITOR_SCHEMA_VERSION:
            editor = parse_editor_field(value["editor"], value["profile_id"], entries)
        elif any(is_editor_entry(item.path) for item in entries):
            raise BackupManifestError("editor entries need manifest schema 4")
        reconstruction, directories = None, ()
        if version >= 3:
            from .backup_reconstruction import validate_directories, validate_reconstruction
            try:
                directories = validate_directories(value["directories"], entries, normalized_entry_path, entry_path_key)
                # The editor field, not the reconstruction metadata, accounts for the editor entries
                payload = tuple(item for item in entries if not is_editor_entry(item.path))
                reconstruction = validate_reconstruction(value["reconstruction"], value["profile_id"], semantic_digest, runtime_profile, payload, directories)
            except ValueError as error:
                raise BackupManifestError(str(error)) from error
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
            reconstruction=reconstruction,
            directories=directories,
            editor=editor,
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
        if self.schema_version >= 2:
            result["semantic_profile_digest"] = self.semantic_profile_digest
            result["runtime_profile"] = self.runtime_profile
        if self.schema_version >= 3:
            result["reconstruction"] = self.reconstruction
            result["directories"] = list(self.directories)
        # The manifest digest covers the editor field
        if self.schema_version == EDITOR_SCHEMA_VERSION:
            result["editor"] = self.editor
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


def _is_utc(value: str) -> bool:
    """Return whether a timestamp text is an explicit zero-offset UTC instant."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    # Require an explicit timezone whose offset is exactly zero
    return parsed.tzinfo is not None and parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0
