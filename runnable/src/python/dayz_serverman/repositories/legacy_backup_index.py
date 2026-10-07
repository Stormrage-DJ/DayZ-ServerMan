"""Versioned manager-owned index for read-only legacy backup references."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..domain.models import RecordState, RecordUnavailable
from .json_store import StagingPolicy, VersionedJsonRepository
from .external_root import external_root_identity, validate_external_root
from .legacy_backup_proof import (
    backup_format, backup_path_identity, backup_reference_id, is_sha256,
    normalize_backup_relative, utc_timestamp,
)
from .legacy_source import LegacyInventory


# Verification states that a stored legacy entry may report
STATUSES = frozenset(("AVAILABLE", "MISSING", "CHANGED", "UNREADABLE"))


class LegacyBackupIndexError(ValueError):
    """Raised when a legacy backup index is malformed or inconsistent."""
    pass


@dataclass(frozen=True)
class LegacyBackupEntry:
    """One verified reference to an external legacy backup file."""

    reference_id: str
    relative_path: str
    size: int
    sha256: str
    format: str
    warnings: tuple[str, ...]
    status: str
    last_verified_at: str

    def fields(self) -> dict[str, object]:
        """Return the persisted field mapping for this entry."""
        return {
            "reference_id": self.reference_id, "relative_path": self.relative_path,
            "size": self.size, "sha256": self.sha256, "format": self.format,
            "warnings": list(self.warnings), "status": self.status,
            "last_verified_at": self.last_verified_at,
        }

    def public(self) -> dict[str, object]:
        """Return the public presentation of this entry."""
        return self.fields()


@dataclass(frozen=True)
class LegacyBackupIndex:
    """Manager-owned index of read-only external legacy backup references."""

    revision: int
    source_root: str
    source_root_identity: str
    inventory_digest: str
    verification_version: int
    last_verified_at: str
    entries: tuple[LegacyBackupEntry, ...]

    def fields(self) -> dict[str, object]:
        """Return the persisted field mapping for the index."""
        return {
            "source_root": self.source_root,
            "source_root_identity": self.source_root_identity,
            "inventory_digest": self.inventory_digest,
            "verification_version": self.verification_version,
            "last_verified_at": self.last_verified_at,
            "entries": [item.fields() for item in self.entries],
        }

    @property
    def digest(self) -> str:
        """Return the digest over the canonical field encoding."""
        # Encode with sorted keys and compact separators for a stable digest
        payload = json.dumps(self.fields(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def public(self) -> dict[str, object]:
        """Return the public presentation of the index."""
        # Expose entries with explicit reference-only and non-restorable flags
        return {
            "revision": self.revision, "index_digest": self.digest,
            "entries": [item.public() for item in self.entries],
            "external_reference_only": True, "restorable": False,
        }


class LegacyBackupIndexRepository:
    """Load and save the legacy backup index with revision control."""

    def __init__(self, path: Path, *, staging: StagingPolicy = StagingPolicy.OWNER) -> None:
        """Store the index path and its versioned JSON backing store with the session's staging-file rule."""
        self.path = path.resolve(strict=False)
        self._repository = VersionedJsonRepository(self.path, 1, staging=staging)

    def load_optional(self) -> LegacyBackupIndex | None:
        """Return the stored index, or None when none has been written."""
        inspection = self._repository.inspect()
        # Report no index when the record has not been created yet
        if inspection.state == RecordState.MISSING:
            return None
        # Refuse to load records that are not in the valid state
        if inspection.state != RecordState.VALID or inspection.document is None:
            raise RecordUnavailable(inspection)
        return _parse(inspection.document.revision, inspection.document.fields)

    def save(self, index: LegacyBackupIndex, expected_revision: int | None) -> LegacyBackupIndex:
        """Persist the index under revision control and return the stored form."""
        # Reparse the stored document so validation always applies
        document = self._repository.save(index.fields(), expected_revision)
        return _parse(document.revision, document.fields)

    def from_inventory(self, inventory: LegacyInventory) -> LegacyBackupIndex:
        """Build a fresh index from a verified legacy inventory."""
        # Require the inventory to know its backup source root
        if inventory.backup_root is None:
            raise LegacyBackupIndexError("legacy backup source root is unavailable")
        # Validate and canonicalize the external source root
        try:
            source_root = validate_external_root(
                str(inventory.backup_root), require_existing=True,
            )
        except ValueError as error:
            raise LegacyBackupIndexError("legacy backup source root is invalid") from error
        # Derive the root identity and the newest verification time (epoch when empty)
        source_identity = external_root_identity(source_root)
        verified_at = max(
            (item.verified_at for item in inventory.backups), default="1970-01-01T00:00:00Z",
        )
        # Convert every inventory entry into a stored entry
        entries = tuple(LegacyBackupEntry(
            item.reference_id, item.relative_path, item.size, item.sha256, item.format,
            item.warnings, "AVAILABLE", item.verified_at,
        ) for item in inventory.backups)
        # Assemble and validate the index before returning it
        index = LegacyBackupIndex(
            0, source_root, source_identity,
            _entry_inventory_digest(entries), 1,
            verified_at, entries,
        )
        _validate(index)
        return index


def _parse(revision: int, fields: Mapping[str, Any]) -> LegacyBackupIndex:
    """Parse stored fields into a validated index."""
    # Require exactly the expected top-level fields
    expected = {
        "source_root", "source_root_identity", "inventory_digest", "verification_version",
        "last_verified_at", "entries",
    }
    if set(fields) != expected or not isinstance(fields["entries"], list):
        raise LegacyBackupIndexError("legacy backup index fields are invalid")
    # Parse each entry, then validate the assembled index
    entries = tuple(_parse_entry(item) for item in fields["entries"])
    index = LegacyBackupIndex(
        revision, fields["source_root"], fields["source_root_identity"],
        fields["inventory_digest"], fields["verification_version"],
        fields["last_verified_at"], entries,
    )
    _validate(index)
    return index


def parse_legacy_backup_fields(
    revision: int, fields: Mapping[str, Any],
) -> LegacyBackupIndex:
    """Parse current-schema index fields for migration payload verification."""
    return _parse(revision, fields)


def _parse_entry(value: object) -> LegacyBackupEntry:
    """Parse one stored mapping into a legacy backup entry."""
    # Require exactly the expected entry fields
    fields = {
        "reference_id", "relative_path", "size", "sha256", "format", "warnings",
        "status", "last_verified_at",
    }
    if not isinstance(value, dict) or set(value) != fields or not isinstance(value["warnings"], list):
        raise LegacyBackupIndexError("legacy backup index entry is invalid")
    return LegacyBackupEntry(
        value["reference_id"], value["relative_path"], value["size"], value["sha256"],
        value["format"], tuple(value["warnings"]), value["status"], value["last_verified_at"],
    )


def _validate(index: LegacyBackupIndex) -> None:
    """Validate index metadata, entry uniqueness, and canonical ordering."""
    # Validate the source root and the verification timestamp
    try:
        canonical_root = validate_external_root(
            index.source_root, require_existing=False, require_canonical=True,
        )
        verified_at = utc_timestamp(index.last_verified_at)
    except ValueError as error:
        raise LegacyBackupIndexError("legacy backup index metadata is invalid") from error
    # Cross-check derived identities, the digest, and the schema version
    if (
        external_root_identity(canonical_root) != index.source_root_identity
        or not is_sha256(index.inventory_digest)
        or type(index.verification_version) is not int or index.verification_version != 1
        or verified_at != index.last_verified_at
    ):
        raise LegacyBackupIndexError("legacy backup index metadata is invalid")
    # Reject ambiguous paths and duplicate reference identifiers
    identities: set[str] = set()
    references: set[str] = set()
    for entry in index.entries:
        _validate_entry(entry, index.source_root_identity)
        identity = backup_path_identity(entry.relative_path)
        if identity in identities or entry.reference_id in references:
            raise LegacyBackupIndexError("legacy backup index entries are ambiguous")
        identities.add(identity)
        references.add(entry.reference_id)
    # Require the documented ordering by path identity
    expected_order = tuple(sorted(
        index.entries,
        key=lambda item: (backup_path_identity(item.relative_path), item.relative_path),
    ))
    if index.entries != expected_order:
        raise LegacyBackupIndexError("legacy backup index order is invalid")
    # Confirm the stored digest matches the entry inventory
    if index.inventory_digest != _entry_inventory_digest(index.entries):
        raise LegacyBackupIndexError("legacy backup index digest is invalid")


def _validate_entry(entry: LegacyBackupEntry, source_root_identity: str) -> None:
    """Validate one entry's fields and derived identity values."""
    # Validate the relative path and the verification timestamp
    try:
        relative = normalize_backup_relative(entry.relative_path)
        verified_at = utc_timestamp(entry.last_verified_at)
    except ValueError as error:
        raise LegacyBackupIndexError("legacy backup index entry fields are invalid") from error
    # Cross-check the stored fields against their derived values
    expected_format, expected_warnings = backup_format(relative)
    if (
        entry.reference_id != backup_reference_id(source_root_identity, relative)
        or not is_sha256(entry.sha256)
        or not isinstance(entry.size, int) or isinstance(entry.size, bool) or entry.size < 0
        or entry.format != expected_format or entry.warnings != expected_warnings
        or entry.status not in STATUSES or verified_at != entry.last_verified_at
    ):
        raise LegacyBackupIndexError("legacy backup index entry fields are invalid")


def _entry_inventory_digest(entries: tuple[LegacyBackupEntry, ...]) -> str:
    """Return the digest over the entry identity fields."""
    # Project each entry to its identity fields
    payload = [{
        "reference_id": item.reference_id, "relative_path": item.relative_path,
        "size": item.size, "sha256": item.sha256, "format": item.format,
    } for item in entries]
    # Hash the canonical JSON encoding
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
