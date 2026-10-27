"""Compose the preview, Apply, restore and recovery owner methods of the map editor (leaf 8.9)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .application.installation_guard import InstallationGuard
from .application.lifecycle_ports import ServerFolderWriterPort
from .application.mission_map_apply import MissionMapApply
from .application.mission_map_apply_coordinator import MissionMapApplyCoordinator
from .application.mission_map_apply_records import owned_or_none
from .application.mission_map_commit import MissionMapCommit
from .application.mission_map_preview import MissionMapPreview
from .application.mission_map_publication_coordinator import MissionMapPublicationCoordinator
from .application.mission_map_restore import MissionMapRestore
from .application.mission_map_restore_coordinator import MissionMapRestoreCoordinator
from .application.mission_map_restore_review import RestoreReader
from .application.operations.manager import OperationManager
from .application.profiles import ProfileService
from .mission_map_composition import MissionMapParts
from .repositories.mission_map_journal_store import MissionMapJournalRepository, journal_folder
from .repositories.mission_map_owned import check_reserved
from .repositories.paths import PortablePaths


def build_mission_map_commit(paths: PortablePaths, parts: MissionMapParts, guard: InstallationGuard,
                             folder_writer: ServerFolderWriterPort | None, *, create_root: bool) -> MissionMapCommit:
    """Return the one commit step of the editor; Apply, the restores and adoption share it and its locks."""
    journals = MissionMapJournalRepository(journal_folder(paths), create_root=create_root)
    # Plan saves and Apply, restore or adoption commits of one association meet on the one lock set (T2.3-F9)
    return MissionMapCommit(paths, journals, guard, folder_writer, parts.locks, owned_digest=owned_or_none,
                            check_reserved=check_reserved)


def build_mission_map_publication(
    paths: PortablePaths, profiles: ProfileService, parts: MissionMapParts, guard: InstallationGuard,
    folder_writer: ServerFolderWriterPort | None, operations: OperationManager, *,
    medical_baseline: Callable[[str, Path], Path | None], create_root: bool, commit: MissionMapCommit | None = None,
) -> MissionMapPublicationCoordinator:
    """Return the owner methods over one commit step that takes the plan service's association locks.

    `parts` must come from `build_mission_map` with a guard and the lane, so its
    recovery coordinator exists; the 8.5 startup recovery has then already run.
    An observer passes `create_root` false: nothing is created, and its facade
    never holds these methods (A11).
    """
    if parts.recovery_coordinator is None:
        raise ValueError("the map editor publication needs the editor recovery coordinator")
    commit = commit or build_mission_map_commit(paths, parts, guard, folder_writer, create_root=create_root)
    preview = MissionMapPreview(parts.plans, profiles, medical_baseline=medical_baseline)
    reader = RestoreReader(parts.plans, profiles, medical_baseline=medical_baseline)
    restore = MissionMapRestore(reader, commit, paths, profiles)
    return MissionMapPublicationCoordinator(
        preview, restore, MissionMapApplyCoordinator(MissionMapApply(preview, commit, paths, profiles), operations),
        MissionMapRestoreCoordinator(restore, operations), parts.recovery_coordinator)
