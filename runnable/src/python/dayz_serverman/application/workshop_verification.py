"""Deliberate full hash of Workshop sources and their server-folder copies ("Verify files")."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..adapters.windows.publication_paths import (
    PublicationPathError,
    dayz_root_identity,
    safe_dayz_root,
    safe_target,
)
from ..domain.content_proofs import SourceProofRecord, TargetProofRecord
from ..domain.workshop import derive_required_items
from ..repositories.content_proofs import ContentProofStore
from ..repositories.mod_publication_inventory import PublicationInventoryError, inventory_tree
from ..repositories.tree_metadata import TreeMetadataError, tree_metadata_digest
from ..repositories.workshop_cache import (
    CacheVerificationError,
    HashingCancelled,
    WorkshopCacheVerifier,
)
from .content_proof_records import source_record
from .operations.context import OperationContext
from .operations.models import OperationCancelled, OperationFailure
from .profiles import ProfileService
from .settings import SettingsService

# Errors that make one target tree unusable for a digest comparison
_TARGET_ERRORS = (PublicationPathError, PublicationInventoryError, TreeMetadataError,
                  CacheVerificationError, OSError, ValueError)


@dataclass(frozen=True)
class VerifyRequest:
    """Immutable request describing one "Verify files" submission."""

    profile_id: str
    expected_profile_revision: int
    expected_settings_revision: int


def progress_percent(finished_half_steps: int, item_count: int) -> int:
    """Return the percent for the finished half-steps: 5 + 90 x finished / (2 x items)."""
    return 5 + 90 * finished_half_steps // (2 * item_count)


class WorkshopVerificationService:
    """Hash every Workshop source of a profile and its target; replace the stored proofs."""

    def __init__(
        self, profiles: ProfileService, settings: SettingsService, store: ContentProofStore,
        verifier_factory: Callable[[Path], WorkshopCacheVerifier] = WorkshopCacheVerifier,
    ) -> None:
        """Store the profile and settings services, the proof store and the verifier."""
        self._profiles = profiles
        self._settings = settings
        self._store = store
        self._verifier_factory = verifier_factory

    def verify(self, request: VerifyRequest, context: OperationContext) -> dict[str, object]:
        """Verify each item in profile order, source first, and report the states."""
        # Reject stale revisions before any filesystem work
        settings = self._settings.load()
        profile = self._profiles.read(request.profile_id)
        if (settings.revision != request.expected_settings_revision
                or profile.revision != request.expected_profile_revision):
            raise OperationFailure("REVISION_CONFLICT", "Settings or profile changed. Reload.")
        # Both roots must be configured and the server folder must be usable
        if settings.workshop_content_root is None or settings.dayz_root is None:
            raise OperationFailure("PATH_INVALID", "Configure the Workshop and DayZ folders.")
        try:
            root = safe_dayz_root(Path(settings.dayz_root))
            root_identity = dayz_root_identity(root)
        except (PublicationPathError, OSError, ValueError) as error:
            raise OperationFailure("PATH_INVALID", "The DayZ folder is not usable.") from error
        items = derive_required_items(profile)
        if not items:
            return {"items": []}
        verifier = self._verifier_factory(Path(settings.workshop_content_root))
        # The cache and its manifest must be readable before any item is hashed
        try:
            cache_identity = verifier.root_identity()
            verifier.observe(tuple(item.workshop_id for item in items))
        except CacheVerificationError as error:
            raise OperationFailure("WORKSHOP_MANIFEST_INVALID", str(error)) from error
        directories = {
            mod.source.workshop_id: mod.directory for mod in profile.values.mods
            if mod.source.kind == "workshop"
        }
        detail = [{"workshop_id": item.workshop_id, "phase": "queued",
                   "done_bytes": None, "total_bytes": None} for item in items]

        def cancelled() -> bool:
            """Report a cancellation request; asked before each file."""
            return context.cancellation_requested

        results: list[dict[str, object]] = []
        for index, item in enumerate(items):
            workshop_id, directory = item.workshop_id, directories[item.workshop_id]
            try:
                # Source half-step: hash the cached item
                context.checkpoint("verify_source", progress_percent(2 * index, len(items)))
                detail[index]["phase"] = "verify_source"
                context.publish_detail(detail)
                source_state, record = self._verify_source(
                    verifier, cache_identity, workshop_id, cancelled)
                # Target half-step: hash the server-folder copy
                context.checkpoint("verify_target", progress_percent(2 * index + 1, len(items)))
                total = record.total_regular_bytes if record is not None else None
                detail[index].update(phase="verify_target", total_bytes=total)
                context.publish_detail(detail)
                target_state, fingerprint = _verify_target(
                    root, directory,
                    record.content_inventory_digest if record is not None else None, cancelled)
            except HashingCancelled as error:
                # The item in progress writes nothing; finished items stay saved
                raise OperationCancelled("file verification was cancelled") from error
            verified_at = datetime.now(UTC).isoformat(timespec="milliseconds")
            # Replace the stored proofs of this item with what was measured
            target_key = (root_identity, directory)
            matches = target_state == "MATCHES_SOURCE" and record is not None
            self._store.record(
                sources={workshop_id: record} if record is not None else None,
                targets={target_key: TargetProofRecord(
                    workshop_id, record.installed_manifest_id, record.content_inventory_digest,
                    fingerprint, verified_at, "VERIFIED")} if matches else None,
                remove_sources=() if record is not None else (workshop_id,),
                remove_targets=() if matches else (target_key,),
            )
            detail[index].update(phase="done", done_bytes=total)
            context.publish_detail(detail)
            results.append({
                "workshop_id": workshop_id, "source_state": source_state,
                "target_state": target_state, "verified_at": verified_at,
                "error_code": _error_code(source_state, target_state),
            })
        return {"items": results}

    def _verify_source(
        self, verifier: WorkshopCacheVerifier, cache_identity: str, workshop_id: str,
        cancelled: Callable[[], bool],
    ) -> tuple[str, SourceProofRecord | None]:
        """Return the source state and, for a hashed source, its new proof record."""
        try:
            observation = verifier.observe((workshop_id,))[0]
            # No installed record or no directory: there is nothing to hash
            if not observation.installed or not verifier.source_directory_exists(workshop_id):
                return "MISSING", None
            proof = verifier.verify(workshop_id, cancelled)
        except (CacheVerificationError, OSError):
            # Unsafe tree, read error or incomplete manifest
            return "FAILED", None
        record = source_record(cache_identity, observation, proof)
        if record is None:
            return "FAILED", None
        stored = self._store.load().sources.get(workshop_id)
        # The same manifest id with another digest means the content changed
        if (stored is not None and stored.installed_manifest_id == record.installed_manifest_id
                and stored.content_inventory_digest != record.content_inventory_digest):
            return "CHANGED", record
        return "VERIFIED", record


def _verify_target(
    root: Path, directory: str, source_digest: str | None, cancelled: Callable[[], bool],
) -> tuple[str, str | None]:
    """Return the target state and, for a matching target, its fingerprint."""
    try:
        target = safe_target(root, directory)
        if not target.exists():
            return "NOT_APPLIED", None
        # A copy cannot be compared when the source has no digest
        if source_digest is None:
            return "FAILED", None
        # The fingerprint must not move while the content is hashed
        before = tree_metadata_digest(target)
        digest = inventory_tree(target, cancelled)
        fingerprint = tree_metadata_digest(target)
    except _TARGET_ERRORS:
        return "FAILED", None
    if before != fingerprint:
        return "FAILED", None
    return ("MATCHES_SOURCE", fingerprint) if digest == source_digest else ("DIFFERS", None)


def _error_code(source_state: str, target_state: str) -> str | None:
    """Return the item error code; a failed source outranks a failed target."""
    if source_state == "FAILED":
        return "CACHE_VERIFICATION_FAILED"
    if target_state == "FAILED":
        return "TARGET_VERIFICATION_FAILED"
    return None
