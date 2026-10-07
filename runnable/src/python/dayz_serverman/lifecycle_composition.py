"""Compose process lifecycle control with local Steam-query readiness."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .adapters.steam_player_query import SteamPlayerQuery
from .adapters.steam_query import SteamQueryProbe
from .adapters.mission_readiness import RptMissionReadinessProbe
from .adapters.windows.graceful_stop import WindowsGracefulStop
from .adapters.windows.launcher import WindowsProcessLauncher
from .adapters.windows.mutex import WindowsInstallationMutex
from .adapters.windows.processes import WindowsProcessInventory
from .application.external_players import ExternalServerMatch
from .application.online_players import OnlinePlayersCoordinator
from .application.preferences import PreferenceCoordinator
from .application.operations.manager import OperationManager
from .application.profiles import ProfileService
from .application.server_readiness import ReadinessLifecycleService
from .application.settings import SettingsService
from .application.lifecycle import ServerLifecycleService
from .application.lifecycle_ownership import LaunchOwnership
from .repositories.json_store import VersionedJsonRepository
from .repositories.lifecycle_state import LifecycleStateRepository


# Wait of each A2S_INFO exchange to a server outside the manager; a localhost answer takes about 40 to 60 ms
EXTERNAL_PROBE_TIMEOUT_SECONDS = 0.15


def build_lifecycle(
    settings: SettingsService,
    profiles: ProfileService,
    state_repository: VersionedJsonRepository,
    operations: OperationManager,
    logs_root: Path,
    preferences: PreferenceCoordinator | None = None,
    *,
    ownership: LaunchOwnership | None = None,
) -> tuple[ReadinessLifecycleService, WindowsInstallationMutex]:
    """Build authoritative process control and its readiness decorator.

    `ownership` holds the ownership record of A6; both services share it.
    """
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
        ownership,
    )
    # Keep network readiness separate from process ownership and control
    lifecycle = ReadinessLifecycleService(
        process_lifecycle,
        profiles,
        settings,
        SteamQueryProbe(),
        RptMissionReadinessProbe(),
        # D18 extension: a server outside the manager is matched against the selected profile with its own
        # short probe, so a status read waits at most 2 x 0.15 s more (with a challenge round)
        external=ExternalServerMatch(
            profiles, settings, SteamQueryProbe(EXTERNAL_PROBE_TIMEOUT_SECONDS), _selected_profile(preferences),
        ),
        ownership=ownership,
    )
    return lifecycle, mutex


def _selected_profile(preferences: PreferenceCoordinator | None) -> Callable[[], str | None]:
    """Return a reader of the profile selected in the sidebar; any read failure means no selection."""
    def read() -> str | None:
        """Return the stored selected profile, or None."""
        if preferences is None:
            return None
        try:
            value = preferences.get_ui_preferences({}).get("selected_profile_id")
        except Exception:
            # A preference that cannot be read only means that no count is shown for an outside server
            return None
        return value if isinstance(value, str) else None
    return read


def build_online_players(lifecycle: ReadinessLifecycleService) -> OnlinePlayersCoordinator:
    """Build the on-request names read on the query port of the managed server (D18)."""
    return OnlinePlayersCoordinator(lifecycle, SteamPlayerQuery())
