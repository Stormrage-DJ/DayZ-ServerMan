"""Apply preview of the map editor: one review of the saved plan over the current mission (leaf 8.6, R16, R17).

One fingerprint binds the review. It covers the plan revision, the profile
and settings revisions and the mission identity. It also covers the
capabilities with the family gate, the record identities, the file digests of
the freshness set and the candidates.

Apply (8.7) computes the review again under its locks and compares the
fingerprint; there is no preview cache and no timer. The preview writes
nothing. It reports refusals in its answer and raises only when it cannot read
the plan, the profile or the mission.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.mission_map_families import family_gate
from ..domain.mission_map_plan import bounds_of, canonical_json, configuration_fingerprint
from ..domain.mission_map_preview_changes import (
    applied_global, applied_group_names, applied_groups, applied_objects, object_changes, unavailable_encounters,
)
from ..domain.mission_map_preview_texts import NO_SAVED_PLAN_TEXT, PREVIEW_STALE_TEXT, infected_section
from ..domain.mission_map_terrain import TerrainCapabilities
from ..domain.models import RevisionConflict
from ..domain.profiles import ProfileRecord
from ..repositories.mission_map_incorporate import Incorporation, incorporation
from ..repositories.mission_map_layout import TargetClass
from ..repositories.mission_map_loot_report import build_loot_report
from ..repositories.mission_map_mapgroup_read import DIRT, POS, PROTO
from ..repositories.mission_map_owned import WRITABLE_MISSION_FILES, owned_digest
from ..repositories.mission_map_plans import PlanRecordInvalid, PlanState
from ..repositories.mission_map_preview_base import Base, PreviewRecords, build_base, file_changes, read_records
from ..repositories.mission_map_preview_candidates import PreviewCandidates, build_candidates
from ..repositories.mission_map_providers import read_encounter_providers
from ..repositories.mission_map_terrain import AREAFLAGS_FILE, read_capabilities
from .mission_configuration import _contained
from .mission_map_coordinator_records import MissionMapRecordReader
from .mission_map_plans import MissionMapPlanService, MissionPathChanged, _check_association, _runtime_key, plan_store
from .mission_map_preview_read import MissionInputs, read_inputs
from .mission_map_preview_targets import capture_check, other_profiles

# Version of the answer shape and of the fingerprint basis
PREVIEW_VERSION = 1


class ProfileList(Protocol):
    """The profile reads that the preview needs: one profile and every profile (A4 naming)."""

    def list(self) -> tuple[ProfileRecord, ...]:
        """Return every stored profile."""
        ...


class NoSavedPlan(ValueError):
    """Raised when the association has no saved plan: the preview reviews only a saved revision."""


class PreviewStale(RevisionConflict):
    """Raised when a review again under Apply gives another fingerprint than the reviewed preview."""


@dataclass(frozen=True)
class ReviewContext:
    """What one review read and derived; Apply (8.7) builds its records and its incorporations from it."""

    root: Path
    mission: Path
    association: dict[str, Any]
    plan: dict[str, Any]
    plan_revision: int
    profile_revision: int
    settings_revision: int
    terrain_id: str | None
    records: PreviewRecords
    base: Base
    medical: bytes | None
    unavailable: frozenset[str]
    incorporations: dict[str, Incorporation]


@dataclass(frozen=True)
class PreviewReview:
    """One complete review: the answer with its fingerprint, and the candidates that Apply would publish."""

    answer: dict[str, Any]
    fingerprint: str
    candidates: PreviewCandidates
    inputs: MissionInputs
    context: ReviewContext | None = None


class MissionMapPreview:
    """Build the apply preview of a profile's saved plan; reads only, owner session (A11)."""

    def __init__(self, plans: MissionMapPlanService, profiles: ProfileList, *,
                 medical_baseline: Callable[[str, Path], Path | None],
                 capabilities: Callable[[Path], TerrainCapabilities] = read_capabilities) -> None:
        """Keep the plan service, the profile list, the medical baseline lookup (6.3) and the capability reader."""
        self._plans, self._profiles = plans, profiles
        self._medical_baseline, self._capabilities = medical_baseline, capabilities
        self._records = MissionMapRecordReader(plans._area)

    def preview(self, profile_id: object, expected_revision: int | None = None) -> dict[str, Any]:
        """Return the preview answer with its fingerprint."""
        return self.review(profile_id, expected_revision).answer

    def revalidate(self, profile_id: object, fingerprint: str) -> PreviewReview:
        """Review again and require the reviewed fingerprint; a changed context raises PreviewStale."""
        review = self.review(profile_id)
        if review.fingerprint != fingerprint:
            raise PreviewStale(PREVIEW_STALE_TEXT)
        return review

    def review(self, profile_id: object, expected_revision: int | None = None) -> PreviewReview:
        """Read the saved plan and the mission, build every candidate and return the bound review."""
        area = self._plans._area
        profile, root, _relative, mission = self._plans._resolve(profile_id)
        settings_revision = self._plans._settings.load().revision
        plan, revision, association = self._saved_plan(profile, root, expected_revision)
        key, pid = association["mission_key"], profile.values.profile_id
        # A fresh capability read of the target mission gives the family gate, never the plan terrain ID (6.11)
        capabilities = self._capabilities(mission)
        gate = family_gate(capabilities.terrain_id)
        inputs = read_inputs(mission)
        records = read_records(area, pid, key)
        _fingerprint, stale = self._records.snapshot(plan)
        baseline_path = self._medical_baseline(pid, mission)
        medical = None if baseline_path is None else read_bytes_shared(baseline_path)
        base = build_base(area, pid, key, records, inputs.managed)
        providers = read_encounter_providers(root, mission, profile, containment=_contained)
        unavailable = unavailable_encounters(plan["objects"], providers)
        candidates = build_candidates(
            plan, base.files, current_proto=inputs.managed[PROTO], medical=medical, limits=inputs.limits,
            events=inputs.events, globals_xml=inputs.globals_xml, clusters=inputs.clusters,
            unavailable={item["id"] for item in unavailable})
        applied = records.applied_record
        report = build_loot_report(
            plan, capabilities=capabilities, areaflags=candidates.areaflags, mapgroups=candidates.mapgroups,
            baseline={path: candidates.base[path] for path in (AREAFLAGS_FILE, PROTO, POS, DIRT)},
            clusters=inputs.clusters, types=inputs.types_documents(), current_groups=applied_group_names(applied))
        external, coordinated = file_changes(inputs.managed, base, medical)
        # Each external change says whether Incorporate is available, with the 8.7 rule (PO 2026-10-12 23:42:49)
        incorporations = {item["path"]: incorporation(
            item["path"], inputs.managed[item["path"]], base.files[item["path"]],
            base.references[item["path"]].owned_sha256, applied_groups(applied)) for item in external}
        external = [dict(item, incorporate=incorporations[item["path"]].to_dict()) for item in external]
        profiles = self._profiles.list()
        capture = capture_check(area, key, inputs.managed, {record.values.profile_id for record in profiles})
        refusals = _refusals(report, candidates, gate, capture, stale)
        answer = {
            "version": PREVIEW_VERSION,
            "profile": {"profile_id": pid, "display_name": profile.values.display_name, "revision": profile.revision},
            "settings_revision": settings_revision,
            "plan": {"plan_id": plan["plan_id"], "revision": revision,
                     "configuration_fingerprint": configuration_fingerprint(plan)},
            "mission": {"mission_name": mission.name, "mission_root": association["mission_root"], "mission_key": key},
            "capabilities": {**capabilities.to_dict(bounds_of(plan), plan["terrain"]["bounds_confirmed"]),
                             "family": gate.to_dict(), "encounter_providers": providers},
            "bounds": {**plan["terrain"], "detected": capabilities.to_dict()["detected_bounds"]},
            "refused": bool(refusals), "refusals": refusals,
            "choices": {"external_changes": [item["path"] for item in external],
                        "unavailable_encounters": [item["id"] for item in unavailable if item["choice_required"]]},
            "base": base.source,
            "objects": {**object_changes(plan["objects"], applied_objects(applied)),
                        "global": {"current": plan["global_abundance"], "applied": applied_global(applied)}},
            "loot": report,
            "encounters": {"written": _written(candidates), "unavailable": unavailable},
            "infected": infected_section(candidates.caps, candidates.infected_written()),
            "affected_files": _affected(inputs.managed, candidates.contents()),
            "external_changes": external, "coordinated_changes": coordinated,
            "other_profiles": other_profiles(area, root, profiles, pid, key),
            "capture": capture,
            "inputs": {"files": inputs.freshness(capture=capture["required"]), "records": records.identities(),
                       "medical_baseline": None if medical is None else hashlib.sha256(medical).hexdigest()},
        }
        fingerprint = _fingerprint_of(answer, root)
        context = ReviewContext(root, mission, association, plan, revision, profile.revision, settings_revision,
                                capabilities.terrain_id, records, base, medical,
                                frozenset(item["id"] for item in unavailable), incorporations)
        return PreviewReview(dict(answer, fingerprint=fingerprint), fingerprint, candidates, inputs, context)

    @property
    def capabilities(self) -> Callable[[Path], TerrainCapabilities]:
        """Return the capability reader; Apply makes its own fresh read with it under the guard (6.11)."""
        return self._capabilities

    def _saved_plan(self, profile: ProfileRecord, root: Path,
                    expected_revision: int | None) -> tuple[dict[str, Any], int, dict[str, Any]]:
        """Return the valid saved plan of the active association, its revision and the association."""
        loaded = self._plans.load(profile.values.profile_id)
        if loaded["path_change"] is not None:
            raise MissionPathChanged(loaded["path_change"]["folders"], loaded["read_only_reason"])
        association = loaded["association"]
        if loaded["state"] == PlanState.MISSING.value:
            raise NoSavedPlan(NO_SAVED_PLAN_TEXT)
        if loaded["state"] != PlanState.VALID.value:
            store = plan_store(self._plans._area, association["profile_id"], association["mission_key"])
            raise PlanRecordInvalid(store.inspect())
        if expected_revision is not None and expected_revision != loaded["revision"]:
            raise RevisionConflict("the saved plan has another revision than the editor")
        plan = loaded["plan"]
        # The QA75-O1 bounds rules hold: a valid saved plan passed every D1 and bounds check when it was read
        _check_association(plan["association"], association, lambda: _runtime_key(root, profile))
        return plan, loaded["revision"], association


def _refusals(report: dict[str, Any], candidates: PreviewCandidates, gate: Any, capture: dict[str, Any],
              stale: list[str]) -> list[dict[str, Any]]:
    """Return every refusal of the complete Apply: candidates, family gate, capture check and stale records."""
    result = [dict(item, object_id=None) for item in report["refusals"]] + candidates.territory_refusals()
    if not gate.verified:
        result.append(_refusal(gate.reason, None, gate.text))
    if capture["result"] == "refused":
        result.append(_refusal(capture["reason"], None, capture["message"]))
    if stale:
        result.append(_refusal("records_stale", None, "Editor records changed. Reload the plan: " + ", ".join(stale)))
    return result


def _refusal(reason: str, file: str | None, message: str) -> dict[str, Any]:
    """Return one refusal entry in the shape of the loot report refusals."""
    return {"reason": reason, "file": file, "source": None, "field": None, "placements": [], "message": message,
            "object_id": None}


def _affected(current: dict[str, bytes | None], contents: dict[str, bytes | None]) -> list[dict[str, Any]]:
    """Return each writable file whose candidate differs from the current file; captured-only files never do."""
    result = []
    for path in WRITABLE_MISSION_FILES:
        if path not in contents or contents[path] == current[path]:
            continue
        before, after = current[path], contents[path]
        result.append({"path": path, "target_class": TargetClass.MISSION.value,
                       "exists_before": before is not None, "exists_after": after is not None,
                       "sha256_before": _digest(before), "sha256_after": _digest(after),
                       "owned_sha256_after": owned_digest(TargetClass.MISSION, path, after)})
    return result


def _fingerprint_of(answer: dict[str, Any], root: Path) -> str:
    """Return the versioned digest of the complete review and the DayZ root that it read."""
    return hashlib.sha256(canonical_json({"version": PREVIEW_VERSION, "dayz_root": str(root),
                                          "answer": answer})).hexdigest()


def _written(candidates: PreviewCandidates) -> list[str]:
    """Return the IDs of the infected encounters that the territory candidate writes."""
    return list(candidates.territory.encounter_ids) if candidates.infected_written() else []


def _digest(data: bytes | None) -> str | None:
    """Return SHA-256 of the bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()

