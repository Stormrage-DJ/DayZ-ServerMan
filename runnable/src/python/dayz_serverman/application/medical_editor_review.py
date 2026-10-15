"""Review of a medical loot-zone toggle on a mission that the map editor manages (leaf 8.13).

The review reads the writable managed files, the published editor records of
the association and the loot-zone baseline. It refuses with the R17 reason in
three cases (Architect 2026-10-15 01:18:47). The current last-applied record
does not match the files. Its realization is unknown: null, a version 1 record
or no record. The generated groups of the file differ from that realization.

The review then builds the candidate: the 6.3 patcher writes the ordinary
groups, and the clone rule regenerates every generated group.

The preview fingerprint (customer decision 4) is SHA-256 of the D3 canonical
JSON of this basis, version 1:

- `request`: the six request fields of the toggle;
- `target`: the resolved DayZ root, the mission root and the mission key;
- `files`: the whole-file digest of each writable managed file, null when absent;
- `medical`: the digest of the off source (the stored baseline, else the current file);
- `records`: the original manifest, the revision and manifest of the baseline and
  applied records, and the file digest of every ledger entry of the target;
- `associations`: each association folder of the mission key with the file digest
  of its last-applied record, null without one;
- `candidate`: the digest of the candidate `mapgroupproto.xml`.

A missing record is explicit null. The comparison is stateless: equal contexts give equal fingerprints.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.medical_editor_texts import MISMATCH_TEXT, NO_APPLIED_PLAN_TEXT, ZoneToggleRefused
from ..domain.mission_map_applied import GROUPS_FIELD
from ..domain.mission_map_plan import canonical_json
from ..domain.mission_map_values import SHA256
from ..repositories.medical_features import project_zones
from ..repositories.medical_zone_clones import CloneMismatch, regenerate_clones
from ..repositories.medical_zone_fragments import has_zone_changes, zones_enabled
from ..repositories.mission_map_layout import APPLIED_FILE, TargetClass, ledger_folder, target_key
from ..repositories.mission_map_mapgroup_read import PROTO
from ..repositories.mission_map_owned import WRITABLE_MISSION_FILES
from ..repositories.mission_map_preview_base import PreviewRecords, read_records
from .mission_map_preview_read import read_optional

# Version of the fingerprint basis
FINGERPRINT_VERSION = 1
# Reasons of the R17 refusal
RECORD_MISSING, REALIZATION_UNKNOWN, FILES_DIFFER = "applied_record_missing", "realization_unknown", "files_differ"


@dataclass(frozen=True)
class ZoneTarget:
    """A validated zone toggle request on an editor-managed mission and its resolved target."""

    profile_id: str
    profile_revision: int
    settings_revision: int
    root: Path
    mission_root: str
    mission: Path
    enabled: bool
    digest: str
    # The baseline that the toggle reads (manager store, then legacy), and the manager-owned baseline path
    baseline: Path
    owned_baseline: Path

    def request(self) -> dict[str, Any]:
        """Return the six request fields that the fingerprint binds."""
        return {"profile_id": self.profile_id, "feature": "medical_loot_zones", "enabled": self.enabled,
                "expected_profile_revision": self.profile_revision,
                "expected_settings_revision": self.settings_revision, "expected_digest": self.digest}


@dataclass(frozen=True)
class ZoneReview:
    """What the review read and the candidate that the toggle would publish."""

    fingerprint: str
    key: str
    files: dict[str, bytes | None]
    medical: bytes
    records: PreviewRecords
    candidate: bytes
    changed: bool

    @property
    def current(self) -> bytes:
        """Return the current `mapgroupproto.xml`."""
        data = self.files[PROTO]
        assert data is not None
        return data


def fingerprint_form(value: object) -> bool:
    """Return whether a value has the form of a zone review fingerprint: 64 lowercase hexadecimal digits.

    Another value counts as a missing fingerprint (Product Owner 2026-10-15 06:27:10 in evidence 8.13).
    """
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def review_zone_toggle(area: Path, target: ZoneTarget) -> ZoneReview:
    """Return the review of one toggle; raise ZoneToggleRefused with the R17 text when the records do not match."""
    key = target_key(TargetClass.MISSION, target.mission)
    files = {path: read_optional(target.mission, path) for path in WRITABLE_MISSION_FILES}
    current = files[PROTO]
    if current is None:
        raise FileNotFoundError("mapgroupproto.xml is missing")
    records = read_records(area, target.profile_id, key)
    groups = _realization(records, files, key)
    # Without a stored baseline the current file is the off state, as the first Enable stores it
    medical = read_bytes_shared(target.baseline) if target.baseline.is_file() else current
    changed = has_zone_changes(current, medical)
    old = zones_enabled(current, medical)
    patched = project_zones(current, medical, target.enabled) if changed else current
    try:
        candidate = regenerate_clones(current, patched, medical, groups, old, target.enabled if changed else old)
    except CloneMismatch as error:
        raise ZoneToggleRefused(error.reason, MISMATCH_TEXT) from error
    basis = {"version": FINGERPRINT_VERSION, "request": target.request(),
             "target": {"dayz_root": str(target.root), "mission_root": target.mission_root, "mission_key": key},
             "files": {path: _sha(data) for path, data in files.items()}, "medical": _sha(medical),
             "records": dict(records.identities(), ledger=_digests(ledger_folder(area, key).glob("*.json"))),
             "associations": _associations(area, key), "candidate": _sha(candidate)}
    fingerprint = hashlib.sha256(canonical_json(basis)).hexdigest()
    return ZoneReview(fingerprint, key, files, medical, records, candidate, changed and candidate != current)


def _realization(records: PreviewRecords, files: dict[str, bytes | None], key: str) -> list[dict[str, Any]]:
    """Return the realization of the current applied record; refuse when it is unknown or the files differ (R17)."""
    applied = records.applied_record
    if applied is None:
        raise ZoneToggleRefused(RECORD_MISSING, NO_APPLIED_PLAN_TEXT)
    after = {item["path"]: item for item in applied["files_after"] if item["target_key"] == key}
    for path, data in files.items():
        item = after.get(path)
        if item is None or (item["existed"], item["sha256"]) != (data is not None, _sha(data)):
            raise ZoneToggleRefused(FILES_DIFFER, MISMATCH_TEXT)
    groups = applied.get(GROUPS_FIELD)
    if groups is None:
        raise ZoneToggleRefused(REALIZATION_UNKNOWN, MISMATCH_TEXT)
    return list(groups)


def _associations(area: Path, key: str) -> list[list[Any]]:
    """Return each association folder of the mission key with the digest of its last-applied record file."""
    folders = sorted((area / "profiles").glob(f"*/{key}"))
    return [[folder.parent.name, _sha(read_bytes_shared(folder / APPLIED_FILE))
             if (folder / APPLIED_FILE).is_file() else None] for folder in folders if folder.is_dir()]


def _digests(paths: Any) -> list[list[str | None]]:
    """Return the name and file digest of each path, sorted by name."""
    return [[path.name, _sha(read_bytes_shared(path))] for path in sorted(paths) if path.is_file()]


def _sha(data: bytes | None) -> str | None:
    """Return SHA-256 of the bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()
