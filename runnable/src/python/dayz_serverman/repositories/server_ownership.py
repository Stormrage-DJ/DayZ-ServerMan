"""Durable server ownership record (A6): which server process this manager root started."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from ..domain.models import RecordState
from .json_store import StagingPolicy, VersionedJsonRepository


# The record sits beside data/state.json, which keeps its exact schema for older builds
OWNERSHIP_FILE = "server-ownership.json"
OWNERSHIP_SCHEMA_VERSION = 1
# Exact field sets of the record and of its server object (9.1)
_RECORD_FIELDS = frozenset(("manager_root", "server"))
_SERVER_FIELDS = frozenset((
    "pid", "executable_path", "creation_time_ns", "launch_token", "profile_id",
    "started_after_ns", "query_port", "rpt_directory", "session_id",
))


@dataclass(frozen=True)
class OwnedServer:
    """The launch of one server process by a manager session, with its readiness hints."""

    pid: int
    # Canonical image path, as canonical_process_path gives it
    executable_path: str
    # Creation time in 100 ns units of the process; adoption needs it to be equal
    creation_time_ns: int
    launch_token: str
    profile_id: str
    # Wall clock in ns taken right before the launch; the readiness grace counts from it
    started_after_ns: int
    query_port: int | None
    rpt_directory: str | None
    # Operation session of the manager session that wrote the record
    session_id: str


@dataclass(frozen=True)
class OwnershipRecord:
    """The record: the canonical manager root and the owned server, or None after a proven stop."""

    manager_root: str
    server: OwnedServer | None


class OwnershipUnavailable(Exception):
    """The record exists but cannot be used: corrupt, a newer schema, an interrupted write or bad fields."""


class ServerOwnershipRepository:
    """Read and write the ownership record through the atomic, revision-checked JSON store (A12)."""

    def __init__(self, path: Path, *, staging: StagingPolicy = StagingPolicy.OWNER) -> None:
        """Bind the record file; observers pass their staging-file rule (4.2.1)."""
        self._repository = VersionedJsonRepository(path, OWNERSHIP_SCHEMA_VERSION, staging=staging)

    @property
    def path(self) -> Path:
        """Return the record file path."""
        return self._repository.path

    def read(self) -> OwnershipRecord | None:
        """Return the record, None when it is missing, or raise OwnershipUnavailable.

        QF-33: a staging file beside a whole record does not hide it. The record is published by
        an atomic replace, so its content is complete; the staging file is a leftover that the
        next owner write removes (or this process's own write in flight).
        """
        inspection = self._repository.inspect()
        if inspection.state == RecordState.MISSING:
            return None
        whole = inspection.state == RecordState.INTERRUPTED_WRITE and inspection.document is not None
        if (inspection.state != RecordState.VALID and not whole) or inspection.document is None:
            raise OwnershipUnavailable(inspection.state.value)
        record = _parse(dict(inspection.document.fields))
        if record is None:
            raise OwnershipUnavailable("invalid fields")
        return record

    def write(self, record: OwnershipRecord) -> None:
        """Save the record over a missing or valid one; raise OwnershipUnavailable for any other state.

        RevisionConflict, RecordUnavailable and OSError of the save reach the caller.
        """
        # Inspect first: only a missing or a valid record may be replaced (9.2 "Save revision")
        inspection = self._repository.inspect()
        if inspection.state == RecordState.INTERRUPTED_WRITE:
            # QF-33: only an owner writes this record, under the instance lock and the lifecycle mutex,
            # so a staging file seen here is a crash leftover; remove it, then inspect again
            self._remove_leftovers(inspection.evidence)
            inspection = self._repository.inspect()
        if inspection.state == RecordState.MISSING:
            revision = None
        elif inspection.state == RecordState.VALID and inspection.document is not None:
            revision = inspection.document.revision
        else:
            raise OwnershipUnavailable(inspection.state.value)
        server = asdict(record.server) if record.server is not None else None
        self._repository.save({"manager_root": record.manager_root, "server": server}, revision)

    def _remove_leftovers(self, evidence: tuple[Path, ...]) -> None:
        """Delete the staging files of this record only (".server-ownership.json.<hex>.tmp")."""
        prefix = f".{self.path.name}."
        for path in evidence:
            if path.parent == self.path.parent and path.name.startswith(prefix) and path.name.endswith(".tmp"):
                path.unlink(missing_ok=True)


def _parse(fields: dict[str, object]) -> OwnershipRecord | None:
    """Return the record of valid fields, or None when any field set or type is wrong."""
    if set(fields) != _RECORD_FIELDS:
        return None
    root, server = fields["manager_root"], fields["server"]
    if not isinstance(root, str) or not root:
        return None
    if server is None:
        return OwnershipRecord(root, None)
    if not isinstance(server, dict) or set(server) != _SERVER_FIELDS:
        return None
    # Booleans must not pass as integers, and every text must be non-empty
    integers_valid = (
        _integer(server["pid"]) and server["pid"] > 0
        and _integer(server["creation_time_ns"]) and _integer(server["started_after_ns"])
        and (server["query_port"] is None or _integer(server["query_port"]) and 0 < server["query_port"] < 65536)
    )
    texts_valid = all(_text(server[name]) for name in (
        "executable_path", "launch_token", "profile_id", "session_id",
    )) and (server["rpt_directory"] is None or _text(server["rpt_directory"]))
    if not (integers_valid and texts_valid):
        return None
    return OwnershipRecord(root, OwnedServer(**server))


def _integer(value: object) -> bool:
    """Return whether the value is an integer and not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> bool:
    """Return whether the value is a non-empty text."""
    return isinstance(value, str) and bool(value)
