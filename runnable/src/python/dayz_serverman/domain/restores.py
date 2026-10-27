"""Immutable restore preview and durable journal records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any


# Current journal schema version; older files support fewer fields
RESTORE_JOURNAL_SCHEMA = 3
# Journal schema of a restore that also writes editor records: each group names its target class (D19, 10.10)
EDITOR_JOURNAL_SCHEMA = 4


def canonical_digest(value: object) -> str:
    """Return the canonical SHA-256 digest of a JSON-compatible value."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RestoreTarget:
    """One archived entry scheduled for restore into the DayZ root."""

    entry_path: str
    target_relative: str
    action: str
    current_digest: str | None
    snapshot_digest: str
    size: int

    def to_dict(self) -> dict[str, object]:
        """Return the target as its journal JSON object."""
        return {
            "entry_path": self.entry_path,
            "target_relative": self.target_relative,
            # Classify the target by its archive entry prefix
            "target_kind": (
                "RUNTIME_PROFILE" if self.entry_path.startswith("runtime-profile/")
                else "DAYZ_CONFIGURATION"
            ),
            "action": self.action,
            "current_digest": self.current_digest,
            "snapshot_digest": self.snapshot_digest,
            "size": self.size,
        }


@dataclass(frozen=True)
class RestorePreview:
    """Immutable restore preview signed with a content fingerprint."""

    backup_id: str
    created_at: str
    profile_id: str
    profile_revision: int
    settings_revision: int
    manifest_digest: str
    targets: tuple[RestoreTarget, ...]
    fingerprint: str = ""
    # The editor records that the restore reads, the D5 row of each and the skipped parts (10.10); None without
    # an editor part. `editor_notes` holds the Product Owner sentences of that part, in review order.
    editor: dict[str, Any] | None = None
    editor_notes: tuple[str, ...] = ()

    def body(self) -> dict[str, Any]:
        """Return the preview fields covered by the fingerprint."""
        body = {
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "profile_id": self.profile_id,
            "profile_revision": self.profile_revision,
            "settings_revision": self.settings_revision,
            "manifest_digest": self.manifest_digest,
            "targets": [item.to_dict() for item in self.targets],
        }
        # Only a restore with editor records adds the field, so other previews keep their fingerprint
        if self.editor is not None:
            body["editor"], body["editor_notes"] = self.editor, list(self.editor_notes)
        return body

    def signed(self) -> RestorePreview:
        """Return a copy carrying the fingerprint of its body."""
        return replace(self, fingerprint=canonical_digest(self.body()))

    def to_dict(self) -> dict[str, Any]:
        """Return the complete preview object with its fingerprint."""
        return {**self.body(), "fingerprint": self.fingerprint}


@dataclass
class RestoreGroup:
    """Durable per-target progress of one restore publication.

    A group of the DayZ root names its archive entry. A group of the manager root (schema 4) names its target
    in `entry_path`, relative to the manager root. Such a group can also require absence (`new_exists` false).
    """

    entry_path: str
    target_path: str
    staging_path: str
    recovery_path: str | None
    old_existed: bool
    old_digest: str | None
    # None only when the file must end absent
    new_digest: str | None
    state: str = "READY"
    created_ancestors: tuple[str, ...] = ()
    target_class: str = "dayz"
    new_exists: bool = True

    def to_dict(self) -> dict[str, object]:
        """Return the group as its journal JSON object."""
        value = dict(vars(self))
        # Store ancestor paths as a JSON list for persistence
        value["created_ancestors"] = list(self.created_ancestors)
        return value


@dataclass
class RestoreJournal:
    """Durable restore journal recording phases and per-group state."""

    operation_id: str
    backup_id: str
    profile_id: str
    manifest_digest: str
    phase: str
    publication_started: bool
    committed: bool
    resolved: bool
    groups: list[RestoreGroup]
    result: str | None = None
    schema_version: int = RESTORE_JOURNAL_SCHEMA
    runtime_profile: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the journal as its persisted JSON object."""
        result = {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "backup_id": self.backup_id,
            "profile_id": self.profile_id,
            "manifest_digest": self.manifest_digest,
            "phase": self.phase,
            "publication_started": self.publication_started,
            "committed": self.committed,
            "resolved": self.resolved,
            "result": self.result,
            "groups": [group.to_dict() for group in self.groups],
        }
        # Older schemas never wrote ancestor tracking, and only schema 4 names the target class
        for group in result["groups"]:
            if self.schema_version < 3:
                group.pop("created_ancestors", None)
            if self.schema_version < EDITOR_JOURNAL_SCHEMA:
                group.pop("target_class", None)
                group.pop("new_exists", None)
        # Runtime profile identity exists from schema 2 onward
        if self.schema_version >= 2:
            result["runtime_profile"] = self.runtime_profile
        return result
