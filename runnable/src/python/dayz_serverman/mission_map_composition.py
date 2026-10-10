"""Compose the mission map plan services of task 2 from the manager layout (D2, D7)."""

from __future__ import annotations

from dataclasses import dataclass

from .application.mission_map_locks import AssociationLocks
from .application.mission_map_plans import MissionMapPlanService
from .application.mission_map_transfer import MissionMapTransferService
from .application.profiles import ProfileService
from .application.settings import SettingsService
from .repositories.json_store import StagingPolicy
from .repositories.paths import PortablePaths


@dataclass(frozen=True)
class MissionMapParts:
    """Mission map services of one composition.

    `locks` is the one association lock set of the process. The plan service holds it, and the commit
    step of task 8 must take this same instance (T2.3-F9).
    """
    locks: AssociationLocks
    plans: MissionMapPlanService
    transfer: MissionMapTransferService


def build_mission_map(
    paths: PortablePaths, profiles: ProfileService, settings: SettingsService, *,
    staging: StagingPolicy = StagingPolicy.OWNER,
) -> MissionMapParts:
    """Build the plan and transfer services over `paths.mission_map`.

    The build creates no folder and reads no file. The first plan save makes its association folder.
    Observers pass the observer staging policy, so their plan service refuses every write.
    """
    # Plan saves and record commits of one association must meet on the same lock
    locks = AssociationLocks()
    plans = MissionMapPlanService(profiles, settings, paths.mission_map, locks, staging=staging)
    return MissionMapParts(locks=locks, plans=plans, transfer=MissionMapTransferService(plans))
