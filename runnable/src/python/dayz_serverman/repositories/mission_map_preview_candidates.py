"""Candidate files of the apply preview over its base (8.6; builders of 6.6, 6.8 and 7.5).

Every builder starts from the base, so a reapply never compounds. The
ordinary medical children of the base take the current state before the
mapgroup builder runs (F1). Unavailable encounters become R11 choices before
the territory builder runs, so no unavailable encounter is written. This
module reads and writes no file; the caller supplies the bytes.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.mission_map_areaflags_candidate import AreaflagsCandidate, build_areaflags_candidate
from ..domain.mission_map_evaluation_shapes import loot_shape
from ..domain.mission_map_terrain import AREAFLAGS_MISSING, LIMITS_MISSING, TerrainDataError
from .mission_map_infected import (
    TerritoryCandidate, TerritoryRefused, build_territory_candidate, infected_caps, infected_encounters,
)
from .mission_map_mapgroup_candidate import MEDICAL_STATE, MapgroupCandidates, build_mapgroup_candidates
from .mission_map_mapgroup_read import DIRT, POS, PROTO
from .mission_map_mapgroup_routing import MapgroupRefusal
from .mission_map_owned import TERRITORY_FILE
from .mission_map_preview_base import MEDICAL_ERRORS, medical_base
from .mission_map_terrain import AREAFLAGS_FILE, parse_value_flags


@dataclass(frozen=True)
class PreviewCandidates:
    """The candidates of one review, or their refusals, and the infected caps."""

    base: dict[str, bytes | None]
    areaflags: AreaflagsCandidate | TerrainDataError | None
    mapgroups: MapgroupCandidates
    territory: TerritoryCandidate | TerritoryRefused
    caps: dict[str, Any]

    def contents(self) -> dict[str, bytes | None]:
        """Return the candidate bytes of each writable file that a builder built; None keeps a file absent."""
        result: dict[str, bytes | None] = {}
        if not isinstance(self.areaflags, TerrainDataError):
            result[AREAFLAGS_FILE] = None if self.areaflags is None else self.areaflags.content
        result.update({item.path: item.content for item in self.mapgroups.files})
        if isinstance(self.territory, TerritoryCandidate):
            result[TERRITORY_FILE] = self.territory.content
        return result

    def infected_written(self) -> bool:
        """Return whether the territory candidate writes at least one included encounter."""
        return isinstance(self.territory, TerritoryCandidate) and bool(self.territory.encounter_ids)

    def territory_refusals(self) -> list[dict[str, Any]]:
        """Return the refusal of the territory file in the shape of the loot report refusals."""
        if not isinstance(self.territory, TerritoryRefused):
            return []
        return [{"reason": self.territory.reason, "file": TERRITORY_FILE, "source": None, "field": None,
                 "placements": [], "message": str(self.territory), "object_id": self.territory.object_id}]


def build_candidates(plan: dict[str, Any], base: Mapping[str, bytes | None], *, current_proto: bytes | None,
                     medical: bytes | None, limits: bytes | None, events: bytes | None, globals_xml: bytes | None,
                     clusters: Mapping[str, bytes], unavailable: Collection[str]) -> PreviewCandidates:
    """Return the candidates of a validated D1 plan over the base bytes of the writable files."""
    files = {path: base.get(path) for path in (AREAFLAGS_FILE, PROTO, POS, DIRT, TERRITORY_FILE)}
    try:
        files[PROTO] = medical_base(files[PROTO], current_proto, medical)
        mapgroups = build_mapgroup_candidates(plan, {path: files[path] for path in (PROTO, POS, DIRT)},
                                              medical_baseline=medical, maximum=None, clusters=dict(clusters))
    except MEDICAL_ERRORS as error:
        # The builder never sees a base with an old medical state; the 6.3 reason refuses instead
        refusal = MapgroupRefusal(MEDICAL_STATE, PROTO, None, None, (), str(error))
        mapgroups = MapgroupCandidates((), (), (), (), (refusal,))
    # An unavailable encounter is an R11 choice: it is written only after support returns
    objects = [dict(item, excluded=True) if item["id"] in unavailable else item for item in plan["objects"]]
    territory, caps = _territory(files[TERRITORY_FILE], objects, events, globals_xml)
    return PreviewCandidates(files, _areaflags(plan, files[AREAFLAGS_FILE], limits), mapgroups, territory, caps)


def _areaflags(plan: dict[str, Any], base: bytes | None,
               limits: bytes | None) -> AreaflagsCandidate | TerrainDataError | None:
    """Return the tier candidate, its refusal, or None when the mission has no areaflags.map and needs none."""
    tiers = any(shape.tier is not None for item in plan["objects"] if (shape := loot_shape(item)) is not None)
    if not tiers:
        # Without a tier override the builder keeps the base bytes and parses nothing (D9)
        return None if base is None else build_areaflags_candidate(base, plan, ())
    try:
        if base is None:
            raise TerrainDataError(AREAFLAGS_MISSING, "the mission has no areaflags.map")
        if limits is None:
            raise TerrainDataError(LIMITS_MISSING, "the mission has no cfglimitsdefinition.xml")
        return build_areaflags_candidate(base, plan, parse_value_flags(limits))
    except TerrainDataError as error:
        return error


def _territory(base: bytes | None, objects: list[dict[str, Any]], events: bytes | None,
               globals_xml: bytes | None) -> tuple[TerritoryCandidate | TerritoryRefused, dict[str, Any]]:
    """Return the territory candidate or its refusal, and the caps of the included infected encounters."""
    try:
        candidate = build_territory_candidate(base, objects, events=events, globals_xml=globals_xml)
        return candidate, candidate.caps
    except TerritoryRefused as error:
        # The caps stay readable also when the file refuses; the 7.5 reader gives them from the same bytes
        try:
            names = [item["properties"]["event_name"] for item in infected_encounters(objects)]
        except TerritoryRefused:
            names = []
        return error, infected_caps(events, globals_xml, names)
