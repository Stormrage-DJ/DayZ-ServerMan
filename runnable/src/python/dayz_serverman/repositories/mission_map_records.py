"""Stores of the protected original, the working baseline and the last-applied record (D2, D3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from ..domain.mission_map_records import (
    RecordShapeError, identity, parse_applied, parse_baseline, parse_original, sealed,
)
from ..domain.models import RecordState, RepositoryError
from .json_store import StagingPolicy, VersionedJsonRepository
from .mission_map_contents import ContentStore
from .mission_map_layout import (
    APPLIED_FILE, BASELINE_FILE, FILE_SETS, TargetClass, association_folder, is_target_key, original_folder,
)


# Record file of a protected original inside its target folder
ORIGINAL_FILE = "original.json"


class RecordCorrupt(RepositoryError):
    """Raised when a stored record cannot be read or breaks its shape; it needs recovery, never a rewrite."""

    def __init__(self, path: Path, detail: str | None) -> None:
        """Store the record path and the reason."""
        self.path = path
        super().__init__(f"editor record {path.name} is not valid: {detail or 'unreadable'}")


class RecordConflict(RepositoryError):
    """Raised when a create-once record already exists with a different manifest digest."""
    pass


class RecordFile:
    """One versioned editor record file and the shape check of its kind."""

    def __init__(self, path: Path, parse: Callable[[dict[str, Any]], dict[str, Any]],
                 staging: StagingPolicy = StagingPolicy.OWNER) -> None:
        """Bind the record path, its shape check and the staging-file rule."""
        self.path = path
        self._parse = parse
        self._repository = VersionedJsonRepository(path, 1, staging=staging)

    def read(self) -> tuple[dict[str, Any], int] | None:
        """Return the published record and its revision, None when it was never written, or raise RecordCorrupt."""
        inspection = self._repository.inspect()
        document = inspection.document
        if document is None and inspection.state in (RecordState.MISSING, RecordState.INTERRUPTED_WRITE):
            return None
        if document is None or inspection.state not in (RecordState.VALID, RecordState.INTERRUPTED_WRITE):
            raise RecordCorrupt(self.path, inspection.detail)
        # Check the shape and the manifest digest of the published record
        try:
            return self._parse(dict(document.fields)), document.revision
        except RecordShapeError as error:
            raise RecordCorrupt(self.path, str(error)) from error

    def save(self, record: dict[str, Any], expected_revision: int | None) -> int:
        """Publish a checked record under the revision guard and return its revision."""
        return self._repository.save(record, expected_revision).revision


def _store_contents(store: ContentStore, entries: list[dict[str, Any]], contents: Mapping[str, bytes]) -> None:
    """Write the content of every existing file entry; the bytes must match the digest the entry names."""
    for entry in entries:
        if not entry["existed"]:
            continue
        data = contents.get(entry["sha256"])
        if data is None:
            raise ValueError(f"content of {entry['path']} is missing")
        if store.write(data) != entry["sha256"]:
            raise ValueError(f"content of {entry['path']} does not match its digest")


class OriginalStore:
    """Create-once protected original of one physical target (A4)."""

    def __init__(self, area: Path, target_key: str, *, staging: StagingPolicy = StagingPolicy.OWNER) -> None:
        """Bind the store to the original folder of one target key."""
        self.target_key = target_key
        self.folder = original_folder(area, target_key)
        self.contents = ContentStore(self.folder)
        self._file = RecordFile(self.folder / ORIGINAL_FILE, self._parse, staging)

    def read(self) -> dict[str, Any] | None:
        """Return the original of the target, or None when none was captured."""
        found = self._file.read()
        return None if found is None else found[0]

    def create(self, raw: dict[str, Any], contents: Mapping[str, bytes]) -> dict[str, Any]:
        """Write the original once; an identical rewrite does nothing and a different one is refused."""
        record = self._parse(sealed(raw))
        if record["target_key"] != self.target_key:
            raise ValueError("the original names another target key")
        # Only D5 restores may set another original active; Apply never replaces one
        existing = self.read()
        if existing is not None:
            if existing["manifest_sha256"] != record["manifest_sha256"]:
                raise RecordConflict("a different protected original already exists for this target")
            return existing
        _store_contents(self.contents, record["files"], contents)
        self._file.save(record, None)
        return record

    def identity(self) -> dict[str, Any] | None:
        """Return the reference form of the original for a plan's records object."""
        record = self.read()
        return None if record is None else identity("original", record)

    def _parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Check an original against the managed file set of its class."""
        try:
            file_set = FILE_SETS[TargetClass(raw.get("target_class"))]
        except ValueError as error:
            raise RecordShapeError("original target_class must be mission or runtime") from error
        return parse_original(raw, file_set)


class AssociationRecordStore:
    """Revision-guarded record of one profile and mission association: the baseline or the last apply."""

    def __init__(self, area: Path, profile_id: str, mission_key: str, kind: str, *,
                 staging: StagingPolicy = StagingPolicy.OWNER) -> None:
        """Bind the store to one association folder; kind is baseline or applied."""
        if kind not in ("baseline", "applied"):
            raise ValueError("kind must be baseline or applied")
        self.kind = kind
        self.profile_id = profile_id
        self.mission_key = mission_key
        self.folder = association_folder(area, profile_id, mission_key)
        self.contents = ContentStore(self.folder)
        name, parse = (BASELINE_FILE, parse_baseline) if kind == "baseline" else (APPLIED_FILE, parse_applied)
        self._file = RecordFile(self.folder / name, parse, staging)
        self._parse = parse

    def read(self) -> tuple[dict[str, Any], int] | None:
        """Return the record and its revision, or None when the association has none."""
        return self._file.read()

    def save(self, raw: dict[str, Any], contents: Mapping[str, bytes], expected_revision: int | None) -> int:
        """Seal, check and publish the record with its stored file contents; return the new revision."""
        record = self._parse(sealed(raw))
        if (record["profile_id"], record["mission_key"]) != (self.profile_id, self.mission_key):
            raise ValueError("the record names another profile or mission")
        # Contents come first, so a published record never names missing content
        stored = record["files"] if self.kind == "baseline" else record["files_before"]
        _store_contents(self.contents, stored, contents)
        return self._file.save(record, expected_revision)

    def identity(self) -> dict[str, Any] | None:
        """Return the reference form of the record for a plan's records object."""
        found = self.read()
        return None if found is None else identity(self.kind, found[0], found[1])


def association_profiles(area: Path, key: str) -> list[str]:
    """Return every profile that has an association folder for the key: one part of the D2 shared check."""
    profiles = area / "profiles"
    if not is_target_key(key) or not profiles.is_dir():
        return []
    return sorted(folder.name for folder in profiles.iterdir() if (folder / key).is_dir())


def record_mission_root(folder: Path) -> str | None:
    """Return the relative mission root that the last-applied or baseline record of a folder names, leniently."""
    # Read only published records; an unreadable record names no root
    for name in (APPLIED_FILE, BASELINE_FILE):
        inspection = VersionedJsonRepository(folder / name, 1, staging=StagingPolicy.OBSERVER).inspect()
        root = inspection.document.fields.get("mission_root") if inspection.document is not None else None
        if isinstance(root, str) and root:
            return root
    return None
