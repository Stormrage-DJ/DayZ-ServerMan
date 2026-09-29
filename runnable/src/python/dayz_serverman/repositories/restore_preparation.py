"""Prepare verified restore staging and recovery groups."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from ..domain.restores import RestoreGroup, RestoreTarget
from .restore_paths import missing_ancestors, remove_staging, safe_root, safe_target


def prepare_groups(
    snapshot: Path,
    targets: tuple[RestoreTarget, ...],
    dayz_root: Path,
    recovery_root: Path,
    operation_id: str,
    checkpoint: Callable[[str, int], None],
    disk_usage: Callable[[Path], Any],
    copy_file: Callable[[Path, Path, str | None], None],
) -> list[RestoreGroup]:
    """Stage snapshot payloads and recovery copies, then verify free space."""
    recovery_base = recovery_root.resolve(strict=False) / operation_id
    # Reserve the operation recovery folder exclusively so leftovers are visible
    recovery_base.mkdir(parents=True, exist_ok=False)
    groups: list[RestoreGroup] = []
    # Track required bytes per destination volume for the space check
    required_by_parent: dict[Path, int] = {}
    try:
        for index, item in enumerate(targets):
            # Resolve and validate the target below the DayZ root
            root = safe_root(dayz_root)
            target = safe_target(
                root, PurePosixPath(item.target_relative),
                allow_missing_parents=item.entry_path.startswith("runtime-profile/"),
            )
            ancestors = missing_ancestors(root, target)
            # Keep the stage on the target's volume so the swap stays a rename
            if ancestors:
                nearest = ancestors[0].parent
                stage = nearest / f".serverman-{operation_id}-stage" / target.relative_to(nearest)
            else:
                stage = target.with_name(f".{target.name}.{operation_id}.restore-stage")
            recovery = recovery_base.joinpath(*PurePosixPath(item.target_relative).parts)
            space_parent = ancestors[0].parent if ancestors else target.parent
            required_by_parent[space_parent] = required_by_parent.get(space_parent, 0) + item.size
            existed = target.exists()
            group = RestoreGroup(
                item.entry_path, str(target), str(stage), str(recovery) if existed else None,
                existed, item.current_digest, item.snapshot_digest,
                created_ancestors=tuple(str(path) for path in ancestors),
            )
            groups.append(group)
            # Preserve the current file in recovery before publication can touch it
            if existed:
                required_by_parent[space_parent] += target.stat().st_size
                copy_file(target, recovery, item.current_digest)
            # Stage the snapshot payload and verify it as it is copied
            copy_file(snapshot / item.entry_path, stage, item.snapshot_digest)
            checkpoint("STAGE_TARGETS", 25 + int(25 * (index + 1) / len(targets)))
        # Require free space for every volume the restore will touch
        for parent, required in required_by_parent.items():
            # Keep a 16 KiB margin so a nearly full volume cannot fail mid-copy
            if disk_usage(parent).free < required + 16_384:
                # Imported lazily to avoid a circular import with the storage layer
                from .restore_storage import RestoreStorageError
                raise RestoreStorageError(
                    "INSUFFICIENT_SPACE", "A destination volume lacks free space.",
                )
        checkpoint("PREPARE_RECOVERY", 55)
        return groups
    except Exception:
        # Roll back partial staging so a failed preparation leaves no debris
        remove_staging(groups)
        shutil.rmtree(recovery_base, ignore_errors=True)
        raise
