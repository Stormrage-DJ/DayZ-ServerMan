"""Compose the legacy migration services together with the legacy backup index."""

from __future__ import annotations

from dataclasses import dataclass

from .application.legacy_backup_coordinator import LegacyBackupCoordinator
from .application.legacy_backups import LegacyBackupService
from .application.migration_coordinator import MigrationCoordinator
from .application.migrations import MigrationService
from .application.operations.manager import OperationManager
from .application.profiles import ProfileService
from .application.settings import SettingsService
from .repositories.json_store import StagingPolicy, VersionedJsonRepository
from .repositories.legacy_backup_index import LegacyBackupIndexRepository
from .repositories.migration_journal import MigrationJournalRepository
from .repositories.migration_publication import MigrationPublication
from .repositories.migrations import MigrationStorage
from .repositories.paths import PortablePaths
from .repositories.profiles import ProfileRepository


@dataclass(frozen=True)
class MigrationParts:
    """Migration and legacy backup services of one composition."""
    migrations: MigrationService
    migration_coordinator: MigrationCoordinator
    legacy_backups: LegacyBackupService
    legacy_backup_coordinator: LegacyBackupCoordinator


def build_migration(
    paths: PortablePaths, settings: SettingsService, settings_repository: VersionedJsonRepository,
    profiles: ProfileService, profile_repository: ProfileRepository, operations: OperationManager, *,
    staging: StagingPolicy, recover: bool,
) -> MigrationParts:
    """Build migration services with legacy backup indexing.

    An interrupted migration publication is inspected only when `recover` is true; observers pass false.
    """
    migration_storage = MigrationStorage(paths.migrations)
    legacy_backup_repository = LegacyBackupIndexRepository(
        paths.migrations / "legacy-backup-index.json", staging=staging,
    )
    migration_publication = MigrationPublication(
        paths.root, migration_storage,
        MigrationJournalRepository(paths.migrations / "publication-journals"),
        block_recovery=operations.block_for_recovery,
    )
    migrations = MigrationService(
        paths, settings, settings_repository, profiles, profile_repository,
        migration_storage, publisher=migration_publication,
        backup_index_repository=legacy_backup_repository,
    )
    if recover:
        migrations.inspect_recovery()
    legacy_backups = LegacyBackupService(legacy_backup_repository)
    return MigrationParts(
        migrations=migrations,
        migration_coordinator=MigrationCoordinator(migrations, operations),
        legacy_backups=legacy_backups,
        legacy_backup_coordinator=LegacyBackupCoordinator(legacy_backups, operations),
    )
