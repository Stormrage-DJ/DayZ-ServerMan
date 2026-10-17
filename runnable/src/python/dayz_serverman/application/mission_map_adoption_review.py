"""Review of the adoption of a moved, copied or restored mission (leaf 10.13, D2 "Adoption Rules (10.2)", AR-6).

The review finds the case and the route. It finds the source key that the report names (condition (b)), and
the old folders of this profile that adoption retires. The report is the path-change report or the capture
refusal. For `move` and `copy` the review compares each managed file with the source original and with the
source-key output entries (OP-4).

The answer holds the Product Owner texts of the case and route, and one fingerprint binds it. The review writes
nothing. The commit of leaf 10.35 builds its records from the context.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain import mission_map_adoption_texts as texts
from ..domain.mission_map_adoption import (
    ADOPTION_REFUSED, ADOPTION_VERSION, CAPTURE, COPY, KEPT_DIFFERENT, KEPT_SAME, MOVE, NOTHING_TO_ADOPT, PATH_CHANGE,
    RESTORE, RETIRED, RETURNED, capture_case, path_change_case,
)
from ..domain.mission_map_ledger import file_entry
from ..domain.mission_map_plan import canonical_json
from ..domain.mission_map_records import COPIED_FIELDS
from ..domain.mission_map_values import IDENTIFIER
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_layout import (
    APPLIED_FILE, FILE_SETS, TargetClass, association_folder, has_invalidated, history_name, ledger_folder,
)
from ..repositories.mission_map_ledger import LedgerStore
from ..repositories.mission_map_plans import PlanState
from ..repositories.mission_map_records import AssociationRecordStore, OriginalStore
from .mission_map_plans import MissionMapPlanService, _check_association, load_runtime_key
from .mission_map_preview import ProfileList, PreviewStale
from .mission_map_preview_read import read_optional
from .mission_map_preview_targets import capture_check, other_profiles
from .mission_map_sharing import other_targets

OBSERVER = StagingPolicy.OBSERVER


@dataclass(frozen=True)
class AdoptionContext:
    """What one review found; the commit of 10.35 builds its records from it."""

    case: str | None
    route: str | None
    root: Path
    mission: Path
    association: dict[str, Any]
    source_key: str | None
    retire_keys: tuple[str, ...]
    matched_operations: tuple[str, ...]
    existing_folder: bool
    plan_revision: int | None
    records_only: bool = False
    original_step: str | None = None
    kept_original: str | None = None


@dataclass(frozen=True)
class AdoptionReview:
    """One complete adoption review: the answer with its fingerprint, and the context of the commit."""

    answer: dict[str, Any]
    fingerprint: str
    context: AdoptionContext


class AdoptionReader:
    """Build the adoption review of a profile's current mission; reads only, owner session (A11, AR-1)."""

    def __init__(self, plans: MissionMapPlanService, profiles: ProfileList, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        """Keep the plan service, the profile list and the clock of the dated history name."""
        self._plans, self._profiles = plans, profiles
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def revalidate(self, profile_id: object, fingerprint: str) -> AdoptionReview:
        """Review again and require the reviewed fingerprint; any relevant change raises the ADOPT-28 text."""
        review = self.review(profile_id)
        if review.fingerprint != fingerprint:
            raise PreviewStale(texts.ADOPTION_STALE_TEXT)
        return review

    def review(self, profile_id: object) -> AdoptionReview:
        """Find the case and the route, compare the files of a move or a copy, and return the bound review."""
        area = self._plans._area
        profile, root, _relative, mission = self._plans._resolve(profile_id)
        settings_revision = self._plans._settings.load().revision
        loaded = self._plans.load(profile.values.profile_id)
        association, records = loaded["association"], self._profiles.list()
        pid, key, change = association["profile_id"], association["mission_key"], loaded["path_change"]
        # The review agrees with the load: records that do not match the settings refuse (Architect 10:11:41)
        if change is None and loaded["state"] == PlanState.VALID.value:
            _check_association(loaded["plan"]["association"], association, lambda: load_runtime_key(root, profile))
        current: dict[str, bytes | None] = {}
        runtime: dict[str, bytes | None] = {}
        capture, refusals, source, retire, case, route = None, [], None, [], None, None
        records_only, kept = False, None
        # The path state and the records give the case; the capture check runs only when they offer nothing
        if change is not None:
            source = change["shown_mission_key"]
            if loaded["read_only_reason"] == RETURNED:
                case, retire = RETIRED, [source]
            else:
                case, route = path_change_case(*source_facts(area, root, records, pid, source)), PATH_CHANGE
                retire = [folder["mission_key"] for folder in change["folders"]]
        elif has_invalidated(association_folder(area, pid, key)):
            case = RESTORE
        else:
            current = _files(mission, TargetClass.MISSION)
            capture = capture_check(area, key, current, {record.values.profile_id for record in records})
            if capture_case(capture) is not None:
                case, route, source = COPY, CAPTURE, capture["match"]["target_key"]
            else:
                no_source = capture["reason"] == "editor_output"
                refusals.append(_refusal(ADOPTION_REFUSED if no_source else NOTHING_TO_ADOPT,
                                         texts.NO_SOURCE_TEXT if no_source else texts.NOTHING_TO_ADOPT_TEXT))
        old_path = (capture["match"]["display_path"] if route == CAPTURE else
                    _display(area, source, change["folders"][0]["mission_root"]) if source else None)
        matched: set[str] = set()
        if case in (MOVE, COPY):
            original = OriginalStore(area, source, staging=OBSERVER).read()
            applied = (association_folder(area, pid, source) / APPLIED_FILE).exists()
            if original is None and route == PATH_CHANGE and not applied:
                # No Apply wrote files there, so there is nothing to prove and no original to copy (Architect
                # 2026-10-17 09:30:36 in evidence 10.13, point 5); ADOPT-24 stays for damaged records. No record
                # proves that the old folder is gone, so the case is always copy (Architect 10:11:41)
                records_only, case = True, COPY
            elif original is None:
                refusals.append(_refusal(ADOPTION_REFUSED, texts.NO_SOURCE_TEXT))
            else:
                # An original that the new key already has is kept (Architect 2026-10-17 11:42:47, evidence 10.35)
                own = OriginalStore(area, key, staging=OBSERVER).read() if route == PATH_CHANGE else None
                kept = None if own is None else KEPT_SAME if own["files"] == original["files"] else KEPT_DIFFERENT
                current = current or _files(mission, TargetClass.MISSION)
                compared = [(source, original, current)]
                compared += _runtime(area, root, profile, pid, source) if route == PATH_CHANGE else []
                runtime = {f"runtime:{path}": data for _key, _original, files in compared[1:]
                           for path, data in files.items()}
                for source_key, source_original, files in compared:
                    for path in _differing(area, source_key, source_original, files, matched):
                        message = texts.FILES_DIFFER_TEXT[case].format(path=path, old_path=old_path)
                        refusals.append(_refusal(ADOPTION_REFUSED, message, path))
        existing = route == PATH_CHANGE and association_folder(area, pid, key).is_dir()
        stamp = history_name(key, self._clock()) if existing or case == RETIRED else None
        values = {"old_path": old_path, "new_path": str(mission), "other_path": old_path, "history_name": stamp,
                  "count": len(matched)}
        others = other_profiles(area, root, records, pid, source) if case in (MOVE, COPY) else []
        retired = [_display(area, folder, association["mission_root"]) for folder in retire]
        step = _original_step(area, root, records, pid, key, source) if case == RETIRED else None
        answer = {
            "version": ADOPTION_VERSION,
            "profile": {"profile_id": pid, "display_name": profile.values.display_name, "revision": profile.revision},
            "settings_revision": settings_revision,
            "mission": {"mission_name": mission.name, "mission_root": association["mission_root"], "mission_key": key,
                        "display_path": str(mission)},
            "case": case, "route": route, "records_only": records_only, "source": None if source is None else {
                "mission_key": source, "display_path": old_path},
            "refused": bool(refusals), "refusals": refusals,
            **texts.review_texts(case, route, values, retired, existing=existing, others=bool(others),
                                 records_only=records_only, original_step=step, kept_original=kept),
            "other_profiles": others, "kept_original": kept,
            "retire": retire, "existing_folder": existing, "history_name": stamp, "ledger": sorted(matched),
            "plan": {"state": loaded["state"], "revision": loaded["revision"]} if change is not None else None,
            "capture": capture,
            "inputs": {"files": {path: _digest(data) for path, data in sorted({**current, **runtime}.items())},
                       "records": _record_state(area, pid, [key, *retire, *([source] if source else [])], key, source)},
        }
        basis = canonical_json({"version": ADOPTION_VERSION, "dayz_root": str(root), "answer": answer})
        # The dated history name follows the clock; the commit names its own, so the name is not bound
        fingerprint = hashlib.sha256(basis.replace(stamp.encode(), b"{history_name}") if stamp else basis).hexdigest()
        context = AdoptionContext(case, route, root, mission, association, source, tuple(retire),
                                  tuple(sorted(matched)), existing, loaded["revision"] if change is not None else None,
                                  records_only, step, kept)
        return AdoptionReview(dict(answer, fingerprint=fingerprint), fingerprint, context)


def _original_step(area: Path, root: Path, records: Iterable[Any], profile_id: str, key: str,
                   source: str) -> str | None:
    """Return the D5 outcome of step 4 of the retired case for the original of the active key, or None for no row.

    The chain-end original is the incoming original. An incoming original with the same content as the current
    one changes nothing. A key that another profile resolves to or has records for is shared (D2, D5).
    """
    incoming = OriginalStore(area, source, staging=OBSERVER).read()
    current = OriginalStore(area, key, staging=OBSERVER).read()
    if incoming is None:
        return None
    if current is None:
        return "installed"
    if all(incoming[name] == current[name] for name in COPIED_FIELDS):
        return None
    records = list(records)
    shared = other_targets(root, records, profile_id).sharing(TargetClass.MISSION, key) or other_profiles(
        area, root, records, profile_id, key)
    return "shared" if shared else "active"


def _differing(area: Path, key: str, original: dict[str, Any], files: dict[str, bytes | None],
               matched: set[str]) -> list[str]:
    """Return the managed files that equal neither the source original nor a source-key output (OP-4).

    The operation IDs of the output entries whose after digest a file equals join `matched`. An absent file
    matches only an absent file of the original, as the capture check never matches a null digest (D2).
    """
    outputs = [entry for entry in _entries(area, key) if entry["output"]]
    saved = {item["path"]: (item["existed"], item["sha256"]) for item in original["files"]}
    differing = []
    for path, data in files.items():
        digest = _digest(data)
        if (data is not None, digest) == saved.get(path):
            continue
        hits = [entry["operation_id"] for entry in outputs
                if digest is not None and (file_entry(entry, path) or {}).get("after_sha256") == digest]
        matched.update(hits)
        if not hits:
            differing.append(path)
    return differing


def _entries(area: Path, key: str) -> list[dict[str, Any]]:
    """Return the ledger entries of one target key, ordered by operation ID."""
    store = LedgerStore(area, staging=OBSERVER)
    found = (store.read(key, path.stem) for path in sorted(ledger_folder(area, key).glob("*.json"))
             if IDENTIFIER.fullmatch(path.stem) is not None)
    return [entry for entry in found if entry is not None]


def _runtime(area: Path, root: Path, profile: Any, profile_id: str,
             source: str) -> list[tuple[str, dict[str, Any], dict[str, bytes | None]]]:
    """Return the runtime class to compare when the old folder of this profile names a runtime key with an original."""
    found = AssociationRecordStore(area, profile_id, source, "baseline", staging=OBSERVER).read()
    keys = found[0]["target_keys"][1:] if found is not None else []
    original = OriginalStore(area, keys[0], staging=OBSERVER).read() if keys else None
    if original is None:
        return []
    relative = profile.values.runtime_profile
    folder = root.joinpath(*PureWindowsPath(relative).parts) if relative else None
    files = {path: read_optional(folder, path) if folder is not None and folder.is_dir() else None
             for path in FILE_SETS[TargetClass.RUNTIME]}
    return [(keys[0], original, files)]


def _files(folder: Path, target_class: TargetClass) -> dict[str, bytes | None]:
    """Return the bytes of each managed file of a class, or None for an absent file (A12 reads)."""
    return {path: read_optional(folder, path) for path in FILE_SETS[target_class]}


def _display(area: Path, key: str, fallback: str) -> str:
    """Return the display path that the original of a key names; without one, the relative mission root."""
    original = OriginalStore(area, key, staging=OBSERVER).read()
    return original["display_path"] if original is not None else fallback


def source_facts(area: Path, root: Path, records: Iterable[Any], profile_id: str, source: str) -> tuple[bool, bool]:
    """Return whether the mission folder of a source key still exists, and whether another profile resolves to it.

    The folder check is a path check of the display path of the source original, not a file read (OP-2). A key
    without an original counts as existing. A profile whose mission cannot be resolved counts as sharing the key.
    So an unknown fact gives `copy`, which never moves records.
    """
    original = OriginalStore(area, source, staging=OBSERVER).read()
    exists = original is None or Path(original["display_path"]).is_dir()
    return exists, bool(other_targets(root, records, profile_id).sharing(TargetClass.MISSION, source))


def _record_state(area: Path, profile_id: str, keys: Iterable[str], active: str, source: str | None) -> dict[str, Any]:
    """Return the digest of each record file of the named folders, of both originals and of the source ledger."""
    folders = {f"profile/{key}": association_folder(area, profile_id, key) for key in dict.fromkeys(keys)}
    folders["original/active"] = OriginalStore(area, active, staging=OBSERVER).folder
    if source is not None:
        folders.update({"original": OriginalStore(area, source, staging=OBSERVER).folder,
                        "ledger": ledger_folder(area, source)})
    return {name: {path.name: hashlib.sha256(read_bytes_shared(path)).hexdigest()
                   for path in sorted(folder.glob("*.json")) if path.is_file()} if folder.is_dir() else None
            for name, folder in folders.items()}


def _refusal(reason: str, message: str, file: str | None = None) -> dict[str, Any]:
    """Return one refusal entry in the shape of the apply preview refusals (8.6)."""
    return {"reason": reason, "file": file, "source": None, "field": None, "placements": [], "message": message,
            "object_id": None}


def _digest(data: bytes | None) -> str | None:
    """Return SHA-256 of the bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()
