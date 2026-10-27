"""Records, candidate base and reference states of the apply preview (8.6, D3, A6, R17).

Every candidate starts from the base. The base is the working baseline when
the association has one. Without one, a target with a protected original uses
that original, because the first Apply builds the working baseline from it (D3). Otherwise it is the current mission files,
which the first Apply captures. The ordinary medical children of the base take
the state of the current `mapgroupproto.xml` (Architect 2026-10-12 23:41:40, F1).

The reference of a writable file is what the editor last knew of it: the
"files after" entry of the last-applied record, else the base entry. A current
file can differ from its reference. The difference is a coordinated manager
change when only the medical feature made it (A6), else an external edit (R17).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .atomic_file import ContentChangedError
from .configuration_common import ConfigurationFileError
from .json_store import StagingPolicy
from .medical_features import project_zones
from .medical_zone_fragments import zones_enabled
from .mission_map_layout import MISSION_FILE_SET, TargetClass
from .mission_map_mapgroup_read import PROTO
from .mission_map_owned import OWNED_ERRORS, WRITABLE_MISSION_FILES, owned_digest
from .mission_map_records import AssociationRecordStore, OriginalStore

# Sources of the base: the working baseline, an existing protected original, or the current files
BASE_BASELINE, BASE_ORIGINAL, BASE_CURRENT = "baseline", "original", "current"
# Errors of the 6.3 classifier and patcher: mixed, unmappable or structurally different state, or layout
MEDICAL_ERRORS = (ContentChangedError, ConfigurationFileError)


@dataclass(frozen=True)
class PreviewRecords:
    """Published editor records of one association and its mission target, read without staging files."""

    original: dict[str, Any] | None
    baseline: tuple[dict[str, Any], int] | None
    applied: tuple[dict[str, Any], int] | None

    @property
    def applied_record(self) -> dict[str, Any] | None:
        """Return the last-applied record without its revision."""
        return None if self.applied is None else self.applied[0]

    def identities(self) -> dict[str, Any]:
        """Return the manifest identities that the preview fingerprint binds; absence is explicit null."""
        return {"original": None if self.original is None else self.original["manifest_sha256"],
                "baseline": None if self.baseline is None else [self.baseline[1], self.baseline[0]["manifest_sha256"]],
                "applied": None if self.applied is None else [self.applied[1], self.applied[0]["manifest_sha256"]]}


@dataclass(frozen=True)
class Reference:
    """What the editor last knew of one writable file: existence, digest and owned-value digest."""

    existed: bool
    sha256: str | None
    owned_sha256: str | None
    source: str


@dataclass(frozen=True)
class Base:
    """Base bytes of the managed files, their source, and the reference of each writable file."""

    source: str
    files: dict[str, bytes | None]
    references: dict[str, Reference]


def read_records(area: Path, profile_id: str, mission_key: str) -> PreviewRecords:
    """Read the published original, working baseline and last-applied record; a corrupt record raises."""
    def store(kind: str) -> AssociationRecordStore:
        return AssociationRecordStore(area, profile_id, mission_key, kind, staging=StagingPolicy.OBSERVER)
    original = OriginalStore(area, mission_key, staging=StagingPolicy.OBSERVER).read()
    return PreviewRecords(original, store("baseline").read(), store("applied").read())


def build_base(area: Path, profile_id: str, mission_key: str, records: PreviewRecords,
               current: dict[str, bytes | None]) -> Base:
    """Return the base of the candidates and the reference of each writable file."""
    if records.baseline is not None:
        folder = AssociationRecordStore(area, profile_id, mission_key, "baseline", staging=StagingPolicy.OBSERVER)
        entries, contents, source = records.baseline[0]["files"], folder.contents, BASE_BASELINE
    elif records.original is not None:
        store = OriginalStore(area, mission_key, staging=StagingPolicy.OBSERVER)
        entries, contents, source = records.original["files"], store.contents, BASE_ORIGINAL
    else:
        return Base(BASE_CURRENT, dict(current), {})
    stored = {entry["path"]: entry for entry in entries if entry.get("target_key", mission_key) == mission_key}
    files = {path: contents.read(stored[path]["sha256"]) if path in stored and stored[path]["existed"] else None
             for path in MISSION_FILE_SET}
    after = {} if records.applied is None else {
        item["path"]: item for item in records.applied[0]["files_after"] if item["target_key"] == mission_key}
    references = {}
    for path in WRITABLE_MISSION_FILES:
        if path in after:
            item = after[path]
            references[path] = Reference(item["existed"], item["sha256"], item["owned_sha256"], "applied")
        else:
            data = files[path]
            references[path] = Reference(data is not None, _digest(data), _owned(path, data), source)
    return Base(source, files, references)


def medical_base(base: bytes | None, current: bytes | None, medical: bytes | None) -> bytes | None:
    """Return the base `mapgroupproto.xml` with the medical state of the current file (F1).

    The 6.3 classifier reads the state of the current file; the 6.3 patcher
    sets it on the base, with off children from the medical baseline. Without
    a medical baseline both files read as their own off form, as the toggle
    reads them, so nothing changes. A mixed, unmappable or structurally
    different state raises the 6.3 refusal.
    """
    if base is None or current is None or medical is None:
        return base
    state = zones_enabled(current, medical)
    if zones_enabled(base, medical) == state:
        return base
    return project_zones(base, medical, state)


def medical_only(current: bytes, reference: Reference, medical: bytes | None) -> bool:
    """Return whether a medical toggle of the current file alone explains its difference from the reference."""
    if medical is None or not reference.existed:
        return False
    try:
        state = zones_enabled(current, medical)
        return _digest(project_zones(current, medical, not state)) == reference.sha256
    except MEDICAL_ERRORS + (ValueError,):
        return False


def file_changes(current: dict[str, bytes | None], base: Base,
                 medical: bytes | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the external edits and the coordinated manager changes of the writable files.

    A captured-only file is never an external change (Architect 2026-10-14
    12:16:06). A first Apply without records has no reference, so it has no change.
    """
    external, coordinated = [], []
    for path, reference in base.references.items():
        data = current[path]
        if (data is not None, _digest(data)) == (reference.existed, reference.sha256):
            continue
        if path == PROTO and data is not None and medical_only(data, reference, medical):
            coordinated.append({"path": path, "feature": "medical_loot_zones", "reference": reference.source})
            continue
        owned = _owned(path, data)
        external.append({"path": path, "reference": reference.source, "existed_before": reference.existed,
                         "exists_now": data is not None, "sha256_before": reference.sha256, "sha256_now": _digest(data),
                         "owned_changed": owned is None and data is not None or owned != reference.owned_sha256})
    return external, coordinated


def _owned(path: str, data: bytes | None) -> str | None:
    """Return the owned-value digest, or None when the owned block is damaged."""
    try:
        return owned_digest(TargetClass.MISSION, path, data)
    except OWNED_ERRORS:
        return None


def _digest(data: bytes | None) -> str | None:
    """Return SHA-256 of the bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()
