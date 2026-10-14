"""Compose mission map plans, shared locks and owner recovery from the manager layout."""

from __future__ import annotations

from dataclasses import dataclass

from .application.mission_map_coordinator import MissionMapCoordinator
from .application.mission_map_display import IMAGE_BYTE_LIMIT, IMAGE_SIDE_LIMIT, MissionMapDisplay
from .application.mission_map_images import ImageSelectionService
from .application.mission_map_inspection import MissionMapInspection
from .application.mission_map_locks import AssociationLocks
from .application.mission_map_plans import MissionMapPlanService
from .application.mission_map_reader_port import DelegatingReaderPort
from .application.mission_map_transfer import MissionMapTransferService
from .application.mission_map_recovery import MissionMapRecovery
from .application.mission_map_recovery_coordinator import MissionMapRecoveryCoordinator
from .application.installation_guard import InstallationGuard
from .application.lifecycle_ports import ServerFolderWriterPort
from .application.operations.manager import OperationManager
from .application.profiles import ProfileService
from .application.settings import SettingsService
from .repositories.json_store import StagingPolicy
from .repositories.paths import PortablePaths


@dataclass(frozen=True)
class MissionMapParts:
    """Mission map services of one composition.

    `locks` is the one association lock set of the process. The plan service holds it, and the commit
    step of task 8 must take this same instance (T2.3-F9). `reader` is the Inspect reader port; an
    observer session binds its folder reader to it, and an owner session leaves it unbound. `images` is
    the in-process image selection service of the host image dialog, and `display` builds the display-read
    handlers that the handler table registers (3.17).
    """
    locks: AssociationLocks
    plans: MissionMapPlanService
    transfer: MissionMapTransferService
    coordinator: MissionMapCoordinator
    reader: DelegatingReaderPort
    images: ImageSelectionService
    display: MissionMapDisplay
    recovery: MissionMapRecovery | None = None
    recovery_coordinator: MissionMapRecoveryCoordinator | None = None


def build_mission_map(
    paths: PortablePaths, profiles: ProfileService, settings: SettingsService, *,
    staging: StagingPolicy = StagingPolicy.OWNER,
    guard: InstallationGuard | None = None, folder_writer: ServerFolderWriterPort | None = None,
    operations: OperationManager | None = None,
) -> MissionMapParts:
    """Build the plan and transfer services over `paths.mission_map`.

    A wired owner recovers before returning the services. Observers create no folder and run no recovery.
    The first plan save makes its association folder.
    """
    # Plan saves and record commits of one association must meet on the same lock
    locks = AssociationLocks()
    plans = MissionMapPlanService(profiles, settings, paths.mission_map, locks, staging=staging)
    recovery = MissionMapRecovery(paths, settings, guard, folder_writer, locks) if guard is not None else None
    recovery_coordinator = MissionMapRecoveryCoordinator(recovery, operations) if recovery is not None and operations is not None else None
    if staging is StagingPolicy.OWNER and recovery_coordinator is not None:
        recovery_coordinator.startup()
    # Inspect takes the reader side per mission-read slice through this port (A181)
    reader = DelegatingReaderPort()
    # One selection service serves the host image dialog, the background read and the save token
    images = ImageSelectionService(byte_limit=IMAGE_BYTE_LIMIT, side_limit=IMAGE_SIDE_LIMIT)
    coordinator = MissionMapCoordinator(plans, inspection=MissionMapInspection(plans, reader), images=images)
    return MissionMapParts(locks=locks, plans=plans, transfer=MissionMapTransferService(plans),
                           coordinator=coordinator, reader=reader, images=images,
                           display=MissionMapDisplay(plans, images), recovery=recovery,
                           recovery_coordinator=recovery_coordinator)
