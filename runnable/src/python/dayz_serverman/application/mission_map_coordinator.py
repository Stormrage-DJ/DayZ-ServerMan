"""Serve profile-scoped editor load and save calls without an operation lane or game-file writer."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.mission_map_plan import (
    PlanForm, bounds_of, configuration_fingerprint, has_draft_changes, new_identifier, parse_plan,
)
from ..domain.mission_map_terrain import TerrainCapabilities
from ..domain.mission_map_values import PLAN_FORMAT, PLAN_VERSION
from ..repositories.mission_map_plans import MissionMapPlanStore, PlanRecordInvalid, PlanState
from ..repositories.mission_map_layout import association_folder
from ..repositories.mission_map_terrain import read_capabilities
from ..repositories.mission_map_providers import read_encounter_providers
from .mission_map_coordinator_errors import exact_parameters, refusal, translate
from .mission_map_coordinator_records import MissionMapRecordReader
from .mission_map_plans import AssociationMismatch, MissionMapPlanService
from .mission_configuration import _contained


def unknown_terrain_id(folder_name: str) -> str:
    """Return the D1-safe unknown terrain ID of a mission folder, limited to 64 characters."""
    name = re.sub(r"[^A-Za-z0-9._-]", "", folder_name)
    return ("unknown-" + name)[:64] if name else "unknown"


class MissionMapCoordinator:
    """Adapt the plan service and terrain snapshot to the editor's load and save contract."""

    def __init__(self, plans: MissionMapPlanService, *,
                 capabilities: Callable[[Path], TerrainCapabilities] = read_capabilities) -> None:
        """Keep the plan service, its manager area and a read-only capability reader."""
        self._plans = plans
        self._capabilities = capabilities
        self._records = MissionMapRecordReader(plans._area)

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the two handlers for later registration; construction registers nothing."""
        return {"load_mission_map": self.load_mission_map, "save_mission_map_plan": self.save_mission_map_plan}

    def load_mission_map(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Load one profile's plan and capabilities without creating an association folder."""
        exact_parameters(parameters, {"profile_id"})
        try:
            # Resolve the active mission and keep any earlier association read only
            profile, root, _relative, mission = self._plans._resolve(parameters["profile_id"])
            loaded = self._plans.load(profile.values.profile_id)
            capabilities = self._capabilities(mission)
            plan = loaded["plan"]
            if loaded["state"] == PlanState.MISSING.value and not loaded["read_only"]:
                plan = _new_plan(loaded["association"], mission.name, capabilities)
            # Reject a valid document stored in another association instead of leaking its records
            if plan is not None:
                shown = (loaded["path_change"] or {}).get("shown_mission_key", loaded["association"]["mission_key"])
                if (plan["association"]["profile_id"], plan["association"]["mission_key"]) != (
                        profile.values.profile_id, shown):
                    raise AssociationMismatch("The saved plan names another profile or mission association.")
            # Invalid saved data is visible and kept; explicit path-change reasons take precedence
            invalid = loaded["state"] not in (PlanState.MISSING.value, PlanState.VALID.value)
            reason = loaded["read_only_reason"] or ("invalid_saved_data" if invalid else None)
            loaded.update(read_only=loaded["read_only"] or invalid, read_only_reason=reason)
            fingerprint, stale = self._records.snapshot(plan) if plan is not None else (None, [])
            if stale:
                loaded["detail"] = "Editor records changed. Reload the plan: " + ", ".join(stale)
                loaded["problems"].append({"object_id": None, "rule": "records_stale", "message": loaded["detail"]})
            # Browser documents never receive local paths, but keep their image reference and calibration
            public_plan = copy.deepcopy(plan)
            if public_plan is not None and public_plan["background"] is not None:
                public_plan["background"]["path"] = None
            bounds = bounds_of(plan) if plan is not None else None
            confirmed = plan["terrain"]["bounds_confirmed"] if plan is not None else False
            # Provider presence is independent of terrain editing and never enables unverified bandit placement
            snapshot = {**capabilities.to_dict(bounds, confirmed),
                        "encounter_providers": read_encounter_providers(root, mission, profile, containment=_contained)}
            return {**loaded, "profile": {"profile_id": profile.values.profile_id,
                                         "display_name": profile.values.display_name},
                    "mission_name": mission.name, "plan": public_plan,
                    "capabilities": snapshot,
                    "draft_changes": has_draft_changes(plan, fingerprint) if plan is not None else False}
        except ApplicationCallError:
            raise
        except Exception as error:
            raise translate(error) from error

    def save_mission_map_plan(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and save a draft synchronously under only the plan service's association lock."""
        exact_parameters(parameters, {"profile_id", "plan", "expected_revision", "background_selection_id"})
        try:
            # Refuse caller-provided paths before D1 parsing; the native image picker owns paths
            raw = parameters["plan"]
            background = raw.get("background") if isinstance(raw, dict) else None
            if isinstance(background, dict) and background.get("path") is not None:
                message = "A plan sent to save must not name an image path. Choose the image in the map editor."
                raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", message,
                              problems=[{"object_id": None, "rule": "background_path_not_accepted", "message": message}])
            plan = parse_plan(raw, PlanForm.STORED)
            if parameters["background_selection_id"] is not None:
                raise refusal(ErrorCode.INVALID_REQUEST, "selection_unknown", "Choose the image again.")
            applied_fingerprint: str | None = None

            def prepare(locked_plan: dict[str, Any]) -> None:
                """Check D3 identities and preserve an accepted image path while the association lock is held."""
                nonlocal applied_fingerprint
                # Refuse invalid saved bytes before testing references or preserving a native selection
                folder = association_folder(self._plans._area, locked_plan["association"]["profile_id"],
                                            locked_plan["association"]["mission_key"])
                inspection = MissionMapPlanStore(folder).inspect()
                if inspection.state not in (PlanState.MISSING, PlanState.VALID):
                    raise PlanRecordInvalid(inspection)
                applied_fingerprint = self._records.require_fresh(locked_plan)
                # A null path preserves the native selection only when its digest still matches
                stored = inspection.plan
                existing = stored["background"] if stored is not None else None
                image = locked_plan["background"]
                if image is not None and existing is not None and image["sha256"] == existing["sha256"]:
                    image["path"] = existing["path"]

            # The service repeats path-change checks and takes the one existing association lock
            saved = self._plans.save(parameters["profile_id"], plan, parameters["expected_revision"], before_save=prepare)
            document = saved["plan"]
            return {"association": saved["association"], "revision": saved["revision"],
                    "draft_changes": has_draft_changes(document, applied_fingerprint),
                    "fingerprint": configuration_fingerprint(document),
                    "interrupted_evidence": saved["interrupted_evidence"]}
        except ApplicationCallError:
            raise
        except Exception as error:
            raise translate(error) from error


def _new_plan(association: dict[str, Any], mission_name: str,
              capabilities: TerrainCapabilities) -> dict[str, Any] | None:
    """Build an unsaved neutral document only when a valid header gives actual bounds."""
    detected = capabilities.to_dict()["detected_bounds"]
    if detected is None:
        return None
    return {"format": PLAN_FORMAT, "version": PLAN_VERSION, "plan_id": new_identifier(),
            "association": copy.deepcopy(association),
            "terrain": {"terrain_id": capabilities.terrain_id or unknown_terrain_id(mission_name),
                        "bounds": detected, "bounds_source": "detected", "bounds_confirmed": False},
            "global_abundance": 0, "objects": [], "background": None,
            "records": {"originals": [], "baseline": None, "applied": None}}
