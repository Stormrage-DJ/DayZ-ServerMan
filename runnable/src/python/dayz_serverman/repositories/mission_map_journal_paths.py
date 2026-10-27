"""Path derivation and path safety of editor publication journals (D4): every path is derived again before use."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..domain.profiles import ProfileValidationError
from .backup_verification import is_reparse, path_has_reparse
from .mission_map_journal import MapGroup, MapJournal, record_path_kind, record_staging
from .mission_map_journal_store import recovery_folder
from .mission_map_layout import (
    APPLIED_FILE, BASELINE_FILE, HISTORY_MARK, PLAN_FILE, RETIRED_FILE, STAMP_PATTERN, TargetClass,
    association_folder, is_target_key, ledger_folder, original_folder, profile_folder, target_key,
)
from .mission_map_records import ORIGINAL_FILE, restored_copy_file, superseded_file
from .paths import PortablePaths
from .restore_paths import RestorePathError, safe_root


# Suffix of a DayZ-side staging file beside its target: .<name>.<operation_id>.mission-map-stage
GROUP_STAGE_SUFFIX = ".mission-map-stage"
# Record files of the sibling folder that becomes the active folder at step 7 of an adoption commit (10.34)
SIBLING_KINDS = {PLAN_FILE: "plan", BASELINE_FILE: "baseline", APPLIED_FILE: "applied"}
# The closed name of an invalidated last-applied record in the active folder (10.35)
CLOSED_NAME = re.compile(r"applied\.closed-[0-9]{8}T[0-9]{6}Z(?:-[0-9]+)?\.json")


def sibling_kind(name: str, operation_id: str) -> str | None:
    """Return the kind of a file name in the sibling folder: the three records, or a restored copy (10.35)."""
    restored = {f"restored-{target_class.value}-{operation_id}.json" for target_class in TargetClass}
    return SIBLING_KINDS.get(name) or ("restored_copy" if name in restored else None)


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

    Originals, superseded records and ledger entries use a target key of the journal. Baseline, last applied and
    the restored copy of each target class use its association. A copied original has the path of the original.
    """
    area = paths.mission_map
    keys = [journal.mission_key] + ([journal.runtime_profile_key] if journal.runtime_profile_key else [])
    allowed: dict[str, str] = {}
    for key in keys:
        allowed[paths.relative(original_folder(area, key) / ORIGINAL_FILE)] = "original"
        allowed[paths.relative(superseded_file(area, key, journal.operation_id))] = "superseded"
        allowed[paths.relative(ledger_folder(area, key) / f"{journal.operation_id}.json")] = "ledger"
    association = association_folder(area, journal.profile_id, journal.mission_key)
    allowed[paths.relative(association / BASELINE_FILE)] = "baseline"
    allowed[paths.relative(association / APPLIED_FILE)] = "applied"
    for target_class in [TargetClass.MISSION] + ([TargetClass.RUNTIME] if journal.runtime_profile_key else []):
        copy = restored_copy_file(area, journal.profile_id, journal.mission_key, target_class, journal.operation_id)
        allowed[paths.relative(copy)] = "restored_copy"
    return allowed


def adoption_kind(journal: MapJournal, paths: PortablePaths, target: str) -> str | None:
    """Return the 10.34 kind that a manager-relative record path has in the journal, or None.

    Each path is in a folder of the journal's profile. It is the plan of the active association, the retired
    marker of another association folder, or the dated history name of the active folder. The Apply preflight
    uses only record_targets, so it never accepts these kinds.
    """
    profile = paths.relative(profile_folder(paths.mission_map, journal.profile_id))
    path = PurePosixPath(target)
    active = paths.relative(association_folder(paths.mission_map, journal.profile_id, journal.mission_key))
    if target in (active, f"{active}/{PLAN_FILE}"):
        return "rename" if target == active else "plan"
    # The sibling folder of the active folder stages its fresh records (Architect 00:21:00 in evidence 10.34)
    if str(path.parent) == record_staging(active, journal.operation_id):
        return sibling_kind(path.name, journal.operation_id)
    # Adoption of the restore case closes an invalidated record in the active folder (10.35)
    if str(path.parent) == active and CLOSED_NAME.fullmatch(path.name):
        return "history_file"
    # Adoption never retires the new active folder, and never a folder of another profile (OP-1)
    if (path.name == RETIRED_FILE and str(path.parent.parent) == profile and is_target_key(path.parent.name)
            and path.parent.name != journal.mission_key):
        return "retired"
    stamp = path.name.removeprefix(journal.mission_key + HISTORY_MARK)
    if str(path.parent) == profile and stamp != path.name and STAMP_PATTERN.fullmatch(stamp):
        return "rename"
    return None


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
            (allowed.get(record.target_path) or adoption_kind(journal, paths, record.target_path))
            == record_path_kind(record.kind)
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
