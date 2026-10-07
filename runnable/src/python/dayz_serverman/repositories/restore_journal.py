"""Durable versioned restore journal persistence."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable
from pathlib import Path

from ..adapters.windows.shared_files import read_text_shared, replace_file
from ..domain.backups import BACKUP_ID, SHA256
from ..domain.profiles import validate_profile_id, validate_relative_path
from ..domain.restores import RESTORE_JOURNAL_SCHEMA, RestoreGroup, RestoreJournal


# Restore identifiers become file names, so the pattern stays path-safe
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# Lifecycle states allowed for one restore group record
GROUP_STATES = frozenset(("READY", "PUBLISHING", "PUBLISHED", "COMPENSATED"))
# Journal phases in restore lifecycle order
PHASES = frozenset(("PREPARED", "PUBLISHING", "COMPENSATING", "ROLLED_BACK", "COMMITTED"))
# Terminal results allowed on a resolved journal
RESULTS = frozenset(("ROLLED_BACK", "COMMITTED"))


class RestoreJournalError(RuntimeError):
    """Raised when a restore journal cannot be trusted or persisted."""
    pass


class RestoreJournalRepository:
    """Persist restore journals as atomic, fsync-verified JSON files."""
    def __init__(
        self, root: Path, *, retire_hook: Callable[[str], None] | None = None, create_root: bool = True,
    ) -> None:
        """Store the journal root and the optional retirement hook; an observer session creates no folder."""
        self.root = root.resolve(strict=False)
        if create_root:
            self.root.mkdir(parents=True, exist_ok=True)
        # Default the retire hook to a no-op for callers without crash hooks
        self._retire_hook = retire_hook or (lambda _phase: None)

    def path_for(self, operation_id: str) -> Path:
        """Return the journal file path for a restore operation identifier."""
        # Reject identifiers that could escape the journal directory
        if IDENTIFIER.fullmatch(operation_id) is None:
            raise RestoreJournalError("restore operation identifier is invalid")
        return self.root / f"{operation_id}.json"

    def retired_path_for(self, operation_id: str) -> Path:
        """Return the archived path that holds a completed journal."""
        return self.root / "completed" / self.path_for(operation_id).name

    def save(self, journal: RestoreJournal) -> Path:
        """Write the journal atomically and return its file path."""
        # Validate the journal state before persisting it
        _validate_state(journal)
        path = self.path_for(journal.operation_id)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        # Serialize deterministically so persisted bytes stay comparable
        payload = json.dumps(
            journal.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
        ) + "\n"
        # Flush the staged copy before the atomic replace
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path)
        return path

    def load(self, path: Path) -> RestoreJournal:
        """Read a journal file and return the validated record."""
        try:
            raw = json.loads(read_text_shared(path, encoding="utf-8"))
            return _parse(raw, path.stem)
        except RestoreJournalError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RestoreJournalError("restore journal is unreadable") from error
        except (TypeError, ValueError) as error:
            raise RestoreJournalError("restore journal fields are invalid") from error

    def records(self) -> tuple[tuple[Path, RestoreJournal | None], ...]:
        """Return every journal file paired with its record or None."""
        result: list[tuple[Path, RestoreJournal | None]] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name.casefold()):
            try:
                result.append((path, self.load(path)))
            except RestoreJournalError:
                # Report unreadable journals without failing the whole scan
                result.append((path, None))
        return tuple(result)

    def retire(self, journal: RestoreJournal) -> Path:
        """Archive a completed journal and verify the archived copy."""
        # Only a resolved journal with a terminal result may be retired
        _validate_state(journal)
        if not journal.resolved or journal.result not in RESULTS:
            raise RestoreJournalError("only a completed restore journal can be retired")
        active = self.path_for(journal.operation_id)
        # Refuse retirement when the persisted journal changed since it was read
        persisted = self.load(active)
        if persisted.to_dict() != journal.to_dict():
            raise RestoreJournalError("restore journal changed before retirement")
        destination = self.retired_path_for(journal.operation_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RestoreJournalError("retired restore journal already exists")
        self._retire_hook("BEFORE_RETIRE")
        replace_file(active, destination)
        self._retire_hook("AFTER_RETIRE")
        # Prove the archived copy survived the move byte for byte
        if self.load(destination).to_dict() != journal.to_dict():
            raise RestoreJournalError("retired restore journal failed verification")
        return destination


def _parse(raw: object, filename_id: str) -> RestoreJournal:
    """Parse and validate a journal document of schema one to three."""
    common_fields = {
        "schema_version", "operation_id", "backup_id", "profile_id", "manifest_digest",
        "phase", "publication_started", "committed", "resolved", "result", "groups",
    }
    if not isinstance(raw, dict):
        raise RestoreJournalError("restore journal schema is invalid")
    version = raw.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version not in (1, 2, 3):
        raise RestoreJournalError("restore journal schema is invalid")
    # The expected field set depends on the schema version being read
    fields = common_fields if version == 1 else common_fields | {"runtime_profile"}
    if set(raw) != fields:
        raise RestoreJournalError("restore journal schema is invalid")
    # Validate identity, digest, and state fields before building the record
    operation_id = raw["operation_id"]
    backup_id = raw["backup_id"]
    digest = raw["manifest_digest"]
    if not isinstance(operation_id, str) or IDENTIFIER.fullmatch(operation_id) is None or operation_id != filename_id:
        raise RestoreJournalError("restore journal operation is invalid")
    if not isinstance(backup_id, str) or BACKUP_ID.fullmatch(backup_id) is None:
        raise RestoreJournalError("restore backup identifier is invalid")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise RestoreJournalError("restore manifest digest is invalid")
    phase = raw["phase"]
    booleans = (raw["publication_started"], raw["committed"], raw["resolved"])
    if phase not in PHASES or any(not isinstance(value, bool) for value in booleans):
        raise RestoreJournalError("restore journal state is invalid")
    groups_raw = raw["groups"]
    if not isinstance(groups_raw, list) or not groups_raw:
        raise RestoreJournalError("restore journal groups are invalid")
    groups = [_parse_group(value, version) for value in groups_raw]
    result = raw["result"]
    if result is not None and result not in RESULTS:
        raise RestoreJournalError("restore journal result is invalid")
    journal = RestoreJournal(
        operation_id, backup_id, validate_profile_id(raw["profile_id"]), digest, phase,
        booleans[0], booleans[1], booleans[2], groups, result,
        schema_version=version,
        runtime_profile=validate_relative_path(
            raw.get("runtime_profile"), "runtime_profile", optional=True,
        ),
    )
    # Re-apply the state invariants so tampered files are rejected
    _validate_state(journal)
    return journal


def _parse_group(raw: object, version: int) -> RestoreGroup:
    """Parse and validate one restore group entry."""
    fields = {
        "entry_path", "target_path", "staging_path", "recovery_path", "old_existed",
        "old_digest", "new_digest", "state",
    }
    # Version three records the ancestors created during publication
    if version >= 3:
        fields.add("created_ancestors")
    if not isinstance(raw, dict) or set(raw) != fields:
        raise RestoreJournalError("restore journal group fields are invalid")
    for field in ("entry_path", "target_path", "staging_path"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise RestoreJournalError("restore journal group path is invalid")
    recovery = raw["recovery_path"]
    old_digest = raw["old_digest"]
    if recovery is not None and (not isinstance(recovery, str) or not recovery):
        raise RestoreJournalError("restore recovery path is invalid")
    for digest in (old_digest, raw["new_digest"]):
        if digest is not None and (not isinstance(digest, str) or SHA256.fullmatch(digest) is None):
            raise RestoreJournalError("restore group digest is invalid")
    if not isinstance(raw["old_existed"], bool) or raw["state"] not in GROUP_STATES:
        raise RestoreJournalError("restore journal group state is invalid")
    ancestors = raw.get("created_ancestors", [])
    if not isinstance(ancestors, list) or any(not isinstance(item, str) or not item for item in ancestors):
        raise RestoreJournalError("restore journal ancestor paths are invalid")
    return RestoreGroup(
        raw["entry_path"], raw["target_path"], raw["staging_path"], recovery,
        raw["old_existed"], old_digest, raw["new_digest"], raw["state"], tuple(ancestors),
    )


def _validate_state(journal: RestoreJournal) -> None:
    """Reject state combinations that the restore protocol cannot produce."""
    states = [group.state for group in journal.groups]
    # Case-folded targets must be unique so no file is published twice
    if not states or len({group.target_path.casefold() for group in journal.groups}) != len(states):
        raise RestoreJournalError("restore journal groups are not unique")
    flags = (journal.publication_started, journal.committed, journal.resolved, journal.result)
    # Each phase allows exactly one flag and group-state combination
    if journal.phase == "PREPARED":
        valid = flags == (False, False, False, None) and set(states) == {"READY"}
    elif journal.phase == "PUBLISHING":
        valid = flags == (True, False, False, None) and _publication_order(states)
    elif journal.phase == "COMPENSATING":
        split = next((index for index, state in enumerate(states) if state == "COMPENSATED"), len(states))
        valid = (
            flags == (True, False, False, None)
            and _publication_order(states[:split])
            and all(state == "COMPENSATED" for state in states[split:])
        )
    elif journal.phase == "ROLLED_BACK":
        expected = {"COMPENSATED"} if journal.publication_started else {"READY"}
        valid = flags[1:] == (False, True, "ROLLED_BACK") and set(states) == expected
    else:
        valid = (
            journal.phase == "COMMITTED"
            and flags == (True, True, True, "COMMITTED")
            and set(states) == {"PUBLISHED"}
        )
    if not valid:
        raise RestoreJournalError("restore journal state combination is invalid")


def _publication_order(states: list[str]) -> bool:
    """Return True when group states follow the publication order contract."""
    # Lower rank means further along the publication order
    rank = {"PUBLISHED": 0, "PUBLISHING": 1, "READY": 2}
    return (
        all(state in rank for state in states)
        and states.count("PUBLISHING") <= 1
        and [rank[state] for state in states] == sorted(rank[state] for state in states)
    )
