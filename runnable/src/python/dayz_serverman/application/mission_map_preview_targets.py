"""Shared-target naming and the original capture check of the apply preview (8.6, A4, D2).

A target is shared when another profile resolves to it or has an association
folder for it. The preview names every such profile (A4 condition 1). Before
the first Apply on a target, the preview runs the capture check of the commit
step on the current files. It states the residual risk of that check.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..domain.mission_map_ledger import refusal
from ..domain.mission_map_preview_texts import CAPTURE_RISK_TEXT
from ..domain.profiles import ProfileRecord
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_commit_capture import OriginalCaptureRefused
from ..repositories.mission_map_layout import MISSION_FILE_SET, TargetClass, original_folder, target_key
from ..repositories.mission_map_ledger import LedgerStore
from ..repositories.mission_map_owned import OWNED_ERRORS, check_reserved, owned_digest
from ..repositories.mission_map_records import association_profiles
from .mission_configuration import resolve_profile_mission

# Errors of a profile whose mission cannot be resolved; such a profile resolves to no target
UNRESOLVED_ERRORS = (OSError, RuntimeError, ValueError)


def other_profiles(area: Path, root: Path, records: Iterable[ProfileRecord], profile_id: str,
                   mission_key: str) -> list[dict[str, Any]]:
    """Return every other profile that resolves to the mission target or has an association folder for it."""
    known = {record.values.profile_id: record for record in records}
    found = set(association_profiles(area, mission_key)) - {profile_id}
    for identifier, record in known.items():
        if identifier == profile_id or identifier in found:
            continue
        try:
            _relative, mission = resolve_profile_mission(root, record)
            if target_key(TargetClass.MISSION, mission) == mission_key:
                found.add(identifier)
        except UNRESOLVED_ERRORS:
            continue
    # A deleted profile can keep its association folder; it is named without a display name
    return [{"profile_id": identifier, "display_name": known[identifier].values.display_name
             if identifier in known else None} for identifier in sorted(found)]


def capture_check(area: Path, mission_key: str, current: dict[str, bytes | None],
                  existing_profiles: set[str]) -> dict[str, Any]:
    """Return the result of the original capture check of the current mission files.

    The check runs only while the target has no protected original. It uses
    the output ledger and the D8 reserved-name checks, as the commit step does.
    """
    if (original_folder(area, mission_key) / "original.json").exists():
        return {"required": False, "result": "not_required", "reason": None, "message": None, "match": None,
                "adoption_required": False, "files": [], "text": None}
    files = [{"path": path, "existed": current[path] is not None,
              "sha256": None if current[path] is None else hashlib.sha256(current[path]).hexdigest()}
             for path in MISSION_FILE_SET]
    result = {"required": True, "result": "passed", "reason": None, "message": None, "match": None,
              "adoption_required": False, "files": files, "text": CAPTURE_RISK_TEXT}
    ledger = LedgerStore(area, staging=StagingPolicy.OBSERVER)
    for item in files:
        data = current[item["path"]]
        if data is None:
            continue
        try:
            owned = owned_digest(TargetClass.MISSION, item["path"], data)
            matches = ledger.capture_matches(TargetClass.MISSION, item["path"], item["sha256"], owned)
            if matches:
                report = refusal(matches[0], existing_profiles)
                adoption = (original_folder(area, report["target_key"]) / "original.json").exists()
                error = OriginalCaptureRefused(report, adoption_required=adoption)
                return dict(result, result="refused", reason="editor_output", message=str(error), match=report,
                            adoption_required=adoption)
            check_reserved(TargetClass.MISSION, item["path"], data)
        except OWNED_ERRORS as error:
            return dict(result, result="refused", reason=getattr(error, "reason", "reserved_conflict"),
                        message=str(error))
    return result
