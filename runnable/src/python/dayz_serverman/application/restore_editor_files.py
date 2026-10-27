"""Values and pure parts of the same-profile editor restore (D3, D5, D19, AR-6, AR-7, leaf 10.10).

They are the restore inputs, the record bytes, the restored plan and the rebuilt baseline. They also include the
ledger entries of a carried last-applied record.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.backups import BackupManifest
from ..domain.mission_map_applied import record_version
from ..domain.mission_map_ledger import parse_entry, restore_entry_id
from ..domain.mission_map_plan import PlanForm, parse_plan
from ..domain.mission_map_records import sealed, utc_text
from ..repositories.mission_map_layout import FILE_SETS, TargetClass
from ..repositories.mission_map_owned import OWNED_ERRORS, owned_digest
from ..repositories.mission_map_plans import check_size
from ..repositories.restore_editor_targets import EditorWrite
from ..repositories.restore_storage import RestoreStorageError

# Operation kind of a ledger entry that a restore adds for a carried last-applied record (D3, D5)
LEDGER_KIND = "profile_restore"
# Provenance source of a working baseline that a restore builds again from the active originals (D19)
REBUILT_SOURCE = "profile_restore"


@dataclass(frozen=True)
class EditorRestore:
    """The editor files of one restore in publication order, and the summary that its preview binds."""

    writes: tuple[EditorWrite, ...]
    summary: dict[str, Any]
    # The Product Owner sentences of the preview (`editor_notes`), in review order
    notes: list[str]
    profile_id: str
    mission_key: str


@dataclass(frozen=True)
class EditorOwner:
    """The journal fields that `check_original_kinds` and the 10.8 record builders read."""

    operation_id: str
    profile_id: str
    mission_key: str
    runtime_profile_key: str | None


@dataclass(frozen=True)
class EditorContext:
    """The active association of the restoring profile and the folders of its two targets."""

    root: Path
    profile_id: str
    mission_root: str
    mission: Path
    mission_key: str
    # None when the runtime profile folder cannot be resolved; the runtime class then takes no part
    runtime: Path | None
    runtime_key: str | None


class EditorArchive:
    """The opened backup: each editor entry is read with the digest of its manifest entry."""

    def __init__(self, directory: Path, manifest: BackupManifest) -> None:
        """Keep the extracted tree and the digest of each entry."""
        self._directory = directory
        self._digests = {entry.path: entry.sha256 for entry in manifest.entries}

    def read(self, path: str) -> bytes:
        """Return the bytes of one entry, or refuse when they differ from the manifest."""
        data = read_bytes_shared(self._directory.joinpath(*path.split("/")))
        if self._digests.get(path) != sha(data):
            raise RestoreStorageError("BACKUP_INTEGRITY_FAILED", "Snapshot payload changed.")
        return data


def sha(data: bytes) -> str:
    """Return the SHA-256 digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def document_bytes(fields: Mapping[str, Any], schema_version: int, revision: int) -> bytes:
    """Return a record file in the form that VersionedJsonRepository publishes."""
    document = {"schema_version": schema_version, "revision": revision, **fields}
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def record_bytes(record: dict[str, Any], revision: int) -> bytes:
    """Return an editor record file; the envelope version is the version of its fields (8.7)."""
    return document_bytes(record, record_version(record), revision)


def restored_plan(data: bytes, association: tuple[str, str], current_revision: int | None) -> tuple[bytes, bool]:
    """Return the plan file that a restore installs, and whether it is a valid plan of the association.

    A valid plan gets the revision after the higher of the current and the archived revision (AR-7). So a plan
    revision never repeats at one association. Invalid saved data installs as stored (Architect ruling (g)).
    """
    try:
        raw = json.loads(data.decode("utf-8"))
        version, revision = raw.get("schema_version"), raw.get("revision")
        plan = parse_plan(raw.get("plan"), PlanForm.STORED)
        check_size(plan)
        valid = (version == 1 and type(revision) is int and revision >= 0 and set(raw) == {"schema_version",
                 "revision", "plan"} and (plan["association"]["profile_id"], plan["association"]["mission_key"])
                 == association)
    except (UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        valid = False
    if not valid:
        return data, False
    following = max(-1 if current_revision is None else current_revision, revision) + 1
    return document_bytes({"plan": raw["plan"]}, 1, following), True


def baseline_fits(baseline: dict[str, Any], active: Mapping[str, dict[str, Any] | None]) -> bool:
    """Return whether a working baseline was built on the manifest of the active original of each of its targets."""
    keys = baseline["target_keys"]
    if any(active.get(key) is None for key in keys):
        return False
    return baseline["originals"] == [{"target_key": key, "manifest_sha256": active[key]["manifest_sha256"]}
                                     for key in keys]


def rebuilt_baseline(profile_id: str, mission_key: str, mission_root: str, active: Mapping[str, dict[str, Any] | None],
                     operation_id: str, moment: datetime) -> dict[str, Any] | None:
    """Return a working baseline built again from the active originals, or None without a mission original.

    A target class without an active original has no part (Architect ruling (g)): that is its state before the
    first Apply on the target.
    """
    keys = [key for key, original in active.items() if original is not None]
    if mission_key not in keys:
        return None
    keys.sort(key=lambda key: key != mission_key)
    files = [dict(item, target_key=key) for key in keys for item in active[key]["files"]]
    paths = [item["path"] for key in keys for item in active[key]["files"]]
    return sealed({
        "profile_id": profile_id, "mission_key": mission_key, "mission_root": mission_root, "target_keys": keys,
        "originals": [{"target_key": key, "manifest_sha256": active[key]["manifest_sha256"]} for key in keys],
        "files": files, "provenance": [{"time": utc_text(moment), "source": REBUILT_SOURCE,
                                        "operation_id": operation_id, "paths": paths}]})


def applied_installs(applied: dict[str, Any], restored: Callable[[str, str], str | None]) -> bool:
    """Return whether each "files after" digest equals the restored file (D5).

    `restored` raises KeyError for a target key that is not a current target, so such a record never installs.
    """
    try:
        return all(restored(item["target_key"], item["path"]) == (item["sha256"] if item["existed"] else None)
                   for item in applied["files_after"])
    except KeyError:
        return False


def ledger_entries(applied: dict[str, Any], baseline: dict[str, Any] | None, contents: Callable[[str], bytes],
                   targets: Mapping[str, tuple[TargetClass, str]], *, profile_id: str, mission_root: str,
                   operation_id: str, moment: datetime) -> list[dict[str, Any]]:
    """Return one ledger entry for each current target whose files the carried last-applied record changed.

    The entry has the derived operation ID of D5, the carried operation ID and `output` true; this holds also
    when the restore invalidates the record. `targets` maps each current target key to its class and display path.
    """
    after = {(item["target_key"], item["path"]): item for item in applied["files_after"]}
    base = {} if baseline is None else {(item["target_key"], item["path"]): item for item in baseline["files"]}
    entries = []
    for key, (target_class, display) in targets.items():
        files = []
        for item in (item for item in applied["files_before"] if item["target_key"] == key):
            path, written, kept = item["path"], after.get((key, item["path"])), base.get((key, item["path"]))
            before = contents(item["sha256"]) if item["existed"] else None
            reference = contents(kept["sha256"]) if kept is not None and kept["existed"] else None
            file = {"path": path, "before_sha256": item["sha256"],
                    "after_sha256": written["sha256"] if written is not None else None,
                    "baseline_sha256": None if kept is None else kept["sha256"],
                    "owned_before_sha256": _owned(target_class, path, before),
                    "owned_after_sha256": None if written is None else written["owned_sha256"],
                    "owned_baseline_sha256": _owned(target_class, path, reference)}
            changed = (file["after_sha256"] != file["before_sha256"]
                       or file["owned_after_sha256"] != file["owned_before_sha256"])
            if changed and path in FILE_SETS[target_class]:
                files.append(file)
        if not files:
            continue
        entry = sealed({
            "operation_id": restore_entry_id(operation_id, applied["operation_id"]), "operation_kind": LEDGER_KIND,
            "recorded_at": utc_text(moment), "profile_id": profile_id, "target_class": target_class.value,
            "target_key": key, "display_path": display, "mission_root": mission_root, "output": True,
            "carried_operation_id": applied["operation_id"], "files": files})
        entries.append(parse_entry(entry, FILE_SETS[target_class]))
    return entries


def _owned(target_class: TargetClass, path: str, data: bytes | None) -> str | None:
    """Return the owned-value digest of a managed file, or None when its owned block is damaged."""
    try:
        return owned_digest(target_class, path, data)
    except OWNED_ERRORS:
        return None
