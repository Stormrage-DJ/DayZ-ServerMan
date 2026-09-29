"""Task 6.2 immutable publication and journal contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .profiles import validate_profile_id, validate_relative_path
from .workshop import CacheProof


# Lowercase SHA-256 hex digest
SHA256 = re.compile(r"[0-9a-f]{64}")
# Stable identifier: lowercase letters, digits, and hyphens
IDENTIFIER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
# Publication and journal schema version
PUBLICATION_SCHEMA = 1
class PublicationValidationError(ValueError):
    """Raised when a publication intent or journal breaks its contract."""
    pass
class TargetRole(str, Enum):
    """Filesystem role a publication group manages."""

    MANAGED_MOD_DIRECTORY = "MANAGED_MOD_DIRECTORY"
    SERVER_KEYS_DIRECTORY = "SERVER_KEYS_DIRECTORY"


class PublicationPhase(str, Enum):
    """Durable phase of a publication operation."""

    PREPARED = "PREPARED"
    PUBLISHING = "PUBLISHING"
    COMPENSATING = "COMPENSATING"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class GroupState(str, Enum):
    """Per-group progress state inside a publication journal."""

    UNCHANGED_VERIFIED = "UNCHANGED_VERIFIED"
    STAGED = "STAGED"
    PRIOR_MOVED = "PRIOR_MOVED"
    OUTPUT_PUBLISHED = "OUTPUT_PUBLISHED"
    OUTPUT_VERIFIED = "OUTPUT_VERIFIED"
    PRIOR_RESTORED = "PRIOR_RESTORED"


@dataclass(frozen=True)
class ManagedModSource:
    """One managed mod directory published from the Workshop cache."""

    workshop_id: str
    order_index: int
    target_relative: str
    source_path: str
    cache_proof: CacheProof
    output_digest: str
    target_current: bool = False

    def public_dict(self) -> dict[str, object]:
        """Return the public source fields bound into the intent fingerprint."""
        return {
            "workshop_id": self.workshop_id,
            "order_index": self.order_index,
            "target_relative": self.target_relative,
            "cache_proof": self.cache_proof.to_dict(),
            "output_digest": self.output_digest,
            "target_current": self.target_current,
        }


@dataclass(frozen=True)
class KeyCandidate:
    """One verified server key file proposed for publication."""

    filename: str
    content_digest: str
    size: int
    source_workshop_ids: tuple[str, ...]
    source_path: str

    def public_dict(self) -> dict[str, object]:
        """Return the public key fields bound into the intent fingerprint."""
        return {
            "filename": self.filename,
            "content_digest": self.content_digest,
            "size": self.size,
            "source_workshop_ids": list(self.source_workshop_ids),
        }


@dataclass(frozen=True)
class PublicationIntent:
    """Immutable publication intent signed with a fingerprint."""

    publication_id: str
    profile_id: str
    profile_revision: int
    semantic_profile_digest: str
    settings_revision: int
    dayz_root_identity: str
    managed_sources: tuple[ManagedModSource, ...]
    keys: tuple[KeyCandidate, ...]
    fingerprint: str = ""

    def body(self) -> dict[str, object]:
        """Return the intent fields covered by the fingerprint."""
        return {
            "schema_version": PUBLICATION_SCHEMA,
            "profile_id": self.profile_id,
            "profile_revision": self.profile_revision,
            "semantic_profile_digest": self.semantic_profile_digest,
            "settings_revision": self.settings_revision,
            "dayz_root_identity": self.dayz_root_identity,
            "managed_sources": [item.public_dict() for item in self.managed_sources],
            "keys": [item.public_dict() for item in self.keys],
        }

    def signed(self) -> "PublicationIntent":
        """Return a copy carrying the fingerprint of its body."""
        digest = canonical_digest(self.body())
        return PublicationIntent(**{**vars(self), "fingerprint": digest})


@dataclass
class PublicationGroup:
    """Durable per-target state of one publication output."""

    role: TargetRole
    ordinal: int
    target_relative: str
    stage_name: str
    recovery_name: str
    prior_existed: bool
    prior_digest: str | None
    output_digest: str
    state: GroupState = GroupState.STAGED

    def to_dict(self) -> dict[str, object]:
        """Return the group as its journal JSON object."""
        value = vars(self).copy()
        value["role"] = self.role.value
        value["state"] = self.state.value
        return value


@dataclass
class PublicationJournal:
    """Durable publication journal with phase and per-group state."""

    publication_id: str
    intent_fingerprint: str
    phase: PublicationPhase
    publication_started: bool
    committed: bool
    resolved: bool
    result: str | None
    groups: list[PublicationGroup] = field(default_factory=list)
    schema_version: int = PUBLICATION_SCHEMA
    publication_fingerprint: str = ""
    updated_at: str = ""
    authority_intent: PublicationIntent | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        """Return the journal as its persisted JSON object."""
        return {
            "schema_version": self.schema_version,
            "publication_id": self.publication_id,
            "intent_fingerprint": self.intent_fingerprint,
            "publication_fingerprint": self.publication_fingerprint,
            "updated_at": self.updated_at,
            "phase": self.phase.value,
            "publication_started": self.publication_started,
            "committed": self.committed,
            "resolved": self.resolved,
            "result": self.result,
            "groups": [group.to_dict() for group in self.groups],
        }


def publication_fingerprint(intent_fingerprint: str, groups: list[PublicationGroup]) -> str:
    """Return the fingerprint of an intent bound to its journal groups."""
    bindings = []
    for group in groups:
        value = group.to_dict()
        # Group progress is not part of the signed bindings
        value.pop("state")
        bindings.append(value)
    return canonical_digest({"intent_fingerprint": intent_fingerprint, "targets": bindings})


def canonical_digest(value: object) -> str:
    """Return the canonical SHA-256 digest of a JSON-compatible value."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_intent(intent: PublicationIntent) -> None:
    """Validate a publication intent and its fingerprint."""
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
        if source.cache_proof.verification_kind not in ("FULL_CONTENT", "APPLIED_STATE"):
            raise PublicationValidationError("cache proof kind is invalid")
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


def journal_state_is_legal(journal: PublicationJournal) -> bool:
    """Return whether a journal phase and its group states are consistent."""
    states = [group.state for group in journal.groups]
    # Ignore groups the publication never modified
    changed = [state for state in states if state != GroupState.UNCHANGED_VERIFIED]
    # Flags capture start, commit, resolution, and terminal result
    flags = (journal.publication_started, journal.committed, journal.resolved, journal.result)
    if journal.phase == PublicationPhase.PREPARED:
        return flags == (False, False, False, None) and all(
            state in (GroupState.STAGED, GroupState.UNCHANGED_VERIFIED) for state in states)
    if journal.phase == PublicationPhase.PUBLISHING:
        return flags == (True, False, False, None) and _transition_grammar(changed, False)
    if journal.phase == PublicationPhase.COMPENSATING:
        return flags == (True, False, False, None) and _transition_grammar(changed, True)
    if journal.phase == PublicationPhase.COMMITTED:
        valid = flags in ((True, True, False, None), (True, True, True, "COMMITTED"))
        return valid and all(state in (
            GroupState.OUTPUT_VERIFIED, GroupState.UNCHANGED_VERIFIED,
        ) for state in states)
    if journal.phase == PublicationPhase.ROLLED_BACK:
        valid = flags in ((True, False, False, None), (True, False, True, "ROLLED_BACK"))
        split = next((i for i, state in enumerate(changed) if state == GroupState.STAGED), len(changed))
        return valid and all(state == GroupState.PRIOR_RESTORED for state in changed[:split]) and all(
            state == GroupState.STAGED for state in changed[split:]
        )
    return journal.phase == PublicationPhase.RECOVERY_REQUIRED and flags == (
        True, False, False, "RECOVERY_REQUIRED"
    )


def _transition_grammar(states: list[GroupState], compensating: bool) -> bool:
    """Return whether group states follow the permitted transition grammar."""
    index = 0
    # Verified outputs may only form a leading run
    while index < len(states) and states[index] == GroupState.OUTPUT_VERIFIED:
        index += 1
    # At most one group may sit mid-transition
    if index < len(states) and states[index] in (
        GroupState.PRIOR_MOVED, GroupState.OUTPUT_PUBLISHED,
    ):
        index += 1
    # Compensation may additionally restore a leading run
    if compensating:
        while index < len(states) and states[index] == GroupState.PRIOR_RESTORED:
            index += 1
    # Everything after the transition window must still be staged
    return all(state == GroupState.STAGED for state in states[index:])


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
