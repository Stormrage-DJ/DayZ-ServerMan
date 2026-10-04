"""Compose the remote update check: catalog, cache, service, timer and bridge calls."""

from __future__ import annotations

from collections.abc import Callable

from .adapters.steam_web_api import SteamWebApiCatalog
from .application.mod_inventory import ModInventoryService
from .application.preferences import PreferenceCoordinator
from .application.profiles import ProfileService
from .application.update_check import UpdateCheckService
from .application.update_check_coordinator import UpdateCheckCoordinator
from .application.update_check_ports import WorkshopRemoteCatalogPort
from .application.update_check_scheduler import UpdateCheckScheduler
from .domain.profiles import ProfileRecord, ProfileValidationError
from .observability.structured_log import StructuredLogger
from .repositories.paths import PortablePaths
from .repositories.profiles import ProfileNotFound, ProfileRepository, ProfileStorageError
from .repositories.update_check_cache import UpdateCheckCacheRepository

# Errors that make one profile, or the whole listing, unreadable
_PROFILE_ERRORS = (ProfileStorageError, ProfileValidationError, ProfileNotFound, OSError)


def configured_workshop_ids(
    profiles: ProfileService, repository: ProfileRepository,
) -> Callable[[], frozenset[str]]:
    """Return a reader of the Workshop ids of all readable profiles."""

    def read() -> frozenset[str]:
        """Collect the ids; one unreadable profile does not hide the others."""
        try:
            records: tuple[ProfileRecord, ...] = profiles.list()
        except _PROFILE_ERRORS:
            # The listing refuses as a whole, so read the profiles one by one
            records = _readable_profiles(profiles, repository)
        return frozenset(
            mod.source.workshop_id for record in records for mod in record.values.mods
            if mod.source.kind == "workshop" and mod.source.workshop_id is not None
        )

    return read


def _readable_profiles(
    profiles: ProfileService, repository: ProfileRepository,
) -> tuple[ProfileRecord, ...]:
    """Return every profile that can be read on its own."""
    records: list[ProfileRecord] = []
    try:
        names = sorted(path.stem for path in repository.root.glob("*.json"))
    except OSError:
        return ()
    for name in names:
        try:
            records.append(profiles.read(name))
        except _PROFILE_ERRORS:
            continue
    return tuple(records)


def build_update_check(
    paths: PortablePaths,
    profiles: ProfileService,
    profile_repository: ProfileRepository,
    preferences: PreferenceCoordinator,
    logger: StructuredLogger | None,
    *,
    catalog: WorkshopRemoteCatalogPort | None = None,
) -> UpdateCheckService:
    """Build the check service over the Steam catalog and the disposable cache."""
    return UpdateCheckService(
        # Only this adapter opens a network connection
        catalog if catalog is not None else SteamWebApiCatalog(logger=logger),
        UpdateCheckCacheRepository(paths.update_check_cache, logger),
        configured_workshop_ids(profiles, profile_repository),
        # The stored switch is read at every trigger evaluation
        automatic_enabled=preferences.automatic_update_checks,
        logger=logger,
    )


def build_update_check_scheduler(
    service: UpdateCheckService, preferences: PreferenceCoordinator,
    logger: StructuredLogger | None,
) -> UpdateCheckScheduler:
    """Build the timer; the host starts and stops it with the window."""
    scheduler = UpdateCheckScheduler(service, logger)
    # A saved switch is evaluated at once instead of at the next timer tick
    preferences.on_automatic_update_checks_saved(scheduler.wake)
    return scheduler


def build_update_status(
    service: UpdateCheckService, mod_inventory: ModInventoryService,
) -> UpdateCheckCoordinator:
    """Build the bridge calls that report the status and accept check requests."""
    return UpdateCheckCoordinator(service, mod_inventory)
