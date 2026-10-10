"""Path derivation and path safety of editor publication journals (D4): every path is derived again before use."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..domain.profiles import ProfileValidationError
from .backup_verification import is_reparse, path_has_reparse
from .mission_map_journal import MapGroup, MapJournal, record_staging
from .mission_map_journal_store import recovery_folder
from .mission_map_layout import (
    APPLIED_FILE, BASELINE_FILE, TargetClass, association_folder, ledger_folder, original_folder, target_key,
)
from .mission_map_records import ORIGINAL_FILE
from .paths import PortablePaths
from .restore_paths import RestorePathError, safe_root


# Suffix of a DayZ-side staging file beside its target: .<name>.<operation_id>.mission-map-stage
GROUP_STAGE_SUFFIX = ".mission-map-stage"


def group_staging_path(target: Path, operation_id: str, ancestors: Sequence[Path]) -> Path:
    """Return the DayZ-side staging path: beside the target, or in the operation stage folder for new folders.

    The stage folder .serverman-<operation_id>-stage is the backup-restore form, so the shared cleanup prunes it.
    """
    if ancestors:
        nearest = ancestors[0].parent
        return nearest / f".serverman-{operation_id}-stage" / target.relative_to(nearest)
    return target.with_name(f".{target.name}.{operation_id}{GROUP_STAGE_SUFFIX}")


def recovery_copy_path(recovery_root: Path, operation_id: str, target_class: str, relative_path: str) -> Path:
    """Return the recovery copy of one group: <recovery>/<operation_id>/<target_class>/<relative_path>."""
    return recovery_root / operation_id / target_class / Path(*PurePosixPath(relative_path).parts)


def target_folder(root: Path, relative: str) -> Path:
    """Return the strictly resolved mission or runtime profile folder, inside the root and without reparse points.

    The key of this path is the target key of D2, as the plan service derives it.
    """
    folder = root.joinpath(*PureWindowsPath(relative).parts)
    if path_has_reparse(folder) or not folder.is_dir():
        raise RestorePathError("PATH_INVALID", "A mission map target folder is missing or crosses a reparse point.")
    resolved = folder.resolve(strict=True)
    # A folder that resolves outside the DayZ root is never a target
    resolved.relative_to(root)
    return resolved


def record_targets(journal: MapJournal, paths: PortablePaths) -> dict[str, str]:
    """Return each record path that the journal may stage, manager-relative, with its record kind.

    Originals and ledger entries use a target key of the journal; baseline and last applied use its association.
    """
    area = paths.mission_map
    keys = [journal.mission_key] + ([journal.runtime_profile_key] if journal.runtime_profile_key else [])
    allowed: dict[str, str] = {}
    for key in keys:
        allowed[paths.relative(original_folder(area, key) / ORIGINAL_FILE)] = "original"
        allowed[paths.relative(ledger_folder(area, key) / f"{journal.operation_id}.json")] = "ledger"
    association = association_folder(area, journal.profile_id, journal.mission_key)
    allowed[paths.relative(association / BASELINE_FILE)] = "baseline"
    allowed[paths.relative(association / APPLIED_FILE)] = "applied"
    return allowed


def journal_paths_safe(journal: MapJournal, dayz_root: Path, paths: PortablePaths) -> bool:
    """Derive every group and record path of the journal again; any difference, escape or link returns False.

    The DayZ root of the journal must be the configured one, and each target key must equal the key derived
    from its folder with the 2.8 key helper. A False result blocks recovery without a write.
    """
    try:
        root = safe_root(dayz_root)
        if safe_root(Path(journal.dayz_root)) != root:
            return False
        folders = {TargetClass.MISSION.value: target_folder(root, journal.mission_root)}
        if target_key(TargetClass.MISSION, folders["mission"]) != journal.mission_key:
            return False
        if journal.runtime_profile is not None:
            folders[TargetClass.RUNTIME.value] = target_folder(root, journal.runtime_profile)
            if target_key(TargetClass.RUNTIME, folders["runtime"]) != journal.runtime_profile_key:
                return False
        # The lexical recovery folder, so that a link anywhere on its chain is found
        recovery = recovery_folder(paths)
        if not all(_group_safe(journal, group, root, folders, recovery) for group in journal.groups):
            return False
        allowed = record_targets(journal, paths)
        return all(
            allowed.get(record.target_path) == record.kind
            and record.staging_path == record_staging(record.target_path, journal.operation_id)
            and not path_has_reparse(paths.root / record.target_path)
            and not path_has_reparse(paths.root / record.staging_path)
            for record in journal.records
        )
    except (OSError, ValueError, RestorePathError, ProfileValidationError):
        return False


def _group_safe(journal: MapJournal, group: MapGroup, root: Path, folders: dict[str, Path], recovery: Path) -> bool:
    """Return whether the target, staging, recovery and created folders of one group are the derived ones."""
    folder = folders.get(group.target_class)
    if folder is None:
        return False
    target = folder.joinpath(*PurePosixPath(group.relative_path).parts)
    # The target stays inside the DayZ root, also after resolution
    target.resolve(strict=False).relative_to(root)
    ancestors = tuple(Path(value) for value in group.created_ancestors)
    stage = group_staging_path(target, journal.operation_id, ancestors)
    expected_recovery = (
        recovery_copy_path(recovery, journal.operation_id, group.target_class, group.relative_path)
        if group.old_existed else None
    )
    if _resolved(group.target_path) != target.resolve(strict=False):
        return False
    if _resolved(group.staging_path) != stage.resolve(strict=False):
        return False
    if (group.recovery_path is None) != (expected_recovery is None):
        return False
    if expected_recovery is not None:
        if _resolved(group.recovery_path or "") != expected_recovery.resolve(strict=False):
            return False
    # No existing part of a derived or a stored target, staging or recovery chain may be a link or reparse point
    stored = [Path(value) for value in (group.target_path, group.staging_path, group.recovery_path) if value]
    if any(path_has_reparse(path) for path in (target, stage, expected_recovery or recovery, *stored)):
        return False
    return _ancestors_safe(ancestors, target, folder)


def _ancestors_safe(ancestors: tuple[Path, ...], target: Path, folder: Path) -> bool:
    """Return whether created folders form one chain inside the target folder down to the target's parent."""
    if not ancestors:
        return True
    if ancestors[-1].resolve(strict=False) != target.parent.resolve(strict=False):
        return False
    # Each created folder is a direct child of the one before it, starting below an existing folder
    for prior, path in zip((ancestors[0].parent, *ancestors[:-1]), ancestors):
        if path.parent.resolve(strict=False) != prior.resolve(strict=False):
            return False
        path.resolve(strict=False).relative_to(folder)
        if path.exists() and (not path.is_dir() or is_reparse(path)):
            return False
    return True


def _resolved(value: str) -> Path:
    """Return a stored absolute path in resolved form for comparison."""
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("a journal path must be absolute")
    return path.resolve(strict=False)
