"""One-file-per-profile schema-v2 repository with v1 migration."""

from __future__ import annotations

import os
from pathlib import Path

from ..domain.models import RecordState, RecordUnavailable, RevisionConflict
from ..domain.profiles import ProfileInput, ProfileRecord, ProfileValidationError, validate_profile_id
from .json_store import VersionedJsonRepository
from .profile_migration import (
    MigrationHook,
    ProfileMigrationError,
    migrate_v1_document,
    migration_temporary,
    publish_migration,
    read_raw_profile,
    serialize_profile_document,
)


# Schema version this repository writes; older documents migrate up to it
PROFILE_SCHEMA_VERSION = 2


class ProfileNotFound(LookupError):
    """Raised when a requested profile record does not exist."""
    pass


class ProfileStorageError(RuntimeError):
    """Raised when profile storage is unusable or requires recovery."""
    def __init__(self, message: str, *, recovery_required: bool = False) -> None:
        """Store the message and whether the customer must recover storage."""
        self.recovery_required = recovery_required
        super().__init__(message)


class ProfileRepository:
    """Store each profile as its own versioned JSON file under one root."""
    def __init__(self, root: Path, migration_hook: MigrationHook | None = None) -> None:
        """Store the profile root and the optional migration hook."""
        self.root = root.resolve(strict=False)
        self._migration_hook = migration_hook

    def list(self) -> tuple[ProfileRecord, ...]:
        """Return every stored profile, rejecting duplicates and interrupted writes."""
        if not self.root.exists():
            return ()
        # Bring every stored file up to the current schema before listing
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name.casefold()):
            self._prepare_path(path)
        # Refuse to list while an interrupted write is present
        if any(self.root.glob(".*.json.*.tmp")):
            raise ProfileStorageError(
                "an interrupted profile write requires recovery", recovery_required=True,
            )
        records: list[ProfileRecord] = []
        identifiers: set[str] = set()
        # Load each record and reject duplicate identifiers
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name.casefold()):
            # Require the file name to satisfy the profile identifier rules
            try:
                identifier = validate_profile_id(path.stem)
            except ProfileValidationError as error:
                raise ProfileStorageError("a profile filename has an invalid identifier") from error
            record = self._load_path(path, identifier)
            folded = record.values.profile_id.casefold()
            if folded in identifiers:
                raise ProfileStorageError("duplicate profile identifiers were found")
            identifiers.add(folded)
            records.append(record)
        return tuple(records)

    def load(self, profile_id: object) -> ProfileRecord:
        """Load one profile record, rejecting missing or unreadable storage."""
        identifier = validate_profile_id(profile_id)
        path = self._path(identifier)
        self._prepare_path(path)
        inspection = self._repository(path).inspect()
        # Report absence specifically so callers can distinguish it
        if inspection.state == RecordState.MISSING:
            raise ProfileNotFound("profile was not found")
        return self._load_path(path, identifier)

    def save(self, values: ProfileInput, expected_revision: int | None) -> ProfileRecord:
        """Persist profile values and return the stored record revision."""
        # Reject unusable expected revisions before touching storage
        if expected_revision is not None and (
            not isinstance(expected_revision, int) or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise ProfileValidationError("expected_revision must be a non-negative integer or null")
        # Refuse a second file that differs only by identifier case
        self._reject_duplicate_identifier(values.profile_id)
        path = self._path(values.profile_id)
        self._prepare_path(path)
        # Translate an unavailable record into a profile storage error
        try:
            document = self._repository(path).save(ProfileRecord(0, values).fields(), expected_revision)
        except RecordUnavailable as error:
            raise self._unavailable(error) from error
        return ProfileRecord(document.revision, ProfileInput.parse(dict(document.fields)))

    def delete(self, profile_id: object, expected_revision: object) -> str:
        """Delete a profile only while its recorded revision still matches."""
        identifier = validate_profile_id(profile_id)
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ProfileValidationError("expected_revision must be a non-negative integer")
        record = self.load(identifier)
        # Refuse deletion when the profile changed since it was read
        if record.revision != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision}, current revision is {record.revision}"
            )
        try:
            self._path(identifier).unlink()
        except OSError as error:
            raise ProfileStorageError("profile could not be deleted") from error
        return identifier

    def _prepare_path(self, path: Path) -> None:
        """Recover or migrate a profile file so it is safe to inspect."""
        # Finish any interrupted migration before reading the record
        temporary = migration_temporary(path)
        if temporary.exists():
            self._recover_migration(path, temporary)
        if not path.exists():
            return
        try:
            raw = read_raw_profile(path)
        except ProfileMigrationError as error:
            raise ProfileStorageError(str(error), recovery_required=True) from error
        version = raw.get("schema_version")
        # Refuse records written by a newer application version
        if isinstance(version, int) and not isinstance(version, bool) and version > PROFILE_SCHEMA_VERSION:
            raise ProfileStorageError("profile record uses a future schema", recovery_required=True)
        # Migrate legacy version-one records before they are read
        if type(version) is int and version == 1:
            try:
                migrated = migrate_v1_document(raw)
                publish_migration(path, migrated, self._migration_hook)
            except (OSError, ProfileMigrationError) as error:
                raise ProfileStorageError(
                    "profile schema migration requires recovery", recovery_required=True,
                ) from error

    def _recover_migration(self, path: Path, temporary: Path) -> None:
        """Verify a staged migration and publish or discard it safely."""
        try:
            # Validate the staged document before trusting its content
            staged = read_raw_profile(temporary)
            staged_revision = staged.get("revision")
            if (
                not isinstance(staged_revision, int)
                or isinstance(staged_revision, bool)
                or staged_revision < 0
            ):
                raise ProfileMigrationError("migration staging revision is invalid")
            ProfileInput.parse({key: value for key, value in staged.items()
                                if key not in {"schema_version", "revision"}})
            if staged.get("schema_version") != PROFILE_SCHEMA_VERSION:
                raise ProfileMigrationError("migration staging has an unsupported schema")
            # Compare the staging area with the authoritative record
            if path.exists():
                authoritative = read_raw_profile(path)
                version = authoritative.get("schema_version")
                if type(version) is int and version == PROFILE_SCHEMA_VERSION:
                    expected_bytes = serialize_profile_document(authoritative).encode("utf-8")
                    if staged != authoritative or temporary.read_bytes() != expected_bytes:
                        raise ProfileMigrationError(
                            "migration staging conflicts with the authoritative record"
                        )
                    temporary.unlink()
                    return
                if type(version) is not int or version != 1:
                    raise ProfileMigrationError("migration staging does not match the source")
                expected = migrate_v1_document(authoritative)
                expected_bytes = serialize_profile_document(expected).encode("utf-8")
                if staged != expected or temporary.read_bytes() != expected_bytes:
                    raise ProfileMigrationError("migration staging does not match the source")
            # Publish the staged migration only when it matches the source
            os.replace(temporary, path)
        except (OSError, ProfileMigrationError, ProfileValidationError) as error:
            raise ProfileStorageError(
                "interrupted profile migration requires recovery", recovery_required=True,
            ) from error

    def _load_path(self, path: Path, expected_id: str) -> ProfileRecord:
        """Load and validate one profile document from its file."""
        inspection = self._repository(path).inspect()
        # Only a fully valid record may be returned to callers
        if inspection.state != RecordState.VALID or inspection.document is None:
            raise self._inspection_error(inspection.state)
        try:
            values = ProfileInput.parse(dict(inspection.document.fields))
        except ProfileValidationError as error:
            raise ProfileStorageError(
                "profile record contains invalid fields", recovery_required=True,
            ) from error
        # The stored identifier must match its file name
        if values.profile_id != expected_id:
            raise ProfileStorageError(
                "profile identifier does not match its filename", recovery_required=True,
            )
        return ProfileRecord(inspection.document.revision, values)

    def _reject_duplicate_identifier(self, profile_id: str) -> None:
        """Refuse another file that names the same profile identifier."""
        target = self._path(profile_id)
        for path in self.root.glob("*.json") if self.root.exists() else ():
            if path != target and path.stem.casefold() == profile_id.casefold():
                raise ProfileStorageError("duplicate profile identifier")

    def _path(self, profile_id: str) -> Path:
        """Return the file path that stores one profile identifier."""
        return self.root / f"{profile_id}.json"

    @staticmethod
    def _repository(path: Path) -> VersionedJsonRepository:
        """Return the versioned repository bound to one profile file."""
        return VersionedJsonRepository(path, PROFILE_SCHEMA_VERSION)

    @staticmethod
    def _inspection_error(state: RecordState) -> ProfileStorageError:
        """Map a record state to a profile error, flagging recovery needs."""
        return ProfileStorageError(
            f"profile record is unavailable: {state.value}",
            recovery_required=state in {
                RecordState.CORRUPT, RecordState.FUTURE_SCHEMA, RecordState.INTERRUPTED_WRITE,
            },
        )

    @classmethod
    def _unavailable(cls, error: RecordUnavailable) -> ProfileStorageError:
        """Translate an unavailable record into a profile storage error."""
        return cls._inspection_error(error.inspection.state)
