"""Bridge handlers of the map plan transfer: export (leaf 10.6), import (10.33) and copy (10.7).

The handlers give the refusals of `MissionMapTransferService` the closed reasons of D16. The messages are the
Product Owner texts of 2026-10-16 14:26:38 in evidence 10.1; the developer messages of `TransferRefused` stay
inside the service. No method takes a file path: the CLI and the window host read and write the user file.
Export is an observer read; the import and copy previews, the import and the copy are owner methods (A11, AR-1).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.mission_map_merge import Availability, destination_availability
from ..domain.mission_map_terrain import TerrainCapabilities
from ..domain.mission_map_values import PlanValidationError
from ..repositories.mission_map_providers import read_encounter_providers
from ..repositories.mission_map_terrain import read_capabilities
from .mission_configuration import _contained
from .mission_map_coordinator_errors import exact_parameters, refusal, translate
from .mission_map_plans import MissionMapPlanService
from .mission_map_transfer import ConfirmationRequired, MissionMapTransferService, TransferRefused

# The error code of each closed reason of a transfer refusal (D16)
REASON_CODES: dict[str, ErrorCode] = {
    "no_valid_plan": ErrorCode.INVALID_REQUEST, "source_is_destination": ErrorCode.INVALID_REQUEST,
    "plan_limit": ErrorCode.INVALID_REQUEST, "mission_path_changed": ErrorCode.PROFILE_CONTEXT_MISMATCH,
}
# The Product Owner message of each reason and side. "map show", "--from-profile", "--mode replace",
# "--mode merge" and "--confirm-listed" are text to type in the CLI
REFUSAL_TEXTS: dict[tuple[str, str], str] = {
    ("no_valid_plan", "export"): "There is no valid map plan to export. Run map show for the state of the plan.",
    ("no_valid_plan", "destination"): "This profile has no valid saved map plan. Nothing was changed. Run map show "
                                      "for the state of the plan.",
    ("no_valid_plan", "source"): "The source profile has no valid saved map plan to copy. Nothing was changed.",
    ("source_is_destination", "source"): "Copy needs another profile as its source. Name it with --from-profile.",
    ("mission_path_changed", "destination"): "The mission path of this profile changed, so its map plan is read "
                                             "only. Adopt the mission before you import or copy.",
    ("mission_path_changed", "source"): "The mission path of the source profile changed, so it has no map plan to "
                                        "copy.",
    ("plan_limit", "destination"): "The result would hold {objects} objects and {vertices} polygon vertices. The "
                                   "limits are 2,000 objects and 50,000 vertices. Nothing was changed.",
}
# The file is not a plan document (exit 2); each problem follows as the answer states it
INVALID_DOCUMENT_TEXT = "The file is not a valid map plan."
INVALID_MODE_TEXT = "Choose --mode replace or --mode merge."
# The --confirm-listed refusals of import and copy (exit 4); the copy text is final (Product Owner 2026-10-17
# 03:37:11 in evidence 10.33), and leaf 10.7 uses it
CONFIRM_LISTED_TEXT = ("This import has items that DayZ-ServerMan does not import, or it needs a placement review. "
                       "Nothing was changed. Check the list above, then add --confirm-listed.")
CONFIRM_LISTED_COPY_TEXT = ("This copy has items that DayZ-ServerMan does not copy, or it needs a placement review. "
                            "Nothing was changed. Check the list above, then add --confirm-listed.")
MODES = ("replace", "merge")


class MissionMapTransferCoordinator:
    """Adapt the transfer service to the bridge contract of D16; construction registers nothing."""

    def __init__(self, transfer: MissionMapTransferService, plans: MissionMapPlanService, *,
                 capabilities: Callable[[Path], TerrainCapabilities] = read_capabilities) -> None:
        """Keep the transfer service, the plan service that resolves the destination and the capability reader."""
        self._transfer = transfer
        self._plans = plans
        self._capabilities = capabilities

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the transfer handlers for registration; the observer facade keeps only the export."""
        return {"export_mission_map_plan": self.export_mission_map_plan,
                "preview_mission_map_import": self.preview_mission_map_import,
                "import_mission_map_plan": self.import_mission_map_plan,
                "preview_mission_map_copy": self.preview_mission_map_copy,
                "copy_mission_map_plan": self.copy_mission_map_plan}

    def export_mission_map_plan(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the portable text of the saved plan and its revision; nothing is written (observer read)."""
        exact_parameters(parameters, {"profile_id"})
        return _answer(lambda: _export(self._transfer.export_plan(parameters["profile_id"])))

    def preview_mission_map_import(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the D7 report of importing a file text, with the destination revision; nothing is saved."""
        exact_parameters(parameters, {"profile_id", "text", "mode"})
        text, mode = _text_and_mode(parameters)
        return _answer(lambda: self._transfer.preview_import(parameters["profile_id"], text, mode,
                                                             self.availability(parameters["profile_id"])))

    def import_mission_map_plan(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Import a file text as one revision-guarded save; the answer has the revision and the report, not the plan."""
        exact_parameters(parameters, {"profile_id", "text", "mode", "expected_revision", "confirmed"})
        text, mode = _text_and_mode(parameters)
        # Only an explicit true confirms the listed objects, the unmapped items and the placement review
        confirmed = parameters["confirmed"] is True

        def run() -> dict[str, Any]:
            """Save the import and keep the plan out of the answer."""
            saved = self._transfer.import_plan(parameters["profile_id"], text, mode, parameters["expected_revision"],
                                               confirmed, self.availability(parameters["profile_id"]))
            return {"revision": saved["revision"], "report": saved["report"]}

        return _answer(run)

    def preview_mission_map_copy(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the D7 report of copying the active plan of another profile, with the destination revision."""
        exact_parameters(parameters, {"profile_id", "source_profile_id", "mode"})
        source, mode = _source_and_mode(parameters)
        return _answer(lambda: self._transfer.preview_copy(source, parameters["profile_id"], mode,
                                                           self.availability(parameters["profile_id"])),
                       CONFIRM_LISTED_COPY_TEXT)

    def copy_mission_map_plan(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Copy as one revision-guarded save; the source plan stays unchanged and the plan is not echoed."""
        exact_parameters(parameters, {"profile_id", "source_profile_id", "mode", "expected_revision", "confirmed"})
        source, mode = _source_and_mode(parameters)
        confirmed = parameters["confirmed"] is True

        def run() -> dict[str, Any]:
            """Save the copy and keep the plan out of the answer."""
            saved = self._transfer.copy_plan(source, parameters["profile_id"], mode, parameters["expected_revision"],
                                             confirmed, self.availability(parameters["profile_id"]))
            return {"revision": saved["revision"], "report": saved["report"]}

        return _answer(run, CONFIRM_LISTED_COPY_TEXT)

    def availability(self, profile_id: object) -> Availability:
        """Return the destination check of one call: the capability read and the provider read, as load reads them.

        The check depends on the destination mission, so each preview and each commit reads it again (Architect
        2026-10-17 03:38:11 in evidence 10.33). It names unavailable providers, native infected events that are
        not offered, wolves and bears, and tiers that the value layer cannot hold.
        """
        profile, root, _relative, mission = self._plans._resolve(profile_id)
        providers = read_encounter_providers(root, mission, profile, containment=_contained)
        return destination_availability(self._capabilities(mission).unsupported(), providers)


def _export(exported: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the text and the revision of an export."""
    return {"text": exported["text"], "revision": exported["revision"]}


def _text_and_mode(parameters: Mapping[str, Any]) -> tuple[str, str]:
    """Return the file text and the mode; another mode is `invalid_mode` and a text that is no string a bad document."""
    if parameters["mode"] not in MODES:
        raise refusal(ErrorCode.INVALID_REQUEST, "invalid_mode", INVALID_MODE_TEXT)
    if not isinstance(parameters["text"], str):
        raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", INVALID_DOCUMENT_TEXT)
    return parameters["text"], str(parameters["mode"])


def _source_and_mode(parameters: Mapping[str, Any]) -> tuple[str, str]:
    """Return the source profile ID and the mode; another mode is `invalid_mode`, a source that is no ID is refused."""
    if parameters["mode"] not in MODES:
        raise refusal(ErrorCode.INVALID_REQUEST, "invalid_mode", INVALID_MODE_TEXT)
    if not isinstance(parameters["source_profile_id"], str):
        raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", "The map editor parameters are invalid.")
    return parameters["source_profile_id"], str(parameters["mode"])


def _answer(call: Callable[[], dict[str, Any]], confirm_text: str = CONFIRM_LISTED_TEXT) -> dict[str, Any]:
    """Run one transfer call and translate its refusals into the closed reasons of D16."""
    try:
        return call()
    except ApplicationCallError:
        raise
    except TransferRefused as error:
        raise transfer_refusal(error) from error
    except ConfirmationRequired as error:
        raise refusal(ErrorCode.INVALID_REQUEST, "confirmation_required", confirm_text,
                      report=error.report) from error
    except PlanValidationError as error:
        problems = [{"object_id": problem.object_id, "rule": "invalid_document", "message": problem.message}
                    for problem in error.problems]
        raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", INVALID_DOCUMENT_TEXT,
                      problems=problems) from error
    except Exception as error:
        raise translate(error) from error


def transfer_refusal(error: TransferRefused) -> ApplicationCallError:
    """Return the bridge refusal of a transfer refusal: the code and the text of its closed reason and side."""
    text = REFUSAL_TEXTS.get((error.reason, error.side))
    message = text.format(**error.counts) if text is not None and error.counts else text or str(error)
    details = dict(error.counts)
    return refusal(REASON_CODES.get(error.reason, ErrorCode.INVALID_REQUEST), error.reason, message, **details)
