"""Portable persistence domain records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


# JSON-compatible value tree accepted by persisted records
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class RecordState(str, Enum):
    """Health state of a persisted record file."""

    VALID = "VALID"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"
    FUTURE_SCHEMA = "FUTURE_SCHEMA"
    INTERRUPTED_WRITE = "INTERRUPTED_WRITE"


class PathRole(str, Enum):
    """Configured path role a diagnostic evaluates."""

    DAYZ_ROOT = "dayz_root"
    DAYZ_EXECUTABLE = "dayz_executable"
    STEAMCMD_ROOT = "steamcmd_root"
    STEAMCMD_EXECUTABLE = "steamcmd_executable"
    WORKSHOP_CONTENT_ROOT = "workshop_content_root"
    BACKUP_ROOT = "backup_root"


class PathStatus(str, Enum):
    """Diagnostic result for one configured path."""

    READY = "READY"
    UNCONFIGURED = "UNCONFIGURED"
    MISSING = "MISSING"
    MOVED = "MOVED"
    NOT_FILE = "NOT_FILE"
    NOT_DIRECTORY = "NOT_DIRECTORY"
    NOT_WRITABLE = "NOT_WRITABLE"
    UNSUPPORTED_NETWORK = "UNSUPPORTED_NETWORK"
    UNSUPPORTED_REPARSE = "UNSUPPORTED_REPARSE"
    INACCESSIBLE = "INACCESSIBLE"


@dataclass(frozen=True)
class PathDiagnostic:
    """One path-role diagnostic with a message and suggested action."""

    role: PathRole
    status: PathStatus
    configured_path: str | None
    message: str
    action: str


@dataclass(frozen=True)
class SettingsInput:
    """Raw user-supplied settings before validation."""

    dayz_root: str | None = None
    dayz_executable: str | None = None
    steamcmd_root: str | None = None
    steamcmd_executable: str | None = None
    workshop_content_root: str | None = None
    custom_backup_root: str | None = None
    steam_account_name: str | None = None
    steam_authentication_mode: str | None = None


@dataclass(frozen=True)
class ManagerSettings:
    """Validated manager settings with revisions and a path map."""

    revision: int | None
    dayz_root: str | None
    dayz_executable: str | None
    steamcmd_root: str | None
    steamcmd_executable: str | None
    workshop_content_root: str | None
    custom_backup_root: str | None
    last_validated_paths: Mapping[str, str]
    steam_account_name: str | None = None
    steam_authentication_mode: str | None = None

    @classmethod
    def unconfigured(cls) -> ManagerSettings:
        """Return the empty settings used before first configuration."""
        return cls(None, None, None, None, None, None, None, MappingProxyType({}))


@dataclass(frozen=True)
class VersionedDocument:
    """Persisted document with a schema version and revision."""

    schema_version: int
    revision: int
    fields: Mapping[str, JsonValue]

    @classmethod
    def create(
        cls,
        schema_version: int,
        revision: int,
        fields: Mapping[str, JsonValue],
    ) -> VersionedDocument:
        """Return an immutable document with a copied field mapping."""
        return cls(schema_version, revision, MappingProxyType(dict(fields)))

    def as_json_object(self) -> dict[str, JsonValue]:
        """Return the document as its persisted JSON object."""
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            **self.fields,
        }


@dataclass(frozen=True)
class RecordInspection:
    """Result of inspecting one persisted record path."""

    state: RecordState
    path: Path
    document: VersionedDocument | None = None
    evidence: tuple[Path, ...] = ()
    detail: str | None = None


class RepositoryError(RuntimeError):
    """Base error for persistence repository failures."""
    pass


class RevisionConflict(RepositoryError):
    """Raised when a write loses its optimistic revision check."""
    pass


class RecordUnavailable(RepositoryError):
    """Raised when a record cannot be read in its expected state."""

    def __init__(self, inspection: RecordInspection) -> None:
        """Store the inspection that explains why the record is unavailable."""
        self.inspection = inspection
        super().__init__(inspection.detail or f"record is {inspection.state.value}")
