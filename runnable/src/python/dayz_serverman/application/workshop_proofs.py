"""Last-boundary Workshop cache proof construction."""

from __future__ import annotations

from ..domain.workshop import (
    CacheProof,
    ItemOutcome,
    ItemResult,
    RequiredWorkshopItem,
    WorkshopObservation,
)
from ..repositories.workshop_cache import CacheVerificationError, WorkshopCacheVerifier
from .steamcmd_results import error_code


def verify_result(
    item: RequiredWorkshopItem,
    outcome: ItemOutcome,
    before: WorkshopObservation,
    verifier: WorkshopCacheVerifier,
    cached_proof: CacheProof | None = None,
) -> ItemResult:
    """Resolve the final verified result for one item, reusing a cached proof."""
    # Failed or cancelled outcomes never build cache proofs
    if outcome not in (
        ItemOutcome.DOWNLOADED_VERIFIED,
        ItemOutcome.UPDATED_VERIFIED,
        ItemOutcome.VERIFIED_CURRENT,
    ):
        return ItemResult(item, outcome, error_code=error_code(outcome))
    # Prefer the supplied cached proof to avoid re-reading the cache
    proof = cached_proof
    if proof is None:
        # Otherwise verify the installed cache content directly
        try:
            proof = verifier.verify(item.workshop_id)
        except (CacheVerificationError, OSError):
            # A hash that cannot read the cache is a failed verification, never a crash
            return ItemResult(item, ItemOutcome.UNKNOWN_FAILED,
                              error_code="CACHE_VERIFICATION_FAILED")
    # A proof without a manifest id cannot confirm the installed build
    if proof.installed_manifest_id is None:
        return ItemResult(item, ItemOutcome.UNKNOWN_FAILED,
                          error_code="CACHE_MANIFEST_ID_MISSING")
    # Compare before and after manifests to name the terminal outcome
    if not before.installed:
        verified_outcome = ItemOutcome.DOWNLOADED_VERIFIED
    elif before.installed_manifest_id == proof.installed_manifest_id:
        verified_outcome = ItemOutcome.VERIFIED_CURRENT
    else:
        verified_outcome = ItemOutcome.UPDATED_VERIFIED
    return ItemResult(item, verified_outcome, proof=proof)
