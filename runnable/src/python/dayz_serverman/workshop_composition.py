"""Compose SteamCMD updates, the mod inventory, mod publication and profile deletion."""

from __future__ import annotations

from dataclasses import dataclass

from .adapters.windows.mutex import WindowsInstallationMutex
from .adapters.windows.steamcmd import SteamCmdPreflight, WindowsSteamCmdAdapter
from .application.backups import BackupService
from .application.content_proof_records import ContentProofRecorder
from .application.content_proofs import ContentProofResolver
from .application.installation_guard import InstallationGuard
from .application.mod_inventory import CheckSource, ModInventoryService
from .application.mod_inventory_coordinator import ModInventoryCoordinator
from .application.mod_publication import ModPublicationService
from .application.mod_publication_coordinator import ModPublicationCoordinator
from .application.mod_publication_prestart import PrestartFingerprints
from .application.mod_publication_startup import recover_interrupted_publications
from .application.mod_restart_coordinator import ModRestartCoordinator
from .application.operations.manager import OperationManager
from .application.preferences import PreferenceCoordinator
from .application.profile_coordinator import ProfileCoordinator
from .application.profile_deletion import ProfileDeletionService
from .application.profiles import ProfileService
from .application.schedules import ScheduleCoordinator
from .application.server_readiness import ReadinessLifecycleService
from .application.settings import SettingsService
from .application.steamcmd_guard import SteamCmdRunGuard
from .application.target_proofs import TargetProofLookup
from .application.workshop_coordinator import WorkshopCoordinator
from .application.workshop_updates import WorkshopUpdateService
from .application.workshop_verification import WorkshopVerificationService
from .application.workshop_verification_coordinator import WorkshopVerificationCoordinator
from .observability.structured_log import StructuredLogger
from .repositories.applied_mod_state import AppliedModStateRepository
from .repositories.content_proofs import ContentProofStore
from .repositories.mod_publication_journal import PublicationJournalRepository
from .repositories.mod_publication_stage import ModPublicationStorage
from .repositories.paths import PortablePaths


@dataclass(frozen=True)
class WorkshopComposition:
    """Services and coordinators of the Workshop and mod publication area."""

    profile_coordinator: ProfileCoordinator
    workshop_updates: WorkshopUpdateService
    workshop_coordinator: WorkshopCoordinator
    mod_inventory: ModInventoryService
    mod_inventory_coordinator: ModInventoryCoordinator
    mod_publication: ModPublicationService
    mod_publication_coordinator: ModPublicationCoordinator
    mod_restart_coordinator: ModRestartCoordinator
    verification_coordinator: WorkshopVerificationCoordinator


def build_workshop(
    paths: PortablePaths,
    profiles: ProfileService,
    settings: SettingsService,
    operations: OperationManager,
    lifecycle: ReadinessLifecycleService,
    preferences: PreferenceCoordinator,
    schedules: ScheduleCoordinator,
    publication_journals: PublicationJournalRepository,
    check_source: CheckSource,
    logger: StructuredLogger | None = None,
    backups: BackupService | None = None,
    *, steamcmd_guard: SteamCmdRunGuard | None = None,
) -> WorkshopComposition:
    """Build SteamCMD update, inventory, and publication services.

    An interrupted mod publication is recovered first, before any service can queue a mutation.
    """
    # Every write into the DayZ root takes the installation mutex and needs a stopped server
    guard = InstallationGuard(lifecycle, WindowsInstallationMutex())
    recover_interrupted_publications(publication_journals, settings, operations, guard)
    steamcmd_preflight = SteamCmdPreflight()
    # The legacy applied-state file is only read; the proof store replaces its writes
    applied_mod_state = AppliedModStateRepository(paths.applied_mod_state)
    content_proofs = ContentProofStore(paths.content_proofs, logger)
    # Profile deletion also cleans the evidence that belongs to the profile
    profile_deletion = ProfileDeletionService(
        profiles, settings, lifecycle, preferences, schedules, applied_mod_state,
    )
    profile_coordinator = ProfileCoordinator(profiles, operations, profile_deletion)
    workshop_updates = WorkshopUpdateService(
        profiles, settings, steamcmd_preflight, WindowsSteamCmdAdapter(steamcmd_preflight),
        content_proofs=ContentProofResolver(content_proofs, applied_mod_state),
        # The update asks for a fresh check and sends only changed items to SteamCMD
        check_source=check_source,
        # One SteamCMD run at a time, also against the server build check
        steamcmd_guard=steamcmd_guard,
    )
    workshop_coordinator = WorkshopCoordinator(workshop_updates, settings, operations)
    # Row states merge the remote facts with target records; the proof store
    # is read first, then the legacy applied-state file
    mod_inventory = ModInventoryService(
        profiles, settings, check_source=check_source,
        target_proofs=TargetProofLookup([content_proofs, applied_mod_state]),
    )
    mod_publication = ModPublicationService(
        profiles, settings, operations,
        lambda checkpoint: ModPublicationStorage(checkpoint=checkpoint),
        publication_journals, lifecycle, ContentProofRecorder(content_proofs),
        # A requested start accepts a stored fingerprint for an unchanged mod folder
        prestart=PrestartFingerprints(content_proofs, logger),
        # A writing publication takes the installation mutex and needs a stopped server
        guard=guard,
    )
    return WorkshopComposition(
        profile_coordinator=profile_coordinator,
        workshop_updates=workshop_updates,
        workshop_coordinator=workshop_coordinator,
        mod_inventory=mod_inventory,
        mod_inventory_coordinator=ModInventoryCoordinator(mod_inventory),
        mod_publication=mod_publication,
        mod_publication_coordinator=ModPublicationCoordinator(mod_publication, operations),
        # "Update & restart" orders stop, backup and the guarded apply in one operation
        mod_restart_coordinator=ModRestartCoordinator(
            mod_publication, lifecycle, backups, operations,
        ),
        # "Verify files" hashes on the lane and replaces the stored proofs
        verification_coordinator=WorkshopVerificationCoordinator(
            WorkshopVerificationService(profiles, settings, content_proofs), operations,
        ),
    )
