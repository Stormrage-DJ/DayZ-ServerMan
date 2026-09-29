"""Deterministic Workshop-only source and key inventory."""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections.abc import Callable
from pathlib import Path

from ..domain.mod_publication import (
    KeyCandidate,
    ManagedModSource,
    PublicationIntent,
    PublicationValidationError,
    validate_intent,
)
from ..domain.profiles import ProfileRecord
from ..domain.workshop import CacheProof, derive_required_items
from .backup_verification import is_reparse, sha256_file
from .workshop_cache import CacheVerificationError, WorkshopCacheVerifier, _has_alternate_stream
from .tree_metadata import TreeMetadataError, tree_metadata_digest


class PublicationInventoryError(RuntimeError):
    """Publication inventory failure carrying a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        """Store the failure code and forward the human-readable message."""
        self.code = code
        super().__init__(message)


def inventory_tree(root: Path) -> str:
    """Return a canonical digest over every path and file in a source tree."""
    # The root itself must be a plain directory without alternate streams
    if not root.is_dir() or is_reparse(root) or _has_alternate_stream(root):
        raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "source directory is unsafe")
    entries: list[tuple[object, ...]] = []
    identities: set[str] = set()
    # Walk without following links; each entry is validated before it is read
    for parent, directories, files in os.walk(root, followlinks=False):
        base = Path(parent)
        _safe_entry(base, directory=True)
        for name in directories:
            child = base / name
            _safe_entry(child, directory=True)
            relative = _relative_identity(child, root, identities)
            entries.append(("D", relative))
        for name in files:
            child = base / name
            _safe_entry(child, directory=False)
            relative = _relative_identity(child, root, identities)
            entries.append(("F", relative, child.stat().st_size, sha256_file(child)))
    # Sort deterministically so equal trees produce the same digest
    entries.sort(key=lambda value: (str(value[1]).casefold(), str(value[1]), value[0]))
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_publication_intent(
    *,
    publication_id: str,
    profile: ProfileRecord,
    settings_revision: int,
    dayz_root_identity: str,
    cache_root: Path,
    proofs: dict[str, CacheProof],
    checkpoint: Callable[[str, int], None] | None = None,
) -> PublicationIntent:
    """Assemble and sign the publication intent for the current mod set."""
    # Default checkpoint sink keeps callers free of optional wiring
    check = checkpoint or (lambda _phase, _index: None)
    required = derive_required_items(profile)
    required_ids = [item.workshop_id for item in required]
    # Proofs must cover exactly the required Workshop ids
    if set(proofs) != set(required_ids) or len(proofs) != len(required_ids):
        raise PublicationInventoryError("CACHE_VERIFICATION_FAILED", "cache proof set is incomplete")
    mods_by_id = {
        mod.source.workshop_id: mod for mod in profile.values.mods
        if mod.source.kind == "workshop"
    }
    sources: list[ManagedModSource] = []
    key_groups: dict[str, list[tuple[str, Path, str, int]]] = {}
    verifier = WorkshopCacheVerifier(cache_root)
    # Verify each required item against its recorded proof before discovery
    for item in required:
        check("DISCOVER_ITEM", item.order_index)
        expected = proofs[item.workshop_id]
        try:
            manifest_digest = verifier.manifest_record_digest(item.workshop_id)
        except CacheVerificationError as error:
            raise PublicationInventoryError("CACHE_VERIFICATION_FAILED", str(error)) from error
        if manifest_digest != expected.manifest_record_digest:
            raise PublicationInventoryError("CACHE_VERIFICATION_FAILED", "cache proof changed")
        source = cache_root / item.workshop_id
        try:
            metadata_digest = tree_metadata_digest(source)
        except TreeMetadataError as error:
            raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", str(error)) from error
        if (expected.metadata_inventory_digest is None
                or metadata_digest != expected.metadata_inventory_digest):
            raise PublicationInventoryError("PUBLICATION_PREVIEW_STALE", "cache metadata changed")
        mod = mods_by_id[item.workshop_id]
        # Record the verified source and collect its bikey candidates
        sources.append(ManagedModSource(
            item.workshop_id, item.order_index, mod.directory, str(source), expected,
            expected.content_inventory_digest,
            expected.verification_kind == "APPLIED_STATE",
        ))
        # Collect bikey candidates from the item key folder when present
        keys = source / "keys"
        if keys.exists():
            if not keys.is_dir() or is_reparse(keys):
                raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "mod key directory is unsafe")
            for key in sorted(keys.iterdir(), key=lambda path: (_key_identity(path.name), path.name)):
                if is_reparse(key) or _has_alternate_stream(key):
                    raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "mod key entry is unsafe")
                if key.is_dir() or (key.is_file() and key.suffix.casefold() != ".bikey"):
                    continue
                if not key.is_file():
                    raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "mod key entry is unsafe")
                identity = _key_identity(key.name)
                key_groups.setdefault(identity, []).append(
                    (item.workshop_id, key, sha256_file(key), key.stat().st_size),
                )
        # Re-check the manifest afterwards so a concurrent change is caught
        check("CACHE_PROOF_RECHECK", item.order_index)
        if verifier.manifest_record_digest(item.workshop_id) != expected.manifest_record_digest:
            raise PublicationInventoryError("CACHE_VERIFICATION_FAILED", "cache changed during discovery")
    # Coalesce duplicate key material into one candidate per identity
    candidates = tuple(_coalesce_key(identity, values) for identity, values in sorted(key_groups.items()))
    value = PublicationIntent(
        publication_id, profile.values.profile_id, profile.revision, profile.semantic_digest,
        settings_revision, dayz_root_identity, tuple(sources), candidates,
    ).signed()
    # The signed intent must still satisfy the publication contract
    try:
        validate_intent(value)
    except PublicationValidationError as error:
        raise PublicationInventoryError("PUBLICATION_TARGET_INVALID", str(error)) from error
    return value


def _coalesce_key(identity: str, values: list[tuple[str, Path, str, int]]) -> KeyCandidate:
    """Merge duplicate key material into one candidate or fail on collision."""
    proofs = {(item[2], item[3]) for item in values}
    # Duplicate keys must agree on content and size to be safe to publish
    if len(proofs) != 1:
        raise PublicationInventoryError("KEY_COLLISION", f"key collision: {identity}")
    first = values[0]
    return KeyCandidate(
        unicodedata.normalize("NFC", first[1].name), first[2], first[3],
        tuple(item[0] for item in values), str(first[1]),
    )


def _key_identity(name: str) -> str:
    """Normalize a key filename and reject unsafe or invisible characters."""
    normalized = unicodedata.normalize("NFC", name)
    # Control characters, stream separators, and empty names are unsafe
    if not normalized or ":" in normalized or any(ord(mark) < 32 for mark in normalized):
        raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "mod key name is unsafe")
    return normalized.casefold()


def _safe_entry(path: Path, *, directory: bool) -> None:
    """Reject unsafe entries and entries whose type changed during the walk."""
    # Alternate streams and reparse points could hide or redirect content
    if ":" in path.name or is_reparse(path) or _has_alternate_stream(path):
        raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "source tree contains an unsafe entry")
    if directory and not path.is_dir():
        raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "source directory changed")
    if not directory and not path.is_file():
        raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "source file changed")


def _relative_identity(path: Path, root: Path, identities: set[str]) -> str:
    """Return the normalized relative path while rejecting collisions."""
    try:
        # os.walk yields lexical descendants of the validated non-reparse root.
        # Keep that spelling because Windows can expand only a child from 8.3 form.
        relative = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
    except ValueError as error:
        raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "source escapes cache item") from error
    identity = relative.casefold()
    if identity in identities:
        raise PublicationInventoryError("MANAGED_MOD_SOURCE_INVALID", "source has a path collision")
    identities.add(identity)
    return relative
