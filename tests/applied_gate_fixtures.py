"""Shared fixture: an update gate whose proofs say that every mod is already applied."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import current  # noqa: E402
from dayz_serverman.application.content_proofs import ContentProofResolver  # noqa: E402
from dayz_serverman.application.publication_identity import (  # noqa: E402
    authentication_identity_digest,
)
from dayz_serverman.domain.lifecycle import LifecycleSnapshot, ServerState  # noqa: E402
from dayz_serverman.domain.workshop import AuthenticationMode, derive_required_items  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402


def applied_state_gate(case, store, start_requested: bool, require_applied: bool = True,
                       legacy=None) -> str:
    """Return a gate with stored proofs for a publication fixture whose mods were applied once.

    `case` is a PublicationApplicationFixture; `store` holds the records of the
    earlier publication, so every proof is APPLIED_STATE with a target fingerprint.
    With `require_applied` false the gate carries whatever the resolver returns.
    `legacy` is the optional legacy repository that the resolver may read.
    """
    items = derive_required_items(case.profile)
    proofs = ContentProofResolver(store, legacy).resolve(
        case.settings.load(), case.profile, items,
        current(*(item.workshop_id for item in items)), WorkshopCacheVerifier(case.cache),
    )
    if require_applied:
        case.assertEqual({proof.verification_kind for proof in proofs.values()}, {"APPLIED_STATE"})
    result = {
        "profile_id": "main", "profile_revision": case.profile.revision,
        "semantic_profile_digest": case.profile.semantic_digest,
        "settings_revision": case.settings_revision,
        "authentication_identity_digest": authentication_identity_digest(
            AuthenticationMode.ACCOUNT, "Operator"),
        "download_state": "VERIFIED",
        "items": [{"item": item.to_dict(), "outcome": "VERIFIED_CURRENT",
                   "cache_proof": proofs[item.workshop_id].to_dict(), "error_code": None}
                  for item in items],
        "publication_state": "PENDING_PHASE_6_2", "start_authorized": False,
        "start_requested": start_requested,
        "start_error": "PUBLICATION_REQUIRED" if start_requested else None,
        "process_id": 77, "steamcmd_exit_code": 0, "steamcmd_summary": None,
    }
    record = case.operations.submit("UPDATE_WORKSHOP_ITEMS", lambda _context: result)
    return case.wait(record.operation_id).operation_id


class CountingLifecycle:
    """Lifecycle double that counts start attempts and reports a settable server state."""

    def __init__(self, state: ServerState = ServerState.STOPPED) -> None:
        """Start with zero recorded start calls and the given state."""
        self.calls = 0
        self.state = state

    def status(self) -> LifecycleSnapshot:
        """Return a snapshot of the configured state."""
        return LifecycleSnapshot(self.state)

    def stop(self, _settings_revision: int) -> LifecycleSnapshot:
        """Stop the pretended server: the state becomes STOPPED."""
        self.state = ServerState.STOPPED
        return self.status()

    def start(self, _profile_id: str, _profile_revision: int, _settings_revision: int):
        """Record the start attempt and return a managed running snapshot."""
        self.calls += 1
        self.state = ServerState.RUNNING_MANAGED
        return LifecycleSnapshot(ServerState.RUNNING_MANAGED, process_id=444)
