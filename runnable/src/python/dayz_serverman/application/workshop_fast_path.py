"""Resolve lightweight proofs for previously applied, unchanged Workshop mods."""

from __future__ import annotations

from pathlib import Path

from ..adapters.windows.publication_paths import (
    PublicationPathError,
    dayz_root_identity,
    safe_dayz_root,
    safe_target,
)
from ..domain.workshop import ItemOutcome
from ..repositories.applied_mod_state import AppliedModStateRepository
from ..repositories.workshop_cache import CacheVerificationError, WorkshopCacheVerifier


def find_cached_proofs(settings, profile, items, outcomes,
                       verifier: WorkshopCacheVerifier,
                       state: AppliedModStateRepository | None):
    """Return applied-state proofs for items already verified current."""
    # The fast path needs both an applied-state store and a DayZ root
    if state is None or settings.dayz_root is None:
        return {}
    # Capture root identity and current observations for stable lookups
    try:
        root = safe_dayz_root(Path(settings.dayz_root))
        root_identity = dayz_root_identity(root)
        after = {value.workshop_id: value for value in verifier.observe(
            tuple(item.workshop_id for item in items),
        )}
    except (OSError, CacheVerificationError, PublicationPathError):
        return {}
    # Map Workshop source ids to profile mods for target resolution
    mods = {
        mod.source.workshop_id: mod for mod in profile.values.mods
        if mod.source.kind == "workshop"
    }
    cached = {}
    # Resolve a reusable proof for each verified-current item
    for item in items:
        observation = after[item.workshop_id]
        if (outcomes[item.workshop_id] != ItemOutcome.VERIFIED_CURRENT
                or observation.installed_manifest_id is None):
            continue
        try:
            manifest_digest = verifier.manifest_record_digest(item.workshop_id)
            mod = mods[item.workshop_id]
            proof = state.find(
                profile_id=profile.values.profile_id,
                semantic_profile_digest=profile.semantic_digest,
                dayz_root_identity=root_identity,
                workshop_id=item.workshop_id,
                target_relative=mod.directory,
                source=verifier.content_root / item.workshop_id,
                target=safe_target(root, mod.directory),
                manifest_record_digest=manifest_digest,
                installed_manifest_id=observation.installed_manifest_id,
            )
        except (KeyError, OSError, CacheVerificationError, PublicationPathError):
            # A failed lookup simply disables the fast path for that item
            proof = None
        if proof is not None:
            cached[item.workshop_id] = proof
    return cached
