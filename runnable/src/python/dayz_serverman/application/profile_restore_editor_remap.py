"""Pure parts of a restore as a new profile: the destination target keys and the remapped records (D5, D19).

The plan, the working baseline and the last-applied record of the archive name the archived profile and target
keys. A restore as a new profile remaps them to the new profile ID, the destination mission key and the new runtime
profile key. Object identifiers and file digests stay unchanged, because the restore copies the archived bytes.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from ..domain.mission_map_plan import PlanForm, parse_plan
from ..domain.mission_map_records import sealed
from ..repositories.mission_map_layout import TargetClass, target_key
from ..repositories.mission_map_plans import check_size
from .restore_editor_files import document_bytes


def future_folder(root: Path, relative: str) -> Path:
    """Return the resolved path of a folder below the DayZ root that the restore can still create.

    The nearest existing folder is resolved strictly, so the key equals the key of the folder once it exists.
    """
    path = root.joinpath(*PureWindowsPath(relative).parts)
    rest: list[str] = []
    while not path.exists():
        rest.insert(0, path.name)
        path = path.parent
    return path.resolve(strict=True).joinpath(*rest)


def future_key(target_class: TargetClass, root: Path, relative: str) -> str:
    """Return the target key of a destination folder, also before the restore creates it."""
    return target_key(target_class, future_folder(root, relative))


def remapped_plan(data: bytes, association: Mapping[str, Any]) -> tuple[bytes, bool]:
    """Return the plan file of the new profile, and whether the archived plan was valid.

    A valid plan names the new association and no record references; its objects stay unchanged. Its revision
    follows the archived one, because the new association has no current plan. Invalid saved data installs as
    stored (Architect ruling (g)).
    """
    try:
        raw = json.loads(data.decode("utf-8"))
        plan = parse_plan(raw.get("plan"), PlanForm.STORED)
        check_size(plan)
        revision = raw["revision"]
        valid = raw.get("schema_version") == 1 and type(revision) is int and revision >= 0 and set(raw) == {
            "schema_version", "revision", "plan"}
    except (UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        valid = False
    if not valid:
        return data, False
    stored = copy.deepcopy(raw["plan"])
    stored["association"] = dict(association)
    stored["records"] = {"originals": [], "baseline": None, "applied": None}
    return document_bytes({"plan": stored}, 1, revision + 1), True


def remapped_baseline(baseline: dict[str, Any], keys: Mapping[str, str], active: Mapping[str, dict[str, Any] | None],
                      *, profile_id: str, mission_key: str, mission_root: str) -> dict[str, Any]:
    """Return the archived working baseline with the new profile, target keys and active original manifests."""
    targets = [keys[key] for key in baseline["target_keys"]]
    return sealed({
        **{name: value for name, value in baseline.items() if name != "manifest_sha256"},
        "profile_id": profile_id, "mission_key": mission_key, "mission_root": mission_root, "target_keys": targets,
        "originals": [{"target_key": key, "manifest_sha256": active[key]["manifest_sha256"]}  # type: ignore[index]
                      for key in targets],
        "files": [dict(item, target_key=keys[item["target_key"]]) for item in baseline["files"]]})


def remapped_applied(applied: dict[str, Any], keys: Mapping[str, str], baseline: dict[str, Any] | None, *,
                     profile_id: str, mission_key: str, mission_root: str) -> dict[str, Any]:
    """Return the archived last-applied record for the new association; it names the installed baseline.

    A target key outside the archived association stays as archived (Architect 2026-10-17 07:28:52 in evidence
    10.37). The record then never installs, and it gets no ledger entry for that target.
    """
    def mapped(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the file items with their destination target keys."""
        return [dict(item, target_key=keys.get(item["target_key"], item["target_key"])) for item in items]
    fields = {name: value for name, value in applied.items() if name != "manifest_sha256"}
    fields.update(profile_id=profile_id, mission_key=mission_key, mission_root=mission_root,
                  files_after=mapped(applied["files_after"]), files_before=mapped(applied["files_before"]))
    if baseline is not None:
        fields.update(baseline_revision=0, baseline_manifest_sha256=baseline["manifest_sha256"])
    return sealed(fields)
