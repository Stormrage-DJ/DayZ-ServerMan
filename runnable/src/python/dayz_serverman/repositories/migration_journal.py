"""Durable versioned journal for legacy-import publication."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


# Digests are 64-character lowercase hex values
SHA256 = re.compile(r"[0-9a-f]{64}")
# Migration identifiers are 32-character lowercase hex values
MIGRATION_ID = re.compile(r"[0-9a-f]{32}")
# Every state a journal document may record
JOURNAL_STATES = frozenset((
    "PREPARED", "PUBLISHING", "COMPENSATING", "COMMITTED", "ROLLED_BACK",
    "RECOVERY_REQUIRED",
))
# Every state a single destination may record
DESTINATION_STATES = frozenset(("PLANNED", "PUBLISHED", "RESTORED"))


class MigrationJournalError(RuntimeError):
    """Raised when a journal document is invalid or a journal write fails."""
    pass


class MigrationJournalRepository:
    """Persist and validate versioned migration journal documents."""

    def __init__(self, root: Path) -> None:
        """Store the resolved journal root directory."""
        self.root = root.resolve(strict=False)

    def create(self, migration_id: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        """Create a revision zero journal document and persist it."""
        # Build the initial document envelope at revision zero
        document = {"schema_version": 1, "revision": 0, "migration_id": migration_id, **fields}
        # Validate before writing so an invalid document leaves no file
        self._validate(document)
        # Create-only write so an existing journal cannot be replaced
        self._write(self.path(migration_id), document, create=True)
        return document

    def update(self, document: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
        """Apply changes and persist them behind a revision check."""
        # Reload the persisted journal so stale callers cannot overwrite newer state
        current = self.read(self.path(str(document["migration_id"])))
        if current["revision"] != document["revision"]:
            raise MigrationJournalError("migration journal revision changed")
        # Merge the changes and advance the revision counter
        updated = {**current, **changes, "revision": current["revision"] + 1}
        self._validate(updated)
        self._write(self.path(updated["migration_id"]), updated, create=False)
        return updated

    def read(self, path: Path) -> dict[str, Any]:
        """Load and validate a journal document from disk."""
        # Read strict UTF-8 JSON so a corrupt journal fails loudly
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise MigrationJournalError("migration journal is unreadable") from error
        self._validate(value)
        return value

    def active_paths(self) -> tuple[Path, ...]:
        """Return active journal paths in deterministic name order."""
        if not self.root.exists():
            return ()
        return tuple(sorted(self.root.glob("*.json"), key=lambda item: item.name))

    def retired_paths(self) -> tuple[Path, ...]:
        """Return retired journal paths in deterministic name order."""
        if not self.root.exists():
            return ()
        return tuple(sorted(self.root.glob("*.retired"), key=lambda item: item.name))

    def retire(self, document: Mapping[str, Any]) -> Path:
        """Move the active journal file to its retired name and return it."""
        source = self.path(str(document["migration_id"]))
        retired = self.retired_path(str(document["migration_id"]))
        # Rename so a crash leaves either the active or the retired name
        os.replace(source, retired)
        return retired

    def finish_retired(self, migration_id: str) -> None:
        """Delete the retired journal file once recovery is complete."""
        # A missing file is fine; retirement must stay idempotent
        self.retired_path(migration_id).unlink(missing_ok=True)

    def path(self, migration_id: str) -> Path:
        """Return the active journal path for a migration identifier."""
        return self.root / f"{migration_id}.json"

    def retired_path(self, migration_id: str) -> Path:
        """Return the retired journal path for a migration identifier."""
        return self.root / f"{migration_id}.retired"

    def _write(self, path: Path, document: Mapping[str, Any], *, create: bool) -> None:
        """Write a journal document atomically, optionally refusing an existing file."""
        # Ensure the journal root exists before staging the write
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        # Serialize deterministically so journal bytes stay stable
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                # Fsync before the swap so a crash cannot leave a partial journal
                os.fsync(stream.fileno())
            if create and path.exists():
                raise MigrationJournalError("migration journal already exists")
            # Swap the complete file into place
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate(value: object) -> None:
        """Validate the full journal shape and its state consistency."""
        # Require the exact field set so unknown keys cannot hide
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "revision", "migration_id", "source_digest",
            "preview_fingerprint", "state", "publication_started", "committed",
            "resolved", "result", "destinations",
        }:
            raise MigrationJournalError("migration journal fields are invalid")
        # Accept only the supported schema version at exact int type
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise MigrationJournalError("migration journal schema is unsupported")
        # Revisions must be non-negative plain integers
        if type(value["revision"]) is not int or value["revision"] < 0:
            raise MigrationJournalError("migration journal revision is invalid")
        # The identifier must be a 32-character lowercase hex value
        if not isinstance(value["migration_id"], str) or not MIGRATION_ID.fullmatch(value["migration_id"]):
            raise MigrationJournalError("migration journal identifier is invalid")
        # Both digest fields must be lowercase 64-character hex
        for field in ("source_digest", "preview_fingerprint"):
            if not isinstance(value[field], str) or not SHA256.fullmatch(value[field]):
                raise MigrationJournalError("migration journal digest is invalid")
        state = value["state"]
        # The state must belong to the journal state machine
        if state not in JOURNAL_STATES:
            raise MigrationJournalError("migration journal state is invalid")
        for field in ("publication_started", "committed", "resolved"):
            if type(value[field]) is not bool:
                raise MigrationJournalError("migration journal flag is invalid")
        # Cross-check the flags against the recorded state
        _validate_state(value)
        destinations = value["destinations"]
        if not isinstance(destinations, list) or not destinations:
            raise MigrationJournalError("migration journal destinations are invalid")
        # Reject duplicate targets under case-insensitive comparison
        identities: set[str] = set()
        for destination in destinations:
            _validate_destination(destination)
            identity = destination["target_relative"].casefold()
            if identity in identities:
                raise MigrationJournalError("migration journal repeats a destination")
            identities.add(identity)
        # Fold destination states so terminal states stay consistent
        destination_states = {item["state"] for item in destinations}
        expected = {
            "PREPARED": {"PLANNED"},
            "COMMITTED": {"PUBLISHED"},
            "ROLLED_BACK": {"RESTORED"},
        }.get(state)
        if expected is not None and destination_states != expected:
            raise MigrationJournalError("migration destination states conflict with journal state")


def _validate_state(value: Mapping[str, Any]) -> None:
    """Check that journal flags match the recorded publication state."""
    state = value["state"]
    flags = (value["publication_started"], value["committed"], value["resolved"], value["result"])
    # Flag tuples accepted for each state except ROLLED_BACK
    accepted = {
        "PREPARED": (False, False, False, None),
        "PUBLISHING": (True, False, False, None),
        "COMPENSATING": (True, False, False, None),
        "COMMITTED": (True, True, True, "COMMITTED"),
        "RECOVERY_REQUIRED": (True, False, False, "RECOVERY_REQUIRED"),
    }
    # Rolled-back journals must resolve without claiming a commit
    if state == "ROLLED_BACK":
        if value["committed"] or not value["resolved"] or value["result"] != "ROLLED_BACK":
            raise MigrationJournalError("migration journal rollback flags conflict")
    elif flags != accepted[state]:
        raise MigrationJournalError("migration journal state flags conflict")


def _validate_destination(value: object) -> None:
    """Validate one destination record and its recovery evidence."""
    # Destinations persist an exact field set shared with the plan grammar
    fields = {
        "role", "label", "payload_identity", "prior_payload_identity",
        "target_relative", "staged_relative", "recovery_relative",
        "prior_exists", "prior_sha256", "staged_sha256", "state",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise MigrationJournalError("migration destination fields are invalid")
    if value["role"] not in {"SETTINGS", "PROFILE", "LEGACY_BACKUP_INDEX", "REPORT"}:
        raise MigrationJournalError("migration destination role is invalid")
    # Labels are short non-empty identity strings
    if not isinstance(value["label"], str) or not value["label"] or len(value["label"]) > 128:
        raise MigrationJournalError("migration destination label is invalid")
    # Payload identities are non-empty and bounded in length
    if (
        not isinstance(value["payload_identity"], str)
        or not value["payload_identity"] or len(value["payload_identity"]) > 128
    ):
        raise MigrationJournalError("migration destination payload identity is invalid")
    # Prior identity, prior flag, and recovery path must describe one state
    prior_identity = value["prior_payload_identity"]
    if value["prior_exists"] != (prior_identity is not None):
        raise MigrationJournalError("migration destination prior identity conflicts")
    if prior_identity is not None and (
        not isinstance(prior_identity, str) or not prior_identity or len(prior_identity) > 128
    ):
        raise MigrationJournalError("migration destination prior identity is invalid")
    # Both relative paths must be portable manager-owned paths
    for field in ("target_relative", "staged_relative"):
        _safe_relative(value[field], field)
    if value["recovery_relative"] is not None:
        _safe_relative(value["recovery_relative"], "recovery_relative")
    if type(value["prior_exists"]) is not bool:
        raise MigrationJournalError("migration destination prior flag is invalid")
    # The prior digest must exist exactly when prior evidence exists
    prior = value["prior_sha256"]
    if value["prior_exists"] != (prior is not None):
        raise MigrationJournalError("migration destination prior proof conflicts")
    if value["prior_exists"] != (value["recovery_relative"] is not None):
        raise MigrationJournalError("migration destination recovery proof conflicts")
    if prior is not None and (not isinstance(prior, str) or not SHA256.fullmatch(prior)):
        raise MigrationJournalError("migration destination prior digest is invalid")
    # The staged digest must always be lowercase hex
    if not isinstance(value["staged_sha256"], str) or not SHA256.fullmatch(value["staged_sha256"]):
        raise MigrationJournalError("migration destination staged digest is invalid")
    # The destination state must belong to the destination machine
    if value["state"] not in DESTINATION_STATES:
        raise MigrationJournalError("migration destination state is invalid")


def _safe_relative(value: object, field: str) -> None:
    """Reject absolute, drive-qualified, or non-normalized relative paths."""
    # Backslashes and drive colons would break portability
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise MigrationJournalError(f"migration {field} is invalid")
    path = PurePosixPath(value)
    # Dot segments and absolute roots could escape the manager root
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise MigrationJournalError(f"migration {field} is unsafe")
