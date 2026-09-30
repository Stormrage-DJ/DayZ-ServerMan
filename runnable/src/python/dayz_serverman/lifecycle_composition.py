"""Compose process lifecycle control with local Steam-query readiness."""

from __future__ import annotations

from pathlib import Path

from .adapters.steam_query import SteamQueryProbe
from .adapters.mission_readiness import RptMissionReadinessProbe
from .adapters.windows.graceful_stop import WindowsGracefulStop
from .adapters.windows.launcher import WindowsProcessLauncher
from .adapters.windows.mutex import WindowsInstallationMutex
from .adapters.windows.processes import WindowsProcessInventory
from .application.operations.manager import OperationManager
from .application.profiles import ProfileService
from .application.server_readiness import ReadinessLifecycleService
from .application.settings import SettingsService
from .application.lifecycle import ServerLifecycleService
from .repositories.json_store import VersionedJsonRepository
from .repositories.lifecycle_state import LifecycleStateRepository


def build_lifecycle(
    settings: SettingsService,
    profiles: ProfileService,
    state_repository: VersionedJsonRepository,
    operations: OperationManager,
    logs_root: Path,
) -> tuple[ReadinessLifecycleService, WindowsInstallationMutex]:
    """Build authoritative process control and its readiness decorator."""
    # Share one inventory between reconciliation and graceful shutdown
    launcher = WindowsProcessLauncher()
    inventory = WindowsProcessInventory()
    mutex = WindowsInstallationMutex()
    process_lifecycle = ServerLifecycleService(
        settings,
        profiles,
        inventory,
        launcher,
        WindowsGracefulStop(inventory),
        mutex,
        LifecycleStateRepository(state_repository, operations.session_id),
        logs_root,
    )
    # Keep network readiness separate from process ownership and control
    lifecycle = ReadinessLifecycleService(
        process_lifecycle,
        profiles,
        settings,
        SteamQueryProbe(),
        RptMissionReadinessProbe(),
    )
    return lifecycle, mutex
