"""Compose the transfer coordinator of the map editor (leaf 10.6) and the adoption coordinator (10.13, 10.35)."""

from __future__ import annotations

from collections.abc import Mapping

from .application.mission_map_adoption import MissionMapAdoption
from .application.mission_map_adoption_commit import AdoptionCommit
from .application.mission_map_adoption_coordinator import MissionMapAdoptionCoordinator
from .application.mission_map_adoption_review import AdoptionReader
from .application.mission_map_commit import MissionMapCommit
from .application.mission_map_transfer_coordinator import MissionMapTransferCoordinator
from .application.operations.manager import OperationManager
from .bridge.facade import BridgeHandler
from .mission_map_composition import MissionMapParts


class MissionMapTransferHandlers:
    """The transfer handlers, then the adoption handlers, in handler-table order."""

    def __init__(self, transfer: MissionMapTransferCoordinator, adoption: MissionMapAdoptionCoordinator) -> None:
        """Keep both coordinators."""
        self.transfer, self.adoption = transfer, adoption

    def handlers(self) -> Mapping[str, BridgeHandler]:
        """Return export, import and copy, then the adoption preview."""
        return {**self.transfer.handlers(), **self.adoption.handlers()}


def build_mission_map_transfer(parts: MissionMapParts, commit: MissionMapCommit,
                               operations: OperationManager) -> MissionMapTransferHandlers:
    """Return the transfer and adoption handlers over the one plan service of the composition.

    Export is an observer read, so an observer composition holds it too. The import and copy handlers and both
    adoption methods (the preview and the adopt step) are owner methods. The observer facade never holds them
    (A11).

    The adoption commits through the one commit step of Apply and the restores. That step holds the
    association locks of the plan service (Architect 2026-10-17 11:42:47 in evidence 10.35).
    """
    plans = parts.plans
    reader = AdoptionReader(plans, plans._profiles, clock=plans._clock)
    adoption = MissionMapAdoption(reader, AdoptionCommit(commit), commit.paths, clock=plans._clock)
    return MissionMapTransferHandlers(MissionMapTransferCoordinator(parts.transfer, plans),
                                      MissionMapAdoptionCoordinator(adoption, operations))
