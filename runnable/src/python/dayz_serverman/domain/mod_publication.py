"""Task 6.2 immutable publication and journal contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from .mod_publication_validation import (  # noqa: F401 - re-exported for existing importers
    IDENTIFIER,
    SHA256,
    PublicationValidationError,
    validate_intent,
    validate_publication_id,
)
from .workshop import CacheProof


# Publication and journal schema version
PUBLICATION_SCHEMA = 1


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
