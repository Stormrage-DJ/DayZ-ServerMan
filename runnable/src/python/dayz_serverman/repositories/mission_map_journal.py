"""Editor publication journal (D4): fields, per-file groups, manager-root record entries, phases and states."""

from __future__ import annotations

from dataclasses import dataclass, fields


# Journal schema of D4
JOURNAL_SCHEMA = 1
# Journal phases; COMMITTING is the commit point, after which only a roll-forward is allowed
PHASES = frozenset(("PREPARED", "PUBLISHING", "PUBLISHED", "COMMITTING", "COMPENSATING", "ROLLED_BACK", "COMMITTED"))
# Lifecycle states of one DayZ-side group, as in backup restore
GROUP_STATES = frozenset(("READY", "PUBLISHING", "PUBLISHED", "COMPENSATED"))
# Kinds of manager-root records that a publication stages, and their states
RECORD_KINDS = frozenset(("original", "baseline", "applied", "ledger"))
RECORD_STATES = frozenset(("READY", "PUBLISHED"))
# Terminal results of a resolved journal
RESULTS = frozenset(("ROLLED_BACK", "COMMITTED"))
# Group states in publication order: published groups first, then at most one in progress, then ready ones
PUBLICATION_ORDER = ("PUBLISHED", "PUBLISHING", "READY")
# Name mark of a staged record: <record>.json.staged-<operation_id>, beside its target
RECORD_STAGE_MARK = ".staged-"


class MissionMapJournalError(RuntimeError):
    """Raised when an editor publication journal cannot be trusted or persisted."""
    pass


@dataclass
class MapGroup:
    """One DayZ-side file: the backup-restore group shape plus its class, relative path and new existence."""

    target_class: str
    relative_path: str
    target_path: str
    staging_path: str
    recovery_path: str | None
    old_existed: bool
    old_digest: str | None
    new_exists: bool
    # None only when the file must end absent
    new_digest: str | None
    state: str = "READY"
    created_ancestors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the group as its journal JSON object."""
        value = dict(vars(self))
        value["created_ancestors"] = list(self.created_ancestors)
        return value


@dataclass
class MapRecord:
    """One manager-root record that publishes after the commit point; paths are manager-relative."""

    kind: str
    target_path: str
    staging_path: str
    old_existed: bool
    old_manifest_sha256: str | None
    new_manifest_sha256: str
    state: str = "READY"

    def to_dict(self) -> dict[str, object]:
        """Return the record entry as its journal JSON object."""
        return dict(vars(self))


@dataclass
class MapJournal:
    """Durable editor publication journal of one apply, restore original or restore previous operation."""

    operation_id: str
    operation_kind: str
    profile_id: str
    mission_root: str
    mission_key: str
    runtime_profile: str | None
    runtime_profile_key: str | None
    dayz_root: str
    plan_id: str | None
    plan_revision: int | None
    plan_fingerprint: str | None
    preview_fingerprint: str | None
    profile_revision: int
    settings_revision: int
    groups: list[MapGroup]
    records: list[MapRecord]
    phase: str = "PREPARED"
    publication_started: bool = False
    resolved: bool = False
    result: str | None = None
    schema_version: int = JOURNAL_SCHEMA

    @property
    def committed(self) -> bool:
        """Return whether the journal reached its committed terminal state; the engine reads this."""
        return self.phase == "COMMITTED"

    def to_dict(self) -> dict[str, object]:
        """Return the journal as its persisted JSON object."""
        value: dict[str, object] = {field.name: getattr(self, field.name) for field in fields(self)}
        value["groups"] = [group.to_dict() for group in self.groups]
        value["records"] = [record.to_dict() for record in self.records]
        return value


def record_staging(target: str, operation_id: str) -> str:
    """Return the commit staging path of a record path: <record>.json.staged-<operation_id>, beside it."""
    return f"{target}{RECORD_STAGE_MARK}{operation_id}"


def validate_state(journal: MapJournal) -> None:
    """Reject phase, flag and state combinations that the D4 commit order cannot produce."""
    groups = [group.state for group in journal.groups]
    records = [record.state for record in journal.records]
    # Every target appears once; runtime groups need the runtime profile folder of the journal
    targets = [group.target_path.casefold() for group in journal.groups]
    record_targets = [record.target_path.casefold() for record in journal.records]
    if not (groups or records) or len(set(targets)) != len(targets) or len(set(record_targets)) != len(records):
        raise MissionMapJournalError("mission map journal targets are missing or not unique")
    if journal.runtime_profile is None and any(group.target_class == "runtime" for group in journal.groups):
        raise MissionMapJournalError("mission map journal names a runtime file without a runtime profile")
    flags = (journal.publication_started, journal.resolved, journal.result)
    unpublished = all(state == "READY" for state in records)
    published = all(state == "PUBLISHED" for state in groups)
    phase = journal.phase
    if phase == "PREPARED":
        valid = flags == (False, False, None) and all(state == "READY" for state in groups) and unpublished
    elif phase == "PUBLISHING":
        valid = flags == (True, False, None) and _in_order(groups, PUBLICATION_ORDER) and unpublished
    elif phase == "PUBLISHED":
        valid = flags == (True, False, None) and published and unpublished
    elif phase == "COMMITTING":
        # Records publish one by one after the commit point
        valid = flags == (True, False, None) and published and _in_order(records, ("PUBLISHED", "READY"))
    elif phase == "COMPENSATING":
        split = next((index for index, state in enumerate(groups) if state == "COMPENSATED"), len(groups))
        valid = (flags == (True, False, None) and unpublished
                 and _in_order(groups[:split], PUBLICATION_ORDER)
                 and all(state == "COMPENSATED" for state in groups[split:]))
    elif phase == "ROLLED_BACK":
        expected = "COMPENSATED" if journal.publication_started else "READY"
        valid = flags[1:] == (True, "ROLLED_BACK") and all(state == expected for state in groups) and unpublished
    else:
        valid = flags == (True, True, "COMMITTED") and published and all(state == "PUBLISHED" for state in records)
    if not valid:
        raise MissionMapJournalError("mission map journal state combination is invalid")


def _in_order(states: list[str], order: tuple[str, ...]) -> bool:
    """Return whether states follow the order with at most one in-progress state."""
    rank = {state: index for index, state in enumerate(order)}
    return (all(state in rank for state in states) and states.count("PUBLISHING") <= 1
            and [rank[state] for state in states] == sorted(rank[state] for state in states))
