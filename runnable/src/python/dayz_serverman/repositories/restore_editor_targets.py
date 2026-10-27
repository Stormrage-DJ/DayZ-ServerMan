"""The manager-root target class of the backup restore journal: editor record files (D19, A10, leaf 10.10).

A same-profile restore writes the editor records in its own journal, beside the files of the DayZ root. Each
editor file is one group of class `manager`. Its `entry_path` is the target path relative to the manager root,
and the target must be below the editor area. A group stages the bytes beside its target, as a DayZ group does,
and keeps a recovery copy of a file that exists. A group with `new_exists` false makes its target absent; one
create group and one removal group together rename a record. Recovery derives every path again.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..domain.models import RevisionConflict
from ..domain.restores import RestoreGroup, RestoreJournal
from .backup_verification import is_reparse, path_has_reparse, sha256_file
from .paths import PortablePaths
from .restore_paths import RestorePathError, copy_verified, missing_ancestors, remove_stage_tree

# Target class of an editor record file, and the folder of its recovery copies below the operation folder
MANAGER_CLASS = "manager"
RECOVERY_PART = "manager"


@dataclass(frozen=True)
class EditorWrite:
    """One editor file of a restore: new bytes, or None when the file must end absent; the path is manager-relative.

    `expected` is the digest of the file when the restore read it, None when it was absent.
    """

    relative: str
    data: bytes | None
    expected: str | None = None


def editor_target(paths: PortablePaths, relative: str) -> Path:
    """Return the target of a manager-relative path below the editor area, after the containment checks."""
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(part in ("", ".", "..") or ":" in part or "\\" in part for part in parts.parts):
        raise RestorePathError("PATH_INVALID", "Restore target path is unsafe.")
    target = paths.root.joinpath(*parts.parts)
    area = paths.mission_map.resolve(strict=False)
    resolved = target.resolve(strict=False)
    if resolved == area or not resolved.is_relative_to(area):
        raise RestorePathError("PATH_INVALID", "Restore target path is unsafe.")
    # The nearest existing folder and the target itself must not be links or reparse points
    parent = target.parent
    while not parent.exists() and parent != paths.root:
        parent = parent.parent
    if not parent.is_dir() or path_has_reparse(parent):
        raise RestorePathError("PATH_INVALID", "Restore target parent is unsafe or missing.")
    if target.exists() and (not target.is_file() or is_reparse(target)):
        raise RestorePathError("PATH_INVALID", "Restore target is not a regular file.")
    return target


def editor_stage(target: Path, operation_id: str, ancestors: tuple[Path, ...]) -> Path:
    """Return the staging path: beside the target, or below the nearest existing folder when folders are missing."""
    if ancestors:
        nearest = ancestors[0].parent
        return nearest / f".serverman-{operation_id}-stage" / target.relative_to(nearest)
    return target.with_name(f".{target.name}.{operation_id}.restore-stage")


def editor_recovery(recovery_root: Path, operation_id: str, relative: str) -> Path:
    """Return the recovery copy of one editor file below the operation recovery folder."""
    return recovery_root.resolve(strict=False) / operation_id / RECOVERY_PART / PurePosixPath(relative)


def prepare_editor_groups(paths: PortablePaths, writes: tuple[EditorWrite, ...], recovery_root: Path,
                          operation_id: str) -> list[RestoreGroup]:
    """Stage each changed editor file and its recovery copy; a file that already holds its new state is skipped.

    The caller removes the staging of the returned groups when a later step fails.
    """
    groups: list[RestoreGroup] = []
    for write in writes:
        target = editor_target(paths, write.relative)
        existed = target.exists()
        old = sha256_file(target) if existed else None
        new = None if write.data is None else hashlib.sha256(write.data).hexdigest()
        if old == new:
            continue
        # A plan save after the read changed the file, so the decision of the restore is stale
        if old != write.expected:
            raise RevisionConflict("Restore preview is stale or changed.")
        ancestors = missing_ancestors(paths.root, target) if write.data is not None else ()
        stage = editor_stage(target, operation_id, ancestors)
        recovery = editor_recovery(recovery_root, operation_id, write.relative) if existed else None
        group = RestoreGroup(write.relative, str(target), str(stage), None if recovery is None else str(recovery),
                             existed, old, new, created_ancestors=tuple(str(path) for path in ancestors),
                             target_class=MANAGER_CLASS, new_exists=write.data is not None)
        # Track the group before its first write, so a failure removes partial staging
        groups.append(group)
        if recovery is not None:
            copy_verified(target, recovery, old)
        if write.data is not None:
            _stage_bytes(stage, write.data, new)
    return groups


def finish_editor_groups(groups: list[RestoreGroup]) -> None:
    """After the committed proof, remove what a moved-aside file and created folders left in staging.

    The recovery copy keeps the bytes of a removed file, as it keeps every replaced file of a restore.
    """
    for group in groups:
        if group.target_class != MANAGER_CLASS:
            continue
        stage = Path(group.staging_path)
        if not group.new_exists and stage.is_file() and not is_reparse(stage):
            stage.unlink()
        remove_stage_tree(group)


def _stage_bytes(stage: Path, data: bytes, digest: str | None) -> None:
    """Write the new bytes to a fresh staging file and verify them."""
    stage.parent.mkdir(parents=True, exist_ok=True)
    with stage.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if sha256_file(stage) != digest:
        raise RestorePathError("RESTORE_VERIFY_FAILED", "Restore copy failed checksum verification.")


def editor_paths_safe(journal: RestoreJournal, paths: PortablePaths | None, recovery_root: Path) -> bool:
    """Verify that every manager-root group still maps to its derived target, staging and recovery paths."""
    groups = [group for group in journal.groups if group.target_class == MANAGER_CLASS]
    if not groups:
        return True
    # A journal with editor groups cannot be checked without the manager layout, so it blocks
    if paths is None:
        return False
    try:
        for group in groups:
            target = editor_target(paths, group.entry_path)
            if Path(group.target_path) != target:
                return False
            ancestors = tuple(Path(value) for value in group.created_ancestors)
            if Path(group.staging_path) != editor_stage(target, journal.operation_id, ancestors):
                return False
            # A recovery copy exists exactly for a file that existed, at its derived path
            expected = editor_recovery(recovery_root, journal.operation_id, group.entry_path)
            if group.old_existed != (group.recovery_path is not None):
                return False
            if group.recovery_path is not None and Path(group.recovery_path).resolve(strict=False) != expected:
                return False
            if ancestors and not _chain(ancestors, target, paths):
                return False
        return True
    except (OSError, ValueError, RestorePathError):
        return False


def _chain(ancestors: tuple[Path, ...], target: Path, paths: PortablePaths) -> bool:
    """Return whether the created folders form one chain inside the editor area up to the target's folder."""
    area = paths.mission_map.resolve(strict=False)
    if ancestors[-1] != target.parent:
        return False
    for prior, path in zip((ancestors[0].parent, *ancestors[:-1]), ancestors):
        resolved = path.resolve(strict=False)
        if path.parent != prior or not (resolved == area or resolved.is_relative_to(area)):
            return False
        if path.exists() and (not path.is_dir() or is_reparse(path)):
            return False
    return True
