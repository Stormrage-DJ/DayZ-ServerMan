"""Reconciliation choices of an Apply and the candidates that they give (8.7, R11, R17).

Each external change of the reviewed preview needs an explicit choice:

- `incorporate`: the edit becomes part of the working baseline, with
  provenance. The preview tells whether this is available (rule in
  `repositories/mission_map_incorporate.py`). The candidates are then built
  again from the new working baseline, so the editor values never compound.
- `replace`: the candidate replaces the file. The external version is the
  "files before" content of the new last-applied record. That content is
  stored before the first DayZ-side byte is written (D3, D4 step 2).

Each unavailable encounter that the plan does not exclude needs the choice
`exclude`: its spawning is not written, and its linked loot stays (R11, 7.4).
The applied record lists these encounters in `excluded_encounters`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.mission_map_terrain import TerrainDataError
from ..repositories.mission_map_mapgroup_read import PROTO
from ..repositories.mission_map_preview_candidates import PreviewCandidates, build_candidates
from .mission_map_preview import PreviewReview

# Choice values of an external change and of an unavailable encounter
INCORPORATE, REPLACE, EXCLUDE = "incorporate", "replace", "exclude"
# Fields of the choices object of an Apply request
CHOICE_FIELDS = frozenset(("external_changes", "unavailable_encounters"))
# Reason codes of a refused Apply before any write
CHOICES_INVALID = "choices_invalid"
CHOICE_REQUIRED = "choice_required"
PREVIEW_REFUSED = "preview_refused"
INCORPORATION_REFUSED = "incorporation_refused"
CANDIDATE_REFUSED = "candidate_refused"
# Product Owner texts of 2026-10-15 01:16:10 in evidence 8.7; they name no file and no encounter
CHOICE_FILES_TEXT = ("Apply needs a choice for each mission file that changed outside the map editor: keep the change "
                     "or replace the file. Make a choice for each of these files, then apply again.")
CHOICE_ENCOUNTERS_TEXT = ("Apply cannot write an encounter that is not available on this mission. Exclude each of "
                          "these encounters from Apply, then apply again.")


class ApplyRefused(ValueError):
    """Raised when Apply cannot start: invalid or missing choices, or a refused review; nothing was written."""

    def __init__(self, reason: str, message: str, *, path: str | None = None) -> None:
        """Keep the closed reason code and the file that the refusal names, if any."""
        self.reason, self.path = reason, path
        super().__init__(message)


@dataclass(frozen=True)
class Choices:
    """The choices of one Apply request: one for each external change and each unavailable encounter."""

    external: dict[str, str]
    excluded: tuple[str, ...]

    @property
    def incorporated(self) -> list[str]:
        """Return the paths whose external edit becomes part of the working baseline, sorted."""
        return sorted(path for path, choice in self.external.items() if choice == INCORPORATE)

    @property
    def replaced(self) -> list[str]:
        """Return the paths whose external edit the candidate replaces, sorted."""
        return sorted(path for path, choice in self.external.items() if choice == REPLACE)


def parse_choices(raw: object) -> Choices:
    """Return the choices of a request; any other shape or value is refused before the review."""
    if not isinstance(raw, Mapping) or set(raw) != CHOICE_FIELDS:
        raise ApplyRefused(CHOICES_INVALID, "The Apply choices are invalid.")
    external, encounters = raw["external_changes"], raw["unavailable_encounters"]
    if not _choice_map(external, (INCORPORATE, REPLACE)) or not _choice_map(encounters, (EXCLUDE,)):
        raise ApplyRefused(CHOICES_INVALID, "The Apply choices are invalid.")
    return Choices(dict(external), tuple(sorted(encounters)))


def check_choices(choices: Choices, answer: dict[str, Any]) -> None:
    """Require exactly one choice for each external change and each unavailable encounter of the review."""
    wanted = answer["choices"]
    if set(choices.external) != set(wanted["external_changes"]):
        raise ApplyRefused(CHOICE_REQUIRED, CHOICE_FILES_TEXT)
    if set(choices.excluded) != set(wanted["unavailable_encounters"]):
        raise ApplyRefused(CHOICE_REQUIRED, CHOICE_ENCOUNTERS_TEXT)


def check_review(review: PreviewReview) -> None:
    """Refuse a review that has a refusal; the family gate is checked before this (6.11)."""
    refusals = review.answer["refusals"]
    if refusals:
        first = refusals[0]
        raise ApplyRefused(PREVIEW_REFUSED, str(first["message"]), path=first.get("file"))


def chosen_base(review: PreviewReview, choices: Choices) -> dict[str, bytes | None]:
    """Return the working-baseline bytes after the incorporations, or refuse an unavailable one."""
    context = review.context
    assert context is not None
    files = dict(context.base.files)
    for path in choices.incorporated:
        result = context.incorporations[path]
        if not result.available:
            raise ApplyRefused(INCORPORATION_REFUSED, str(result.message), path=path)
        files[path] = result.data
    return files


def chosen_candidates(review: PreviewReview, choices: Choices, base: dict[str, bytes | None]) -> PreviewCandidates:
    """Return the reviewed candidates, or the candidates built again over a base with incorporations."""
    context = review.context
    assert context is not None
    if not choices.incorporated:
        return review.candidates
    inputs = review.inputs
    candidates = build_candidates(
        context.plan, base, current_proto=inputs.managed[PROTO], medical=context.medical, limits=inputs.limits,
        events=inputs.events, globals_xml=inputs.globals_xml, clusters=inputs.clusters,
        unavailable=context.unavailable)
    # A base with an incorporated edit can refuse a builder; nothing is written then
    refusals = [str(item.message) for item in candidates.mapgroups.refusals]
    if isinstance(candidates.areaflags, TerrainDataError):
        refusals.append(str(candidates.areaflags))
    refusals += [str(item["message"]) for item in candidates.territory_refusals()]
    if refusals:
        raise ApplyRefused(CANDIDATE_REFUSED, refusals[0])
    return candidates


def _choice_map(value: object, allowed: tuple[str, ...]) -> bool:
    """Return whether a value maps non-empty texts to allowed choice values."""
    return isinstance(value, Mapping) and all(
        isinstance(key, str) and key and choice in allowed for key, choice in value.items())
