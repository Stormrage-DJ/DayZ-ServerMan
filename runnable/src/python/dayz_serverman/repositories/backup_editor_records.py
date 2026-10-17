"""Editor records of a profile backup: collect them, stage them and verify their inventory (D18, 10.9).

The records travel as plain files with their digests. They are the plan, the working baseline and the
last-applied record of the active association, and the protected originals of its target keys. Every
content file that one of these records names travels too. There are no receipts, no capsule reseal and
no shared witness.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.backup_editor_manifest import (
    ORIGINAL_ITEM_FIELDS, association_content, editor_field, is_editor_entry, original_content, original_entry,
    record_entry,
)
from ..domain.backups import BackupManifestError, ManifestEntry, entry_path_key
from ..domain.mission_map_applied import content_items, record_version
from ..domain.mission_map_records import parse_applied, parse_baseline, parse_original
from ..domain.models import RepositoryError
from .backup_errors import BackupStorageError
from .backup_verification import sha256_file
from .json_store import StagingPolicy
from .mission_map_layout import APPLIED_FILE, BASELINE_FILE, FILE_SETS, PLAN_FILE, TargetClass, association_folder
from .mission_map_plans import MissionMapPlanStore, PlanState
from .mission_map_records import ORIGINAL_FILE, AssociationRecordStore, OriginalStore

if TYPE_CHECKING:
    from ..domain.backups import BackupManifest


# Record file of each association record kind
RECORD_FILES = {"baseline": BASELINE_FILE, "applied": APPLIED_FILE}
# Shape check of each record kind that names content; a plan names none and travels as stored
PARSERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "baseline": parse_baseline, "applied": parse_applied,
    "original": lambda raw: parse_original(raw, FILE_SETS[TargetClass(raw.get("target_class"))]),
}


class EditorRecordsInvalid(BackupStorageError):
    """Raised when an editor record or content file is corrupt; the backup then carries no editor record."""

    def __init__(self) -> None:
        """Set the code and the diagnostic message."""
        super().__init__("EDITOR_RECORDS_INVALID", "An editor record of this profile is not valid.")


@dataclass(frozen=True)
class EditorArchive:
    """The `editor` field of a backup and the bytes of each `editor/` entry, in manifest order."""

    field: dict[str, Any]
    files: tuple[tuple[str, bytes], ...]

    @property
    def size(self) -> int:
        """Return the byte count of every editor entry."""
        return sum(len(data) for _path, data in self.files)


def collect_editor_records(area: Path, association: dict[str, Any], target_keys: Iterable[str]) -> EditorArchive | None:
    """Return the records of the association and the originals of its target keys, or None when it has no record.

    The caller holds the association lock, so an autosave or a commit cannot give a mixed set. Each record
    is the published file as read; its shape is checked from those same bytes. Superseded, restored and
    invalidated copies, retired and history folders, ledger entries and images are never read.
    """
    profile_id, mission_key = association["profile_id"], association["mission_key"]
    files: dict[str, bytes] = {}
    kinds: list[str] = []
    plan = _plan(MissionMapPlanStore(association_folder(area, profile_id, mission_key),
                                     staging=StagingPolicy.OBSERVER, association=(profile_id, mission_key)))
    if plan is not None:
        files[record_entry("plan")], kinds = plan, ["plan"]
    for kind, name in RECORD_FILES.items():
        store = AssociationRecordStore(area, profile_id, mission_key, kind)
        data = _published(store.folder / name)
        if data is None:
            continue
        record = _same_record(kind, data, _read(store.read))
        files[record_entry(kind)] = data
        kinds.append(kind)
        for digest in named_contents(kind, record):
            files[association_content(digest)] = _read(lambda: store.contents.read(digest))
    if not kinds:
        return None
    originals = []
    for key in sorted(set(target_keys)):
        original = OriginalStore(area, key)
        data = _published(original.folder / ORIGINAL_FILE)
        if data is None:
            continue
        record = _same_record("original", data, _read(lambda: (original.read(), None)))
        files[original_entry(key)] = data
        originals.append(record)
        for digest in named_contents("original", record):
            files[original_content(key, digest)] = _read(lambda: original.contents.read(digest))
    field = editor_field(profile_id, mission_key, association["mission_root"], kinds, originals)
    return EditorArchive(field, tuple(sorted(files.items(), key=lambda item: entry_path_key(item[0]))))


def stage_editor_files(archive: EditorArchive, staging: Path) -> tuple[ManifestEntry, ...]:
    """Write each editor entry into the staging tree and return its entry, with the digest of the staged bytes."""
    entries = []
    for path, data in archive.files:
        target = staging.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        digest = sha256_file(target)
        # The staged file must hold exactly the bytes that were read under the lock
        if digest != hashlib.sha256(data).hexdigest() or target.stat().st_size != len(data):
            raise BackupStorageError("BACKUP_INTEGRITY_FAILED", "A staged editor record failed verification.")
        entries.append(ManifestEntry(path, len(data), digest))
    return tuple(entries)


def verify_editor_entries(manifest: BackupManifest, read: Callable[[str], bytes]) -> None:
    """Check that the carried records match the field and that the content entries are exactly those they name.

    `BackupManifest.parse` already checked the field and the entry layout; this check reads the record bytes.
    A missing or extra content entry, or a record that disagrees with the field, raises BackupManifestError.
    """
    field = manifest.editor
    if field is None:
        return
    association = field["association"]
    named: set[str] = set()
    for kind in association["records"]:
        if kind == "plan":
            continue
        record = parse_record(kind, read(record_entry(kind)))
        if (record["profile_id"], record["mission_key"]) != (field["profile_id"], association["mission_key"]):
            raise BackupManifestError("a carried editor record names another association")
        named.update(association_content(digest) for digest in named_contents(kind, record))
    for item in field["originals"]:
        record = parse_record("original", read(original_entry(item["target_key"])))
        if {name: record[name] for name in ORIGINAL_ITEM_FIELDS} != item:
            raise BackupManifestError("a carried original disagrees with the editor field")
        named.update(original_content(item["target_key"], digest) for digest in named_contents("original", record))
    # The parse admitted only record entries and content entries below the editor prefix
    present = {entry.path for entry in manifest.entries if is_editor_entry(entry.path) and "/contents/" in entry.path}
    if present != named:
        raise BackupManifestError("the editor content entries differ from the content that the records name")


def parse_record(kind: str, data: bytes) -> dict[str, Any]:
    """Return the checked fields of one versioned record file; the envelope version must match its fields."""
    try:
        raw = json.loads(data.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("the record root is not an object")
        version, revision = raw.pop("schema_version", None), raw.pop("revision", None)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("the record revision is invalid")
        record = PARSERS[kind](raw)
        if record_version(record) != version or isinstance(version, bool):
            raise ValueError("the record version differs from its fields")
        return record
    except (UnicodeError, ValueError, TypeError, KeyError) as error:
        raise BackupManifestError(f"a carried {kind} record is invalid: {error}") from error


def named_contents(kind: str, record: dict[str, Any]) -> list[str]:
    """Return the digests of the content files that a record names, also through baseline provenance."""
    return sorted({item["sha256"] for item in content_items(kind, record) if item["existed"]})


def _published(path: Path) -> bytes | None:
    """Return the bytes of a published record file, or None when it does not exist."""
    try:
        return read_bytes_shared(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise BackupStorageError("BACKUP_SOURCE_INVALID", "An editor record of this profile cannot be read.") from error


def _plan(store: MissionMapPlanStore) -> bytes | None:
    """Return the bytes of a valid plan record, None without one; a plan that is not valid is invalid editor data."""
    data = _published(store.path)
    if data is None:
        return None
    state = store.inspect().state
    if state is PlanState.MISSING:
        return None
    if state is not PlanState.VALID:
        raise EditorRecordsInvalid()
    # The lock keeps an autosave out, so the checked file still holds the bytes that were read
    if _published(store.path) != data:
        raise BackupStorageError("BACKUP_SOURCE_CHANGED", "An editor record changed during backup creation.")
    return data


def _read(reader: Callable[[], Any]) -> Any:
    """Run a store read; a corrupt record or content is invalid editor data, and a read failure refuses."""
    try:
        return reader()
    except (RepositoryError, ValueError) as error:
        raise EditorRecordsInvalid() from error
    except OSError as error:
        raise BackupStorageError("BACKUP_SOURCE_INVALID", "An editor record of this profile cannot be read.") from error


def _same_record(kind: str, data: bytes, found: tuple[dict[str, Any], Any] | None) -> dict[str, Any]:
    """Return the record parsed from the archived bytes; the store must have read the same record."""
    try:
        record = parse_record(kind, data)
    except BackupManifestError as error:
        raise EditorRecordsInvalid() from error
    if found is None or found[0] != record:
        raise BackupStorageError("BACKUP_SOURCE_CHANGED", "An editor record changed during backup creation.")
    return record
