"""Restore path containment, copy, and checksum primitives."""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath

from ..adapters.windows.shared_files import open_shared
from ..domain.restores import RestoreGroup, RestoreJournal
from ..domain.profiles import ProfileValidationError, validate_relative_path
from .backup_verification import is_reparse, path_has_reparse, sha256_file


class RestorePathError(RuntimeError):
    """Raised for unsafe restore paths and failed copy verification."""
    def __init__(self, code: str, message: str) -> None:
        """Store the machine-readable error code with the message."""
        self.code = code
        super().__init__(message)


def target_relative(entry_path: str, runtime_profile: str | None = None) -> PurePosixPath:
    """Map a snapshot entry path to its path relative to the DayZ root."""
    parts = PurePosixPath(entry_path).parts
    if len(parts) < 2:
        raise RestorePathError("PATH_INVALID", "Snapshot target mapping is invalid.")
    # Bare payload entries map directly below the DayZ root
    if parts[0] == "payload":
        return PurePosixPath(*parts[1:])
    # Only payload and runtime-profile entries have a defined target mapping
    if parts[0] != "runtime-profile" or runtime_profile is None:
        raise RestorePathError("PATH_INVALID", "Snapshot target mapping is invalid.")
    try:
        normalized = validate_relative_path(runtime_profile, "runtime_profile")
    except ProfileValidationError as error:
        raise RestorePathError("PATH_INVALID", "Runtime profile mapping is invalid.") from error
    assert normalized is not None
    return PurePosixPath(*normalized.replace("\\", "/").split("/"), *parts[1:])


def safe_root(root: Path) -> Path:
    """Resolve the DayZ root, rejecting network, relative, and linked paths."""
    # Reject UNC and relative roots before touching the filesystem
    if str(root).replace("/", "\\").startswith("\\\\") or not root.is_absolute():
        raise RestorePathError("PATH_INVALID", "DayZ root must be a local absolute path.")
    # Resolve strictly so safety checks run against the final target
    resolved = root.resolve(strict=True)
    if not resolved.is_dir() or path_has_reparse(root):
        raise RestorePathError("PATH_INVALID", "DayZ root is unsafe.")
    return resolved


def safe_target(
    root: Path, relative: PurePosixPath, *, allow_missing_parents: bool = False,
) -> Path:
    """Resolve a restore target below the root and reject unsafe paths."""
    # Reject absolute, parent-traversal, and drive-qualified path parts
    if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts):
        raise RestorePathError("PATH_INVALID", "Restore target path is unsafe.")
    # Build the target and prove it stays below the DayZ root
    target = root.joinpath(*relative.parts)
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise RestorePathError("PATH_INVALID", "Restore target escapes DayZ root.") from error
    # Walk up to the nearest existing ancestor for the safety checks
    parent = _nearest_existing(target.parent, root)
    if (
        (not allow_missing_parents and parent != target.parent)
        or not parent.is_dir() or path_has_reparse(parent)
    ):
        raise RestorePathError("PATH_INVALID", "Restore target parent is unsafe or missing.")
    # Existing targets must be plain files, never links or reparse points
    if target.exists() and (not target.is_file() or is_reparse(target)):
        raise RestorePathError("PATH_INVALID", "Restore target is not a regular file.")
    if not os.access(parent, os.W_OK):
        raise RestorePathError("PATH_NOT_WRITABLE", "Restore target is not writable.")
    return target


def missing_ancestors(root: Path, target: Path) -> tuple[Path, ...]:
    """Return the directory chain that must be created before the target."""
    parent = target.parent
    existing = _nearest_existing(parent, root)
    relative = parent.relative_to(existing)
    # Collect each missing directory between the ancestor and the parent
    result: list[Path] = []
    current = existing
    for part in relative.parts:
        current /= part
        result.append(current)
    return tuple(result)


def _nearest_existing(path: Path, root: Path) -> Path:
    """Walk upward until an existing ancestor of the target is found."""
    current = path
    while not current.exists():
        if current == root:
            break
        current = current.parent
    # Confirm the ancestor stays inside the DayZ root
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as error:
        raise RestorePathError("PATH_INVALID", "Restore target ancestor is unsafe.") from error
    return current


def copy_verified(source: Path, target: Path, digest: str | None) -> None:
    """Copy a source into a staging path and verify the copied digest."""
    # Refuse sources that are not digest-verified regular files
    if digest is None or not source.is_file() or is_reparse(source):
        raise RestorePathError("RESTORE_VERIFY_FAILED", "Restore copy source is invalid.")
    # Create parents and refuse to overwrite an existing staging file
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RestorePathError("RESTORE_CONFLICT", "Restore staging path already exists.")
    with open_shared(source) as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    # Hash the copy so a torn or tampered write cannot pass
    if sha256_file(target) != digest:
        raise RestorePathError("RESTORE_VERIFY_FAILED", "Restore copy failed checksum verification.")


def matches(path: Path, digest: str | None) -> bool:
    """Return True when the file exists and its digest matches."""
    return digest is not None and path.is_file() and not is_reparse(path) and sha256_file(path) == digest


def group_old_matches(group: RestoreGroup) -> bool:
    """Return True when the target still matches its pre-restore state."""
    target = Path(group.target_path)
    return matches(target, group.old_digest) if group.old_existed else not target.exists()


def old_state_matches(groups: list[RestoreGroup]) -> bool:
    """Return True when every group still matches its pre-restore state."""
    return all(group_old_matches(group) for group in groups)


def remove_staging(groups: list[RestoreGroup]) -> None:
    """Remove staged copies created for a restore attempt."""
    for group in groups:
        path = Path(group.staging_path)
        # Remove the staged file before pruning its stage directory
        if path.is_file() and not is_reparse(path):
            path.unlink()
        remove_stage_tree(group)


def remove_stage_tree(group: RestoreGroup) -> None:
    """Prune the stage directory that contains a staged copy."""
    path = Path(group.staging_path)
    # Stop at the first dedicated stage directory above the file
    for parent in path.parents:
        if not parent.name.startswith(".serverman-") or not parent.name.endswith("-stage"):
            continue
        if parent.exists() and not is_reparse(parent):
            shutil.rmtree(parent)
        break


def journal_paths_safe(journal: RestoreJournal, dayz_root: Path, recovery_root: Path) -> bool:
    """Verify every journal path still maps to its expected location."""
    try:
        root = safe_root(dayz_root)
        recovery = recovery_root.resolve(strict=False)
        # Re-derive each expected path so tampered journals are rejected
        for group in journal.groups:
            target = Path(group.target_path).resolve(strict=False)
            relative = target_relative(group.entry_path, journal.runtime_profile)
            if target != root.joinpath(*relative.parts).resolve(strict=False):
                return False
            ancestors = tuple(Path(value) for value in group.created_ancestors)
            if ancestors:
                nearest = ancestors[0].parent
                expected_stage = nearest / f".serverman-{journal.operation_id}-stage" / target.relative_to(nearest)
            else:
                expected_stage = target.with_name(f".{target.name}.{journal.operation_id}.restore-stage")
            if Path(group.staging_path).resolve(strict=False) != expected_stage:
                return False
            # Recovery copies must sit under the operation recovery folder
            expected_recovery = recovery / journal.operation_id / Path(*relative.parts)
            if group.old_existed:
                if group.recovery_path is None:
                    return False
                if Path(group.recovery_path).resolve(strict=False) != expected_recovery:
                    return False
                if path_has_reparse(Path(group.recovery_path).parent):
                    return False
            elif group.recovery_path is not None:
                return False
            if path_has_reparse(target.parent) or path_has_reparse(expected_stage.parent):
                if not ancestors:
                    return False
            if ancestors:
                if ancestors[-1].resolve(strict=False) != target.parent.resolve(strict=False):
                    return False
                if any(
                    path.parent.resolve(strict=False) != prior.resolve(strict=False)
                    for prior, path in zip((ancestors[0].parent, *ancestors[:-1]), ancestors)
                ):
                    return False
                # Created ancestors must be new, inside the root, and real directories
                for path in ancestors:
                    path.resolve(strict=False).relative_to(root)
                    if path.exists() and (not path.is_dir() or is_reparse(path)):
                        return False
        return True
    except (OSError, ValueError, RestorePathError):
        return False
