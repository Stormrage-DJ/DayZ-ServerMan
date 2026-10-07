"""Application workflow for reviewed managed-mod publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from ..domain.mod_publication import PublicationIntent, PublicationJournal
from ..repositories.mod_publication_inventory import (
    PublicationInventoryError,
    build_publication_intent,
)
from ..repositories.mod_publication_journal import PublicationJournalRepository
from ..repositories.mod_publication_stage import ModPublicationStorage, PublicationCancelled
from ..adapters.windows.publication_paths import PublicationPathError, dayz_root_identity
from .content_proof_records import ContentProofRecorder
from .folder_writer_scope import WriterScope
from .installation_guard import PLAIN_APPLY_REQUIRES_GUARD, InstallationGuard
from .lifecycle_ports import ServerFolderWriterPort
from .mod_publication_apply import apply_publication, missing_keys, publication_writes
from .operations.context import OperationContext
from .operations.manager import OperationManager
from .operations.models import OperationCancelled
from .profiles import ProfileService
from .mod_publication_gate import PublicationGateError, parse_publication_gate
from .mod_publication_prestart import PrestartFingerprints
from .mod_publication_start import (  # noqa: F401 - re-exported for existing importers
    LifecycleStartPort,
    ModPublicationError,
    PublicationProofError,
    PublicationStartCancelled,
    empty_publication_journal,
    load_verified_retired_publication,
    reviewed_publication_fingerprint,
    start_after_publication,
    start_server,
    verified_publication_result,
    verify_committed_publication,
)
from .settings import SettingsService


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
        content_proofs: ContentProofRecorder | None = None,
        prestart: PrestartFingerprints | None = None,
        guard: InstallationGuard | None = None,
        guard_plain_apply: bool = PLAIN_APPLY_REQUIRES_GUARD,
        folder_writer: ServerFolderWriterPort | None = None,
    ) -> None:
        """Store collaborators, the optional proof recorder, pre-start rule and write guard.

        `guard_plain_apply` is the policy of decision D10: whether a writing
        publication without a requested start also runs inside the guard.
        `folder_writer` is the A13 writer side; None means no lock is wired.
        """
        self._profiles = profiles
        self._settings = settings
        self._operations = operations
        self._storage_factory = storage_factory
        self._journals = journals
        self._lifecycle = lifecycle
        self._content_proofs = content_proofs
        self._prestart = prestart
        self._guard = guard
        self._guard_plain_apply = guard_plain_apply
        self._folder_writer = folder_writer

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
                {"workshop_id": item.workshop_id, "target_relative": item.target_relative,
                 "current": item.target_current}
                for item in intent.managed_sources
            ],
            "key_count": len(intent.keys),
            # Key files of the plan that the keys folder does not hold yet
            "missing_key_count": missing_keys(intent, Path(self._settings.load().dayz_root)),
            # Effective policy of decision D10, so the page never guesses it
            "plain_apply_guarded": self._guard is not None and self._guard_plain_apply,
        }

    def confirm_restart_plan(self, request: PublicationRequest, reviewed_fingerprint: str) -> bool:
        """Require the reviewed plan with a requested start; return whether it would write.

        This is the plan check of a restart before anything stops. It changes nothing.
        """
        intent = self._rebuild(request, f"confirm-{request.update_operation_id[:32].lower()}")
        gate = self._operations.get(request.update_operation_id)
        # Only the reviewed plan of an update that asked for a start may stop the server
        if gate.result["start_requested"] is not True or reviewed_publication_fingerprint(
            intent, request.update_operation_id, True,
        ) != reviewed_fingerprint:
            raise ModPublicationError("PUBLICATION_PREVIEW_STALE", "Publication preview changed.")
        return publication_writes(intent, Path(self._settings.load().dayz_root))

    def publish(
        self, request: PublicationRequest, reviewed_fingerprint: str,
        context: OperationContext, *, writer_scope: WriterScope | None = None,
    ) -> dict[str, object]:
        """Stage, publish, verify, and optionally start the reviewed targets.

        An apply and restart passes the `writer_scope` that it took before the stop (QF-2);
        then this run takes no writer side of its own.
        """
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
        scope = writer_scope if writer_scope is not None else WriterScope(self._folder_writer)
        def storage_checkpoint(phase: str, index: int) -> None:
            """Relay storage progress while keeping cancellation authoritative."""
            # A13: a writing run takes the writer side before BEFORE_PUBLICATION is relayed
            if (writer_scope is None and self._folder_writer is not None and phase == "BEFORE_PUBLICATION"
                    and publication_writes(final, Path(settings.dayz_root))):
                scope.acquire()
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
        def record_proofs(committed: PublicationJournal) -> None:
            """Record the content proofs of the committed publication, best effort."""
            if (self._content_proofs is not None and final.managed_sources
                    and settings.workshop_content_root is not None):
                self._content_proofs.record_publication(
                    final, Path(settings.dayz_root), Path(settings.workshop_content_root),
                    committed, getattr(storage, "hashed_fingerprints", None),
                )
        # Stage and publish the reviewed targets; a writing run takes the write guard
        journal = apply_publication(
            storage=storage, journals=self._journals, intent=final,
            dayz_root=Path(settings.dayz_root), start_requested=final_start_requested,
            guard=self._guard, guard_plain_apply=self._guard_plain_apply,
            record_proofs=record_proofs, writer_scope=scope,
        )
        # Assemble the verified publication evidence for the operation
        result = verified_publication_result(
            final, journal, request.update_operation_id, final_start_requested,
            reviewed_fingerprint,
        )
        context.record_evidence(result)
        # Honor the optional start request only after verification
        if not final_start_requested:
            return result
        # Re-check the committed publication, then hand off to the lifecycle start
        return start_after_publication(
            result=result, final=final, journal=journal, dayz_root=Path(settings.dayz_root),
            reviewed_fingerprint=reviewed_fingerprint,
            update_operation_id=request.update_operation_id,
            rebuild=lambda: self._rebuild(
                request, f"publication-{context.operation_id[:32].lower()}",
            ),
            start_requested=lambda: self._operations.get(
                request.update_operation_id,
            ).result["start_requested"],
            journals=self._journals, lifecycle=self._lifecycle, context=context,
            prestart=self._prestart,
        )

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

