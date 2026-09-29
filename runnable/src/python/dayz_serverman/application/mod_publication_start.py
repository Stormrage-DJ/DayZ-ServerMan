"""Post-publication proof and the single authorized server-start handoff."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from ..adapters.windows.publication_paths import safe_dayz_root, safe_target
from ..domain.lifecycle import LifecycleFailure, LifecycleSnapshot
from ..domain.mod_publication import (
    GroupState,
    PublicationIntent,
    PublicationJournal,
    PublicationPhase,
    publication_fingerprint,
    canonical_digest,
)
from ..repositories.mod_publication_inventory import inventory_tree
from ..repositories.mod_publication_journal import PublicationJournalRepository


class LifecycleStartPort(Protocol):
    """Port for handing a verified publication to server start."""
    # Start the server for the published profile and return its snapshot
    def start(
        self,
        profile_id: str,
        expected_profile_revision: int,
        expected_settings_revision: int,
    ) -> LifecycleSnapshot: ...


class PublicationProofError(RuntimeError):
    """The committed publication no longer matches its verified proof."""


class PublicationStartCancelled(RuntimeError):
    """Start was cancelled after publication and before lifecycle handoff."""


def reviewed_publication_fingerprint(
    intent: PublicationIntent,
    update_operation_id: str,
    start_requested: bool,
) -> str:
    """Fingerprint the reviewed publication including the start decision."""
    # Bind the intent, update operation, and start choice into one proof
    return canonical_digest({
        "contract": "dayz-serverman-mod-publication-review-v1",
        "intent_fingerprint": intent.fingerprint,
        "update_operation_id": update_operation_id,
        "start_requested": start_requested,
    })


def empty_publication_journal(intent: PublicationIntent) -> PublicationJournal:
    """Build a committed journal for a publication with no managed targets."""
    # Commit an empty journal so the result shape matches staged runs
    journal = PublicationJournal(
        intent.publication_id, intent.fingerprint, PublicationPhase.COMMITTED,
        True, True, True, "COMMITTED", [],
    )
    # Fingerprint the empty group set for later verification
    journal.publication_fingerprint = publication_fingerprint(intent.fingerprint, [])
    return journal


def verified_publication_result(
    intent: PublicationIntent,
    journal: PublicationJournal,
    update_operation_id: str,
    start_requested: bool,
    reviewed_fingerprint: str,
) -> dict[str, object]:
    """Assemble the verified publication evidence returned to the bridge."""
    # Report managed targets and keys, requesting start only when asked
    return {
        "profile_id": intent.profile_id,
        "publication_fingerprint": reviewed_fingerprint,
        "update_operation_id": update_operation_id,
        "publication_state": "VERIFIED",
        "key_state": "VERIFIED",
        "managed_targets": [
            {"target_relative": group.target_relative, "digest": group.output_digest}
            for group in journal.groups if group.target_relative.casefold() != "keys"
        ],
        "keys_digest": next(
            (group.output_digest for group in journal.groups
             if group.target_relative.casefold() == "keys"), None,
        ),
        "start_requested": start_requested,
        "start_authorized": False,
        "start_state": "PENDING" if start_requested else "NOT_REQUESTED",
        "start_error": "PUBLICATION_REQUIRED" if start_requested else None,
    }


def verify_committed_publication(
    intent: PublicationIntent,
    journal: PublicationJournal,
    dayz_root: Path,
    cancellation_requested: Callable[[], bool] = lambda: False,
) -> None:
    """Re-prove the journal and the published bytes before any start."""
    # Rebuild the expected target list from the reviewed intent
    expected_targets = tuple(item.target_relative for item in intent.managed_sources)
    if intent.keys:
        expected_targets += ("keys",)
    if (
        journal.intent_fingerprint != intent.fingerprint
        or journal.publication_fingerprint != publication_fingerprint(
            intent.fingerprint, journal.groups,
        )
        or journal.phase != PublicationPhase.COMMITTED
        or not journal.committed
        or not journal.resolved
        or journal.result != "COMMITTED"
        or tuple(group.target_relative for group in journal.groups) != expected_targets
        or any(group.state not in (
            GroupState.OUTPUT_VERIFIED, GroupState.UNCHANGED_VERIFIED,
        ) for group in journal.groups)
    ):
        raise PublicationProofError("Publication completion evidence is invalid.")
    root = safe_dayz_root(dayz_root)
    # Walk every published group and require unchanged content
    for group in journal.groups:
        if inventory_tree(safe_target(root, group.target_relative)) != group.output_digest:
            raise PublicationProofError("Published mod or key content changed before start.")
        if cancellation_requested():
            raise PublicationStartCancelled


def load_verified_retired_publication(
    repository: PublicationJournalRepository,
    intent: PublicationIntent,
    committed: PublicationJournal,
    cancellation_requested: Callable[[], bool] = lambda: False,
) -> PublicationJournal:
    """Load the retired journal and require it to match the committed one."""
    active = repository.path_for(intent.publication_id)
    # The active journal path must be gone before start
    if active.exists():
        raise PublicationProofError("Publication journal did not retire.")
    # Load from the retired location and verify repository authority
    persisted = repository.load(repository.retired_root / active.name)
    if cancellation_requested():
        raise PublicationStartCancelled
    repository.verify_authority(persisted)
    if cancellation_requested():
        raise PublicationStartCancelled
    # Require persisted evidence identical to the committed journal
    if persisted.to_dict() != committed.to_dict():
        raise PublicationProofError("Persisted publication evidence changed.")
    if cancellation_requested():
        raise PublicationStartCancelled
    return persisted


def start_server(
    lifecycle: LifecycleStartPort,
    intent: PublicationIntent,
) -> dict[str, object]:
    """Hand off the verified publication to the lifecycle start port."""
    # Map lifecycle failures into the start result instead of raising
    try:
        snapshot = lifecycle.start(
            intent.profile_id,
            intent.profile_revision,
            intent.settings_revision,
        )
    except LifecycleFailure as error:
        return {
            "start_authorized": True,
            "start_state": "FAILED",
            "start_error": error.code,
            "start_message": error.safe_message,
        }
    # Report the started state with the lifecycle snapshot
    return {
        "start_authorized": True,
        "start_state": "STARTED",
        "start_error": None,
        "server": snapshot.to_dict(),
    }
