"""Prepare changed and unchanged publication groups without touching live targets."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from ..adapters.windows.publication_paths import (
    artifact_paths,
    copy_file,
    copy_tree,
    validate_existing_keys,
)
from ..domain.mod_publication import (
    GroupState,
    PublicationGroup,
    PublicationIntent,
    TargetRole,
)
from .backup_verification import is_reparse, sha256_file
from .mod_publication_inventory import inventory_tree
from .tree_metadata import TreeMetadataError, tree_metadata_digest


class StagingError(RuntimeError):
    """Raised when a publication group cannot be staged."""
    pass


def stage_mod_group(
    *,
    operation: str,
    ordinal: int,
    relative: str,
    source: Path,
    target: Path,
    expected_digest: str,
    expected_source_metadata: str,
    expected_target_metadata: str | None,
    target_current: bool,
    checkpoint: Callable[[str, int], None],
    fault: Callable[[str, int], None],
) -> PublicationGroup:
    """Stage a managed mod directory, or reuse a verified unchanged target."""
    # Reserve sibling artifact paths beside the live target
    stage, recovery = artifact_paths(target, operation, ordinal)
    _require_clear_artifacts(stage, recovery)
    try:
        # Refuse a source that changed since the preview signed it
        if tree_metadata_digest(source) != expected_source_metadata:
            raise StagingError("PUBLICATION_PREVIEW_STALE: managed source changed")
    except TreeMetadataError as error:
        raise StagingError("PUBLICATION_PREVIEW_STALE: managed source changed") from error
    # A current target with matching metadata needs no staging copy
    if target_current:
        try:
            if (expected_target_metadata is None or not target.exists()
                    or tree_metadata_digest(target) != expected_target_metadata):
                raise StagingError("PUBLICATION_PREVIEW_STALE: managed target changed")
        except TreeMetadataError as error:
            raise StagingError("PUBLICATION_PREVIEW_STALE: managed target changed") from error
        return PublicationGroup(
            TargetRole.MANAGED_MOD_DIRECTORY, ordinal, relative, stage.name, recovery.name,
            True, expected_digest, expected_digest, GroupState.UNCHANGED_VERIFIED,
        )
    # Capture the prior target inventory for rollback evidence
    prior = inventory_tree(target) if target.exists() else None
    try:
        # Reuse the existing target when it already matches the expected output
        if prior == expected_digest:
            return PublicationGroup(
                TargetRole.MANAGED_MOD_DIRECTORY, ordinal, relative, stage.name, recovery.name,
                True, prior, expected_digest, GroupState.UNCHANGED_VERIFIED,
            )
        # Copy the source tree into the sibling stage directory
        copy_tree(source, stage, checkpoint, ordinal)
        # Verify the staged copy before publishing its inventory
        output = inventory_tree(stage)
        if output != expected_digest:
            raise StagingError("PUBLICATION_STAGE_FAILED: staged source changed")
        fault("AFTER_STAGE", ordinal)
        return PublicationGroup(
            TargetRole.MANAGED_MOD_DIRECTORY, ordinal, relative, stage.name, recovery.name,
            prior is not None, prior, output,
        )
    except Exception:
        # Never leave a partial stage behind on failure
        _remove_partial_stage(stage)
        raise


def stage_keys_group(
    *,
    intent: PublicationIntent,
    ordinal: int,
    target: Path,
    checkpoint: Callable[[str, int], None],
    fault: Callable[[str, int], None],
) -> PublicationGroup:
    """Stage the keys directory, preserving existing non-colliding keys."""
    # Reserve sibling artifact paths beside the live keys directory
    stage, recovery = artifact_paths(target, intent.publication_id, ordinal)
    _require_clear_artifacts(stage, recovery)
    # Record the prior keys inventory for rollback evidence
    prior = inventory_tree(target) if target.exists() else None
    try:
        # Index existing keys case-insensitively to detect collisions
        existing = _existing_keys(target) if target.exists() else {}
        missing = []
        for key in intent.keys:
            current = existing.get(key.filename.casefold())
            if current is not None and sha256_file(current) != key.content_digest:
                raise StagingError(f"KEY_COLLISION: key collision: {key.filename}")
            if current is None:
                missing.append(key)
        # Nothing to add means the existing keys already satisfy the intent
        if prior is not None and not missing:
            return PublicationGroup(
                TargetRole.SERVER_KEYS_DIRECTORY, ordinal, "keys", stage.name, recovery.name,
                True, prior, prior, GroupState.UNCHANGED_VERIFIED,
            )
        # Start from a copy of the existing directory or an empty stage
        if target.exists():
            copy_tree(target, stage, checkpoint, ordinal)
        else:
            stage.mkdir()
        # Copy only the keys that are not already present
        for key in missing:
            checkpoint("COPY_KEY", ordinal)
            copy_file(Path(key.source_path), stage / key.filename, key.content_digest)
        # Inventory the final staged keys tree
        output = inventory_tree(stage)
        fault("AFTER_STAGE", ordinal)
        return PublicationGroup(
            TargetRole.SERVER_KEYS_DIRECTORY, ordinal, "keys", stage.name, recovery.name,
            prior is not None, prior, output,
        )
    except Exception:
        # Remove the partial stage so a retry starts clean
        _remove_partial_stage(stage)
        raise


def _existing_keys(target: Path) -> dict[str, Path]:
    """Index existing key files by folded name, refusing name collisions."""
    validate_existing_keys(target)
    # Ignore subdirectories; keys are plain files
    files = [path for path in target.iterdir() if path.is_file()]
    existing = {path.name.casefold(): path for path in files}
    # Two files folding to one name would make the key set ambiguous
    if len(existing) != len(files):
        raise StagingError("KEY_COLLISION: existing keys have a name collision")
    return existing


def _require_clear_artifacts(stage: Path, recovery: Path) -> None:
    """Refuse group staging while leftover artifacts exist."""
    if stage.exists() or recovery.exists():
        raise StagingError("PUBLICATION_STAGE_FAILED: publication artifact exists")


def _remove_partial_stage(stage: Path) -> None:
    """Delete a partial stage directory unless it is a reparse point."""
    # A reparse point must never be deleted recursively
    if stage.exists() and stage.is_dir() and not is_reparse(stage):
        shutil.rmtree(stage)
