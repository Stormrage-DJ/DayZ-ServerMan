"""Admit configuration publication and startup recovery under existing lifecycle ownership."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from ..domain.lifecycle import LifecycleFailure
from ..repositories.configuration_journal import ConfigurationPublicationError
from ..repositories.configuration_publication import ConfigurationPublication
from .folder_writer_scope import OWNER_WRITER_WAIT_SECONDS, STARTUP_RECOVERY_WAIT_SECONDS


OWNER = "APPLY_CONFIGURATION"
UNRESOLVED = "Mutations are blocked by unresolved configuration recovery."


class ConfigurationRecovery:
    """Coordinate the exact publication adapter, stopped guard and owner-specific lane blocks."""

    def __init__(self, publication, settings, operations, guard, folder_writer=None) -> None:
        """Keep dependencies without reading or creating recovery artifacts."""
        self.publication = publication
        self.settings, self.operations = settings, operations
        self.guard = guard.with_folder_writer(folder_writer)

    def pending(self) -> bool:
        """Treat unreadable storage as pending, with no writes or guessed safe state."""
        try:
            return bool(self.publication.store.records())
        except (OSError, ValueError, TypeError):
            return True

    def startup(self) -> None:
        """Recover before handlers are exposed; no journal means no lifecycle or settings read."""
        if not self.pending():
            return
        try:
            root = self.settings.load().dayz_root
            if root is None:
                self.operations.block_for_missing_dayz_root(UNRESOLVED, owner=OWNER)
                return
            with self.guard.stopped(Path(root), folder_wait=STARTUP_RECOVERY_WAIT_SECONDS):
                self._inspect(Path(root))
        except (OSError, ValueError, LifecycleFailure, ConfigurationPublicationError):
            self.operations.block_for_recovery(UNRESOLVED, owner=OWNER)

    @contextmanager
    def mutation(self):
        """Hold mutex then stopped proof then folder writer across validation and publication."""
        root = self.settings.load().dayz_root
        if root is None:
            raise ConfigurationPublicationError("PATH_INVALID", "DayZ root is not configured.")
        with self.guard.stopped(Path(root), folder_wait=OWNER_WRITER_WAIT_SECONDS):
            if self.pending():
                self._inspect(Path(root))
            yield

    def _inspect(self, root: Path) -> None:
        """Lift only this owner's blocks after every journal resolves safely."""
        result = self.publication.inspect(root)
        if result["blocked"]:
            self.operations.block_for_recovery(UNRESOLVED, owner=OWNER)
            raise ConfigurationPublicationError("RECOVERY_REQUIRED", UNRESOLVED, recovery_required=True)
        self.operations.clear_recovery_block(owner=OWNER)

    def require_root_match(self, candidate: Path) -> bool:
        """Allow the existing settings repair only for roots named by readable journals."""
        try:
            return all(journal is not None and Path(journal.dayz_root) == candidate.resolve()
                       for _path, journal in self.publication.store.records())
        except (OSError, ValueError, TypeError):
            return False


def configure_recovery(paths, configuration, coordinator, settings, operations, guard,
                       folder_writer=None, *, observer=False):
    """Bind one owner; observers neither recover nor gain a mutation entry point."""
    publication = ConfigurationPublication(paths.profiles, paths.operations)
    recovery = ConfigurationRecovery(publication, settings, operations, guard, folder_writer)
    if not observer:
        configuration.bind_publication(publication)
        coordinator.bind_admission(recovery.mutation)
        recovery.startup()
    return recovery
