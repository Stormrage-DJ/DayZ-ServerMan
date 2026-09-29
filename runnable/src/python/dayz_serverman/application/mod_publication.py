"""Application workflow for reviewed managed-mod publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from ..domain.mod_publication import PublicationIntent
from ..repositories.mod_publication_inventory import (
    PublicationInventoryError,
    build_publication_intent,
)
from ..repositories.mod_publication_journal import PublicationJournalRepository
from ..repositories.mod_publication_journal import PublicationJournalError
from ..repositories.mod_publication_stage import (
    ModPublicationStorage,
    PublicationCancelled,
    PublicationStorageError,
)
from ..repositories.applied_mod_state import AppliedModStateRepository
from ..repositories.tree_metadata import TreeMetadataError
from ..adapters.windows.publication_paths import PublicationPathError, dayz_root_identity
from .operations.context import OperationContext
from .operations.manager import OperationManager
from .operations.models import OperationCancelled
from .profiles import ProfileService
from .mod_publication_gate import PublicationGateError, parse_publication_gate
from .mod_publication_start import (
    LifecycleStartPort,
    PublicationProofError,
    PublicationStartCancelled,
    empty_publication_journal,
    load_verified_retired_publication,
    reviewed_publication_fingerprint,
    start_server,
    verified_publication_result,
    verify_committed_publication,
)
from .settings import SettingsService


class ModPublicationError(RuntimeError):
    """Publication workflow failure carrying a bridge error code."""

    def __init__(self, code: str, message: str, *, recovery_required: bool = False) -> None:
        """Store the bridge code and whether recovery is required."""
        self.code = code
        self.recovery_required = recovery_required
        super().__init__(message)


@dataclass(frozen=True)
class PublicationRequest:
    """Frozen identity of the reviewed publication request."""
    profile_id: str
    profile_revision: int
    semantic_profile_digest: str
    settings_revision: int
    update_operation_id: str


class ModPublicationService:
    """Preview and publish reviewed managed mods and keys, then start once."""

    def __init__(
        self, profiles: ProfileService, settings: SettingsService,
        operations: OperationManager, storage_factory: Callable[[Callable], ModPublicationStorage],
        journals: PublicationJournalRepository, lifecycle: LifecycleStartPort,
        applied_state: AppliedModStateRepository | None = None,
    ) -> None:
        """Store collaborators and the optional applied-state recorder."""
        self._profiles = profiles
        self._settings = settings
        self._operations = operations
        self._storage_factory = storage_factory
        self._journals = journals
        self._lifecycle = lifecycle
        self._applied_state = applied_state

    def preview(self, request: PublicationRequest) -> dict[str, object]:
        """Return the reviewed targets and the fingerprint publish must echo."""
        # Rebuild the publication intent from the approved update gate
        intent = self._rebuild(request, f"preview-{request.update_operation_id[:32].lower()}")
        gate = self._operations.get(request.update_operation_id)
        start_requested = gate.result["start_requested"]
        # Fingerprint the review so publish can detect later drift
        return {
            "profile_id": intent.profile_id,
            "profile_revision": intent.profile_revision,
            "semantic_profile_digest": intent.semantic_profile_digest,
            "settings_revision": intent.settings_revision,
            "update_operation_id": request.update_operation_id,
            "publication_fingerprint": reviewed_publication_fingerprint(
                intent, request.update_operation_id, start_requested,
            ),
            "targets": [
                {"workshop_id": item.workshop_id, "target_relative": item.target_relative}
                for item in intent.managed_sources
            ],
            "key_count": len(intent.keys),
        }

    def publish(
        self, request: PublicationRequest, reviewed_fingerprint: str,
        context: OperationContext,
    ) -> dict[str, object]:
        """Stage, publish, verify, and optionally start the reviewed targets."""
        # Report preflight progress and rebuild the reviewed intent
        context.checkpoint("PUBLICATION_PREFLIGHT", 5)
        intent = self._rebuild(request, f"publication-{context.operation_id[:32].lower()}")
        gate = self._operations.get(request.update_operation_id)
        start_requested = gate.result["start_requested"]
        # Require the reviewer fingerprint so the approved plan cannot change
        if reviewed_publication_fingerprint(
            intent, request.update_operation_id, start_requested,
        ) != reviewed_fingerprint:
            raise ModPublicationError("PUBLICATION_PREVIEW_STALE", "Publication preview changed.")
        def storage_checkpoint(phase: str, index: int) -> None:
            """Relay storage progress while keeping cancellation authoritative."""
            progress = min(50, 10 + max(index, 0) * 5)
            try:
                context.checkpoint(phase, progress)
            except OperationCancelled as error:
                raise PublicationCancelled(str(error)) from error
        # Map storage progress into the publication budget
        storage = self._storage_factory(storage_checkpoint)
        context.checkpoint("BEFORE_PUBLICATION", 55)
        # Rebuild the intent again at the publication boundary
        final = self._rebuild(
            request, f"publication-{context.operation_id[:32].lower()}",
            checkpoint=lambda phase, index: context.checkpoint(
                phase, min(50, 10 + max(index, 0) * 5),
            ),
        )
        if final.fingerprint != intent.fingerprint:
            raise ModPublicationError("PUBLICATION_PREVIEW_STALE", "Publication context changed.")
        final_gate = self._operations.get(request.update_operation_id)
        final_start_requested = final_gate.result["start_requested"]
        if reviewed_publication_fingerprint(
            final, request.update_operation_id, final_start_requested,
        ) != reviewed_fingerprint:
            raise ModPublicationError("PUBLICATION_PREVIEW_STALE", "Publication review changed.")
        # Require the reviewed settings revision before staging anything
        settings = self._settings.load()
        if settings.revision != request.settings_revision or settings.dayz_root is None:
            raise ModPublicationError("REVISION_CONFLICT", "Manager settings changed.")
        # Stage and publish the reviewed targets, or journal an empty publication
        try:
            if final.managed_sources or final.keys:
                journal = storage.stage(final, Path(settings.dayz_root))
                storage.publish(journal, Path(settings.dayz_root), self._journals)
            else:
                journal = empty_publication_journal(final)
        except PublicationCancelled as error:
            raise OperationCancelled(str(error)) from error
        except PublicationJournalError as error:
            raise ModPublicationError(
                "RECOVERY_REQUIRED", "Publication journal verification failed.",
                recovery_required=True,
            ) from error
        except PublicationPathError as error:
            raise ModPublicationError("PATH_INVALID", str(error)) from error
        except OSError as error:
            raise ModPublicationError("PUBLICATION_FAILED", "Publication storage failed.") from error
        except (PublicationInventoryError, PublicationStorageError) as error:
            raise ModPublicationError(
                getattr(error, "code", "PUBLICATION_FAILED"), str(error),
                recovery_required=getattr(error, "recovery_required", False),
            ) from error
        # Record the applied state best effort; publication already succeeded
        if self._applied_state is not None and final.managed_sources:
            try:
                self._applied_state.record(final, Path(settings.dayz_root))
            except (OSError, ValueError, TypeError, TreeMetadataError):
                pass
        # Assemble the verified publication evidence for the operation
        result = verified_publication_result(
            final, journal, request.update_operation_id, final_start_requested,
            reviewed_fingerprint,
        )
        context.record_evidence(result)
        # Honor the optional start request only after verification
        if not final_start_requested:
            return result
        if context.cancellation_requested:
            return {**result, "start_state": "CANCELLED", "start_error": "UPDATE_CANCELLED"}
        # Rebuild once more so the start decision sees the final state
        post_publication = self._rebuild(
            request, f"publication-{context.operation_id[:32].lower()}",
        )
        if context.cancellation_requested:
            return _cancelled_start(result)
        if post_publication.fingerprint != final.fingerprint:
            raise ModPublicationError(
                "PUBLICATION_PREVIEW_STALE", "Publication context changed before start.",
            )
        post_gate = self._operations.get(request.update_operation_id)
        post_start_requested = post_gate.result["start_requested"]
        if reviewed_publication_fingerprint(
            post_publication, request.update_operation_id, post_start_requested,
        ) != reviewed_fingerprint:
            raise ModPublicationError(
                "PUBLICATION_PREVIEW_STALE", "Publication review changed before start.",
            )
        if context.cancellation_requested:
            return _cancelled_start(result)
        # Re-verify the retired journal and committed content before start
        try:
            persisted = journal
            if journal.groups:
                persisted = load_verified_retired_publication(
                    self._journals, post_publication, journal,
                    lambda: context.cancellation_requested,
                )
            verify_committed_publication(
                post_publication, persisted, Path(settings.dayz_root),
                lambda: context.cancellation_requested,
            )
        except PublicationStartCancelled:
            return _cancelled_start(result)
        except (OSError, PublicationPathError, PublicationInventoryError,
                PublicationJournalError, PublicationProofError) as error:
            raise ModPublicationError(
                "PUBLICATION_VERIFICATION_FAILED",
                "Published mods or keys changed before start.",
            ) from error
        if context.cancellation_requested:
            return _cancelled_start(result)
        result = {**result, "start_authorized": True, "start_error": None}
        context.record_evidence(result)
        if context.cancellation_requested:
            return _cancelled_start(result)
        # Hand off to the lifecycle start and fold in its snapshot
        result.update(start_server(self._lifecycle, post_publication))
        return result

    def _rebuild(
        self, request: PublicationRequest, publication_id: str,
        checkpoint: Callable[[str, int], None] | None = None,
    ) -> PublicationIntent:
        """Rebuild the publication intent from current profile and gate state."""
        # Re-read the profile and settings revisions that the review froze
        profile = self._profiles.read(request.profile_id)
        settings = self._settings.load()
        if (profile.revision != request.profile_revision
                or profile.semantic_digest != request.semantic_profile_digest
                or settings.revision != request.settings_revision):
            raise ModPublicationError("REVISION_CONFLICT", "Profile or settings changed.")
        if settings.dayz_root is None or settings.workshop_content_root is None:
            raise ModPublicationError("PATH_INVALID", "DayZ and Workshop roots are required.")
        gate = self._operations.get(request.update_operation_id)
        # Reparse the update gate into cache proofs for this intent
        try:
            proofs, _start_requested = parse_publication_gate(
                gate, request, profile, settings,
            )
        except PublicationGateError as error:
            raise ModPublicationError("PUBLICATION_PREVIEW_STALE", str(error)) from error
        # Build the publication intent from the proven inputs
        try:
            root_identity = dayz_root_identity(Path(settings.dayz_root))
            return build_publication_intent(
                publication_id=publication_id, profile=profile,
                settings_revision=settings.revision, dayz_root_identity=root_identity,
                cache_root=Path(settings.workshop_content_root), proofs=proofs,
                checkpoint=checkpoint,
            )
        except (PublicationInventoryError, PublicationPathError, OSError) as error:
            raise ModPublicationError(
                getattr(error, "code", "PATH_INVALID"), str(error),
            ) from error


def _cancelled_start(result: dict[str, object]) -> dict[str, object]:
    """Return a cancelled start result that keeps publication evidence."""
    return {
        **result,
        "start_authorized": False,
        "start_state": "CANCELLED",
        "start_error": "UPDATE_CANCELLED",
    }
