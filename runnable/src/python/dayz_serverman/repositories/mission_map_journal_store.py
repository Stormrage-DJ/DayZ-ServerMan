"""Durable editor publication journals below data/operations/mission-map-journals/, with parsing (D4)."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path, PureWindowsPath

from ..adapters.windows.shared_files import read_text_shared, replace_file
from ..domain.mission_map_records import APPLIED_KINDS
from ..domain.mission_map_values import IDENTIFIER, SHA256, TARGET_KEY
from ..domain.profiles import validate_profile_id, validate_relative_path
from .mission_map_journal import (
    GROUP_STATES, JOURNAL_SCHEMA, PHASES, RECORD_KINDS, RECORD_STATES, RESULTS, MapGroup, MapJournal, MapRecord,
    MissionMapJournalError, record_staging, validate_state,
)
from .mission_map_layout import FILE_SETS, TargetClass
from .paths import PortablePaths, normalize_manager_relative


# Folders below data/operations: active journals (retired ones in completed/) and recovery copies by operation
JOURNAL_FOLDER = "mission-map-journals"
RECOVERY_FOLDER = "mission-map-recovery"
COMPLETED_FOLDER = "completed"


def journal_folder(paths: PortablePaths) -> Path:
    """Return the folder of the active editor publication journals."""
    return paths.operations / JOURNAL_FOLDER


def recovery_folder(paths: PortablePaths) -> Path:
    """Return the folder that holds one recovery-copy folder for each operation."""
    return paths.operations / RECOVERY_FOLDER


class MissionMapJournalRepository:
    """Persist editor publication journals as atomic, fsync-verified JSON files."""

    def __init__(
        self, root: Path, *, retire_hook: Callable[[str], None] | None = None, create_root: bool = True,
    ) -> None:
        """Store the journal folder and the optional retirement hook; an observer session creates no folder."""
        self.root = root.resolve(strict=False)
        if create_root:
            self.root.mkdir(parents=True, exist_ok=True)
        # Default the retire hook to a no-op for callers without crash hooks
        self._retire_hook = retire_hook or (lambda _phase: None)

    def path_for(self, operation_id: str) -> Path:
        """Return the journal file of one operation; the identifier must be one safe name."""
        if not isinstance(operation_id, str) or IDENTIFIER.fullmatch(operation_id) is None:
            raise MissionMapJournalError("mission map operation identifier is invalid")
        return self.root / f"{operation_id}.json"

    def retired_path_for(self, operation_id: str) -> Path:
        """Return the archived path that holds a retired journal."""
        return self.root / COMPLETED_FOLDER / self.path_for(operation_id).name

    def save(self, journal: MapJournal) -> Path:
        """Check the journal state, then write it durably through one atomic replace."""
        validate_state(journal)
        path = self.path_for(journal.operation_id)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        # Serialize deterministically so persisted bytes stay comparable
        payload = json.dumps(journal.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        # Flush the staged copy before the atomic replace, so the journal is durable before any DayZ-side byte
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path)
        return path

    def load(self, path: Path) -> MapJournal:
        """Read and check one journal file."""
        try:
            raw = json.loads(read_text_shared(path, encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise MissionMapJournalError("mission map journal is unreadable") from error
        return parse_journal(raw, path.stem)

    def records(self) -> tuple[tuple[Path, MapJournal | None], ...]:
        """Return every active journal file with its journal, or None when it cannot be trusted."""
        result: list[tuple[Path, MapJournal | None]] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name.casefold()):
            try:
                result.append((path, self.load(path)))
            except MissionMapJournalError:
                # Report an untrusted journal without failing the whole scan
                result.append((path, None))
        return tuple(result)

    def retire(self, journal: MapJournal) -> Path:
        """Archive a resolved journal into completed/ and verify the archived copy."""
        validate_state(journal)
        if not journal.resolved or journal.result not in RESULTS:
            raise MissionMapJournalError("only a resolved mission map journal can be retired")
        active = self.path_for(journal.operation_id)
        # Refuse retirement when the persisted journal changed since it was read
        if self.load(active).to_dict() != journal.to_dict():
            raise MissionMapJournalError("mission map journal changed before retirement")
        destination = self.retired_path_for(journal.operation_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise MissionMapJournalError("retired mission map journal already exists")
        self._retire_hook("BEFORE_RETIRE")
        replace_file(active, destination)
        self._retire_hook("AFTER_RETIRE")
        # Prove the archived copy survived the move
        if self.load(destination).to_dict() != journal.to_dict():
            raise MissionMapJournalError("retired mission map journal failed verification")
        return destination


# Exact key sets of the persisted objects
JOURNAL_FIELDS = frozenset(field.name for field in fields(MapJournal))
GROUP_FIELDS = frozenset(field.name for field in fields(MapGroup))
RECORD_FIELDS = frozenset(field.name for field in fields(MapRecord))


def parse_journal(raw: object, filename_id: str) -> MapJournal:
    """Parse and check a journal document; any deviation raises MissionMapJournalError."""
    try:
        journal = _parse(raw, filename_id)
        validate_state(journal)
        return journal
    except MissionMapJournalError:
        raise
    except (TypeError, ValueError, KeyError) as error:
        raise MissionMapJournalError(f"mission map journal fields are invalid: {error}") from error


def _parse(raw: object, filename_id: str) -> MapJournal:
    """Build the journal from its JSON object after the field checks."""
    if not isinstance(raw, dict) or set(raw) != JOURNAL_FIELDS or not _integer(raw["schema_version"]):
        raise MissionMapJournalError("mission map journal schema is invalid")
    if raw["schema_version"] != JOURNAL_SCHEMA:
        raise MissionMapJournalError("mission map journal schema version is not supported")
    # The operation identifier names the file and every staging path, so it must be one safe name
    if not _matches(raw["operation_id"], IDENTIFIER) or raw["operation_id"] != filename_id:
        raise MissionMapJournalError("mission map journal operation is invalid")
    if raw["operation_kind"] not in APPLIED_KINDS:
        raise MissionMapJournalError("mission map journal operation kind is invalid")
    validate_profile_id(raw["profile_id"])
    validate_relative_path(raw["mission_root"], "mission_root")
    validate_relative_path(raw["runtime_profile"], "runtime_profile", optional=True)
    # A runtime key exists exactly when the journal names a runtime profile folder
    runtime_key_ok = (raw["runtime_profile_key"] is None) if raw["runtime_profile"] is None else _matches(
        raw["runtime_profile_key"], TARGET_KEY)
    if not _matches(raw["mission_key"], TARGET_KEY) or not runtime_key_ok:
        raise MissionMapJournalError("mission map journal target keys are invalid")
    if not isinstance(raw["dayz_root"], str) or not PureWindowsPath(raw["dayz_root"]).is_absolute():
        raise MissionMapJournalError("mission map journal DayZ root must be absolute")
    _check_plan_identity(raw)
    if not _integer(raw["profile_revision"]) or not _integer(raw["settings_revision"]):
        raise MissionMapJournalError("mission map journal revisions are invalid")
    if raw["phase"] not in PHASES or raw["result"] not in (None, *RESULTS):
        raise MissionMapJournalError("mission map journal phase is invalid")
    if not isinstance(raw["publication_started"], bool) or not isinstance(raw["resolved"], bool):
        raise MissionMapJournalError("mission map journal flags are invalid")
    if not isinstance(raw["groups"], list) or not isinstance(raw["records"], list):
        raise MissionMapJournalError("mission map journal groups or records are invalid")
    groups = [_parse_group(value) for value in raw["groups"]]
    records = [_parse_record(value, raw["operation_id"]) for value in raw["records"]]
    values = {name: raw[name] for name in JOURNAL_FIELDS - {"groups", "records"}}
    return MapJournal(**values, groups=groups, records=records)


def _check_plan_identity(raw: dict[str, object]) -> None:
    """Require the plan identity of an apply, and none for a restore."""
    plan = (raw["plan_id"], raw["plan_revision"], raw["plan_fingerprint"], raw["preview_fingerprint"])
    if raw["operation_kind"] != "apply":
        valid = plan == (None, None, None, None)
    else:
        valid = (_matches(plan[0], IDENTIFIER) and _integer(plan[1])
                 and _matches(plan[2], SHA256) and _matches(plan[3], SHA256))
    if not valid:
        raise MissionMapJournalError("mission map journal plan identity is invalid")


def _parse_group(raw: object) -> MapGroup:
    """Parse one DayZ-side group; its relative path must belong to the managed file set of its class."""
    if not isinstance(raw, dict) or set(raw) != GROUP_FIELDS:
        raise MissionMapJournalError("mission map journal group fields are invalid")
    target_class = TargetClass(raw["target_class"])
    if raw["relative_path"] not in FILE_SETS[target_class]:
        raise MissionMapJournalError("mission map journal group is outside the managed file set")
    if any(not isinstance(raw[name], str) or not raw[name] for name in ("target_path", "staging_path")):
        raise MissionMapJournalError("mission map journal group path is invalid")
    old, new = raw["old_existed"], raw["new_exists"]
    if not isinstance(old, bool) or not isinstance(new, bool) or not (old or new):
        raise MissionMapJournalError("mission map journal group existence is invalid")
    # An old file has a digest and a recovery copy; a new file has a digest; an absent side has neither
    sides_ok = (
        (_matches(raw["old_digest"], SHA256) and isinstance(raw["recovery_path"], str) and raw["recovery_path"])
        if old else raw["old_digest"] is None and raw["recovery_path"] is None
    ) and (_matches(raw["new_digest"], SHA256) if new else raw["new_digest"] is None)
    ancestors = raw["created_ancestors"]
    ancestors_ok = isinstance(ancestors, list) and all(isinstance(item, str) and item for item in ancestors)
    # A file that must end absent never needs created folders
    if not sides_ok or raw["state"] not in GROUP_STATES or not ancestors_ok or (ancestors and not new):
        raise MissionMapJournalError("mission map journal group state is invalid")
    return MapGroup(**{**raw, "target_class": target_class.value, "created_ancestors": tuple(ancestors)})


def _parse_record(raw: object, operation_id: str) -> MapRecord:
    """Parse one record entry; its staging file is beside its target and holds the operation ID."""
    if not isinstance(raw, dict) or set(raw) != RECORD_FIELDS or raw["kind"] not in RECORD_KINDS:
        raise MissionMapJournalError("mission map journal record fields are invalid")
    target = raw["target_path"]
    if not isinstance(target, str) or normalize_manager_relative(target) != target:
        raise MissionMapJournalError("mission map journal record path is invalid")
    if raw["staging_path"] != record_staging(target, operation_id):
        raise MissionMapJournalError("mission map journal record staging path is invalid")
    old = raw["old_existed"]
    old_ok = _matches(raw["old_manifest_sha256"], SHA256) if old is True else (
        old is False and raw["old_manifest_sha256"] is None)
    if not old_ok or not _matches(raw["new_manifest_sha256"], SHA256) or raw["state"] not in RECORD_STATES:
        raise MissionMapJournalError("mission map journal record state is invalid")
    return MapRecord(**raw)


def _matches(value: object, pattern: re.Pattern[str]) -> bool:
    """Return whether a value is a text that fully matches the pattern."""
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _integer(value: object) -> bool:
    """Return whether a value is a non-negative integer and not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
