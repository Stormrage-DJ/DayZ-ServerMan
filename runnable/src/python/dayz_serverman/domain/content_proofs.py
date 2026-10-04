"""Profile-independent content proof records of Workshop sources and server-folder copies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .profiles import WORKSHOP_ID

# Store limits; at each write the oldest records beyond them are dropped
MAX_SOURCE_RECORDS = 512
MAX_TARGET_RECORDS = 1024
# Lowercase SHA-256 hex digest
_SHA256 = re.compile(r"[0-9a-f]{64}")
# Exact field sets of the persisted records
_SOURCE_FIELDS = {
    "cache_root_identity", "installed_manifest_id", "time_updated",
    "content_inventory_digest", "regular_file_count", "total_regular_bytes",
    "metadata_inventory_digest", "verified_at",
}
_TARGET_FIELDS = {
    "workshop_id", "installed_manifest_id", "content_inventory_digest",
    "target_metadata_digest", "verified_at",
}
# Write points that may give a target record its fingerprint; each one hashed the tree in full:
# a copied and verified group, the recorder's own hash, the pre-start check, and "Verify files"
TARGET_BASES = frozenset(("COPIED", "HASHED", "PRESTART", "VERIFIED"))


def target_directory_key(directory: str) -> str:
    """Return the store key of a target directory: the exact text in lower case.

    Two names share a key only when NTFS treats them as one directory. NTFS
    ignores letter case and nothing else, so the key applies no Unicode
    normalization and no case folding: "@Strasse" and "@Straße", or a composed
    and a decomposed spelling, are different directories and keep different
    records. Every side of a target comparison uses this function.
    """
    return directory.lower()


@dataclass(frozen=True)
class SourceProofRecord:
    """Proof that a Workshop item tree had one content digest at one fingerprint."""

    cache_root_identity: str
    installed_manifest_id: str
    time_updated: int
    content_inventory_digest: str
    regular_file_count: int
    total_regular_bytes: int
    metadata_inventory_digest: str
    verified_at: str

    def to_dict(self) -> dict[str, object]:
        """Return the record as its persisted JSON object."""
        return self.__dict__.copy()


@dataclass(frozen=True)
class TargetProofRecord:
    """Proof that a server-folder copy had one content digest at one fingerprint."""

    workshop_id: str
    installed_manifest_id: str
    content_inventory_digest: str
    target_metadata_digest: str
    verified_at: str
    # Write point that hashed the tree; None for a record of an earlier version
    basis: str | None = None

    @property
    def justified(self) -> bool:
        """Return whether a known write point hashed the tree for this fingerprint."""
        return self.basis in TARGET_BASES

    def to_dict(self) -> dict[str, object]:
        """Return the record as its persisted JSON object; a missing basis is left out."""
        value = self.__dict__.copy()
        if self.basis is None:
            del value["basis"]
        return value


@dataclass(frozen=True)
class ProofDocument:
    """All well-formed records of the store; targets are keyed by (root identity, directory key)."""

    sources: dict[str, SourceProofRecord] = field(default_factory=dict)
    targets: dict[tuple[str, str], TargetProofRecord] = field(default_factory=dict)


def parse_source_record(workshop_id: object, raw: Any) -> SourceProofRecord:
    """Return one validated source record; raise ValueError when any part is malformed."""
    if not isinstance(workshop_id, str) or WORKSHOP_ID.fullmatch(workshop_id) is None:
        raise ValueError("source proof id is invalid")
    if not isinstance(raw, dict) or set(raw) != _SOURCE_FIELDS:
        raise ValueError("source proof has the wrong shape")
    record = SourceProofRecord(**raw)
    # Digests are lowercase SHA-256, the manifest id is decimal text
    _require_digests(
        record.cache_root_identity, record.content_inventory_digest,
        record.metadata_inventory_digest,
    )
    _require_manifest_id(record.installed_manifest_id)
    if not _is_integer(record.time_updated) or record.time_updated < 0:
        raise ValueError("source proof time is invalid")
    # A proof always covers content, so both counts are positive
    for count in (record.regular_file_count, record.total_regular_bytes):
        if not _is_integer(count) or count <= 0:
            raise ValueError("source proof count is invalid")
    verified_time(record.verified_at)
    return record


def parse_target_record(root_identity: object, directory: object, raw: Any) -> TargetProofRecord:
    """Return one validated target record; raise ValueError when any part is malformed."""
    _require_digests(root_identity)
    # The directory key must already be in its lower-case form
    if not isinstance(directory, str) or not directory or target_directory_key(directory) != directory:
        raise ValueError("target proof directory key is invalid")
    # The basis is the one additive field; a record of an earlier version has none
    if not isinstance(raw, dict) or set(raw) - {"basis"} != _TARGET_FIELDS:
        raise ValueError("target proof has the wrong shape")
    record = TargetProofRecord(**raw)
    if "basis" in raw and not record.justified:
        raise ValueError("target proof basis is invalid")
    if not isinstance(record.workshop_id, str) or WORKSHOP_ID.fullmatch(record.workshop_id) is None:
        raise ValueError("target proof id is invalid")
    _require_manifest_id(record.installed_manifest_id)
    _require_digests(record.content_inventory_digest, record.target_metadata_digest)
    verified_time(record.verified_at)
    return record


def verified_time(value: object) -> datetime:
    """Return the verification time; raise ValueError unless it is ISO-8601 UTC text."""
    if not isinstance(value, str):
        raise ValueError("proof timestamp is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Require the explicit UTC offset, as the publication gate does
    if parsed.tzinfo != UTC:
        raise ValueError("proof timestamp is not UTC")
    return parsed


def _require_digests(*values: object) -> None:
    """Raise ValueError unless every value is a lowercase SHA-256 digest."""
    if any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in values):
        raise ValueError("proof digest is invalid")


def _require_manifest_id(value: object) -> None:
    """Raise ValueError unless the value is decimal text."""
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("proof manifest id is invalid")


def _is_integer(value: object) -> bool:
    """Return whether the value is an integer and not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)
