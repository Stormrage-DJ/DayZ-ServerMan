"""Read the source records of an adoption: originals, baselines, contents and the runtime part (leaf 10.35).

Every read is an observer read of the manager root or an A12 read of a mission file. Nothing here writes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PureWindowsPath
from typing import Any

from ..domain.mission_map_applied import content_items
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_contents import ContentStore
from ..repositories.mission_map_journal import MapJournal
from ..repositories.mission_map_records import AssociationRecordStore, OriginalStore
from .mission_map_plans import load_runtime_key, plan_store
from .mission_map_preview_read import read_optional

OBSERVER = StagingPolicy.OBSERVER


def file_digest(folder: Path, path: str) -> str | None:
    """Return SHA-256 of a managed file of a folder, or None for an absent file (A12 read)."""
    data = read_optional(folder, path)
    return None if data is None else hashlib.sha256(data).hexdigest()


def runtime_part(area: Path, root: Path, profile: Any, profile_id: str, source: str) -> tuple[str, str] | None:
    """Return the runtime folder and key of the journal when the source records name a runtime part, else None."""
    named = len(baseline_keys(area, profile_id, source)) > 1
    inspection = plan_store(area, profile_id, source, StagingPolicy.OBSERVER).inspect()
    named = named or (inspection.plan is not None and inspection.plan["association"]["runtime_profile_key"] is not None)
    relative = profile.values.runtime_profile
    if not named or relative is None:
        return None
    try:
        return relative, load_runtime_key(root, profile)
    except ValueError:
        # Without a runtime folder there is no runtime part; the baseline is then built from the mission original
        return None


def runtime_folder(root: Path, journal: MapJournal) -> Path:
    """Return the runtime profile folder of the journal."""
    return root.joinpath(*PureWindowsPath(journal.runtime_profile or "").parts)


def baseline_keys(area: Path, profile_id: str, key: str) -> list[str]:
    """Return the target keys of the working baseline of an association folder, or none."""
    found = AssociationRecordStore(area, profile_id, key, "baseline", staging=OBSERVER).read()
    return [] if found is None else list(found[0]["target_keys"])


def active_original(area: Path, key: str) -> dict[str, Any] | None:
    """Return the active original of a key, or None."""
    return OriginalStore(area, key, staging=OBSERVER).read()


def stored_contents(folder: Path, kind: str, record: dict[str, Any]) -> dict[str, bytes]:
    """Return the verified content of each stored file item of a record in its association folder."""
    store = ContentStore(folder)
    return {item["sha256"]: store.read(item["sha256"]) for item in content_items(kind, record) if item["existed"]}
