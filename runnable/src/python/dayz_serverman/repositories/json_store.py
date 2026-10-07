"""Atomic versioned UTF-8 JSON repository."""

from __future__ import annotations

import json
import os
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from ..adapters.windows.shared_files import open_shared, replace_file
from ..domain.models import (
    JsonValue,
    RecordInspection,
    RecordState,
    RecordUnavailable,
    RevisionConflict,
    VersionedDocument,
)


# Field names owned by the document envelope, never by callers
RESERVED_FIELDS = frozenset(("schema_version", "revision"))


class StagingPolicy(str, Enum):
    """How a read treats a staging file beside the record (A4, R-4)."""

    # Today's rule: a staging file is an interrupted write
    OWNER = "OWNER"
    # Observer sessions ignore it: an owner in another process may be writing right now
    OBSERVER = "OBSERVER"


class VersionedJsonRepository:
    """Read and atomically publish one versioned JSON record."""

    def __init__(
        self, path: Path, schema_version: int = 1, *, staging: StagingPolicy = StagingPolicy.OWNER,
    ) -> None:
        """Store the record path, the schema version this repository accepts and the staging-file rule."""
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        self.path = path.resolve(strict=False)
        self.schema_version = schema_version
        self.staging = staging

    def inspect(self) -> RecordInspection:
        """Classify the record file into a state with supporting evidence."""
        # Look for leftover temporary files that indicate an interrupted write; observers read the record only
        interrupted = self._temporary_files() if self.staging is StagingPolicy.OWNER else ()
        # Report a missing record, distinguishing interrupted writes
        if not self.path.exists():
            if interrupted:
                return RecordInspection(
                    RecordState.INTERRUPTED_WRITE,
                    self.path,
                    evidence=interrupted,
                    detail="temporary publication evidence exists without an authoritative record",
                )
            return RecordInspection(RecordState.MISSING, self.path)

        # Parse the record and classify unreadable content as corrupt
        try:
            document = self._read_document(self.path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as error:
            return RecordInspection(
                RecordState.CORRUPT,
                self.path,
                evidence=(self.path, *interrupted),
                detail=str(error),
            )
        # Reject documents written by a newer schema
        if document.schema_version > self.schema_version:
            return RecordInspection(
                RecordState.FUTURE_SCHEMA,
                self.path,
                document=document,
                evidence=(self.path, *interrupted),
                detail=(
                    f"schema {document.schema_version} is newer than supported "
                    f"schema {self.schema_version}"
                ),
            )
        # Treat other schema mismatches as needing a migration
        if document.schema_version != self.schema_version:
            return RecordInspection(
                RecordState.CORRUPT,
                self.path,
                document=document,
                evidence=(self.path, *interrupted),
                detail=f"schema {document.schema_version} requires a migration",
            )
        # Report unresolved temporary evidence for an otherwise valid record
        if interrupted:
            return RecordInspection(
                RecordState.INTERRUPTED_WRITE,
                self.path,
                document=document,
                evidence=interrupted,
                detail="unresolved temporary publication evidence exists",
            )
        return RecordInspection(RecordState.VALID, self.path, document=document)

    def load(self) -> VersionedDocument:
        """Return the stored document, or raise when it is not valid."""
        # Refuse to load records that are not in the valid state
        inspection = self.inspect()
        if inspection.state != RecordState.VALID or inspection.document is None:
            raise RecordUnavailable(inspection)
        return inspection.document

    def save(
        self,
        fields: Mapping[str, JsonValue],
        expected_revision: int | None,
    ) -> VersionedDocument:
        """Save fields under revision control and return the reloaded document."""
        # Validate field names and JSON encoding before any filesystem work
        self._validate_fields(fields)
        inspection = self.inspect()
        # Derive the next revision from the current state, enforcing expectations
        if inspection.state == RecordState.MISSING:
            if expected_revision is not None:
                raise RevisionConflict("new records require expected_revision=None")
            revision = 0
        elif inspection.state == RecordState.VALID and inspection.document is not None:
            current = inspection.document.revision
            if expected_revision != current:
                raise RevisionConflict(
                    f"expected revision {expected_revision}, current revision is {current}"
                )
            revision = current + 1
        else:
            raise RecordUnavailable(inspection)

        # Publish the new document, then reload to confirm the durable bytes
        document = VersionedDocument.create(self.schema_version, revision, fields)
        self._publish(document)
        reloaded = self._read_document(self.path)
        if reloaded != document:
            raise RecordUnavailable(
                RecordInspection(
                    RecordState.CORRUPT,
                    self.path,
                    evidence=(self.path,),
                    detail="published record failed reload validation",
                )
            )
        return reloaded

    def _publish(self, document: VersionedDocument) -> None:
        """Write the document through a staged temporary file and atomic replace."""
        # Ensure the target directory exists before staging
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Stage the document beside the target so the swap stays on one volume
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        # Serialize with stable formatting so reloads compare equal
        payload = json.dumps(
            document.as_json_object(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
        # Write, flush, and fsync the staged bytes before the swap
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Swap the staged file into place
        replace_file(temporary, self.path)

    def _read_document(self, path: Path) -> VersionedDocument:
        """Parse a record file into a validated versioned document."""
        # Parse the file and require an object root
        with open_shared(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("record root must be a JSON object")
        # Separate the envelope fields from the caller field values
        schema_version = data.pop("schema_version", None)
        revision = data.pop("revision", None)
        # Require integer schema and revision values within range
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise ValueError("schema_version must be an integer")
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("revision must be a non-negative integer")
        # Validate the remaining caller fields before building the document
        self._validate_fields(data)
        return VersionedDocument.create(schema_version, revision, data)

    def _validate_fields(self, fields: Mapping[str, Any]) -> None:
        """Reject empty, reserved, or non-JSON field names and values."""
        if any(not isinstance(key, str) or not key for key in fields):
            raise ValueError("record field names must be non-empty strings")
        # Reject field names reserved by the document envelope
        conflict = RESERVED_FIELDS.intersection(fields)
        if conflict:
            raise ValueError(f"record fields use reserved names: {sorted(conflict)}")
        # Require every field value to be JSON-serializable
        try:
            json.dumps(fields, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(f"record contains a non-JSON value: {error}") from error

    def _temporary_files(self) -> tuple[Path, ...]:
        """Return the leftover temporary files for this record in stable order."""
        # Report nothing when the record directory does not exist yet
        if not self.path.parent.exists():
            return ()
        # Match the staging names used during publication
        pattern = f".{self.path.name}.*.tmp"
        return tuple(sorted(self.path.parent.glob(pattern), key=lambda path: path.name))

