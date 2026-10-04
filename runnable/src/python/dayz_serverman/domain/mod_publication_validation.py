"""Validation of publication intents and identifiers of the Task 6.2 contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .profiles import validate_profile_id, validate_relative_path

if TYPE_CHECKING:
    from .mod_publication import PublicationIntent


# Lowercase SHA-256 hex digest
SHA256 = re.compile(r"[0-9a-f]{64}")
# Stable identifier: lowercase letters, digits, and hyphens
IDENTIFIER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")


class PublicationValidationError(ValueError):
    """Raised when a publication intent or journal breaks its contract."""
    pass


def validate_intent(intent: PublicationIntent) -> None:
    """Validate a publication intent and its fingerprint."""
    # Imported here because the contract module re-exports this validation
    from .mod_publication import canonical_digest

    # Validate identity, revisions, and digests first
    validate_profile_id(intent.profile_id)
    _identifier(intent.publication_id, "publication identifier")
    _revision(intent.profile_revision, "profile revision")
    _revision(intent.settings_revision, "settings revision")
    _digest(intent.semantic_profile_digest, "profile digest")
    _digest(intent.dayz_root_identity, "DayZ root identity")
    # Managed sources must be ordered zero..n-1 without gaps
    orders = [source.order_index for source in intent.managed_sources]
    if any(not isinstance(value, int) or isinstance(value, bool) for value in orders):
        raise PublicationValidationError("managed source order is invalid")
    if orders != list(range(len(orders))):
        raise PublicationValidationError("managed source order is invalid")
    targets = []
    # Validate each managed target, digest, and cache proof
    for source in intent.managed_sources:
        targets.append(_relative(source.target_relative, "managed target"))
        _digest(source.output_digest, "managed output digest")
        if not Path(source.source_path).is_absolute():
            raise PublicationValidationError("managed source path is invalid")
        if source.cache_proof.workshop_id != source.workshop_id:
            raise PublicationValidationError("cache proof item does not match source")
        _digest(source.cache_proof.manifest_record_digest, "cache manifest digest")
        _digest(source.cache_proof.content_inventory_digest, "cache inventory digest")
        _digest(source.cache_proof.metadata_inventory_digest, "cache metadata digest")
        if source.cache_proof.verification_kind not in (
            "FULL_CONTENT", "STORED_SOURCE", "APPLIED_STATE",
        ):
            raise PublicationValidationError("cache proof kind is invalid")
        # A stored source proof makes no statement about the target
        if (source.cache_proof.verification_kind == "STORED_SOURCE"
                and source.cache_proof.target_metadata_digest is not None):
            raise PublicationValidationError("stored source proof carries a target digest")
        if source.target_current:
            _digest(source.cache_proof.target_metadata_digest, "target metadata digest")
        for count in (source.cache_proof.regular_file_count, source.cache_proof.total_regular_bytes):
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise PublicationValidationError("cache proof count is invalid")
    # Managed targets must stay unique under Windows case folding
    folded = [value.casefold() for value in targets]
    if len(folded) != len(set(folded)):
        raise PublicationValidationError("managed targets are not unique")
    # Key candidates must be canonically ordered and unique
    key_ids = [key.filename.casefold() for key in intent.keys]
    if key_ids != sorted(key_ids) or len(key_ids) != len(set(key_ids)):
        raise PublicationValidationError("key candidates are not canonical and unique")
    # Validate each key filename, digest, and source
    for key in intent.keys:
        if (
            not key.filename.casefold().endswith(".bikey")
            or any(mark in key.filename for mark in ("/", "\\", ":"))
        ):
            raise PublicationValidationError("key filename is invalid")
        _digest(key.content_digest, "key digest")
        if not isinstance(key.size, int) or isinstance(key.size, bool) or key.size < 0:
            raise PublicationValidationError("key size is invalid")
        if not key.source_workshop_ids or not Path(key.source_path).is_absolute():
            raise PublicationValidationError("key source is invalid")
    # A present fingerprint must match the recomputed body
    expected = canonical_digest(intent.body())
    if intent.fingerprint and intent.fingerprint != expected:
        raise PublicationValidationError("publication fingerprint does not match")


def validate_publication_id(value: object) -> str:
    """Return a validated publication identifier."""
    _identifier(value, "publication identifier")
    assert isinstance(value, str)
    return value


def _revision(value: object, name: str) -> None:
    """Reject a revision that is not a non-negative integer."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PublicationValidationError(f"{name} is invalid")


def _digest(value: object, name: str) -> None:
    """Reject a value that is not a lowercase SHA-256 digest."""
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise PublicationValidationError(f"{name} is invalid")


def _identifier(value: object, name: str) -> None:
    """Reject a value that is not a stable identifier."""
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise PublicationValidationError(f"{name} is invalid")


def _relative(value: object, name: str) -> str:
    """Return a validated relative path or raise a publication error."""
    # Translate profile path errors into publication errors
    try:
        normalized = validate_relative_path(value, name)
    except ValueError as error:
        raise PublicationValidationError(str(error)) from error
    assert normalized is not None
    return normalized
