"""Resolve stored content proofs for Workshop items, with the legacy file as fallback."""

from __future__ import annotations

from pathlib import Path

from ..adapters.windows.publication_paths import (
    dayz_root_identity,
    safe_dayz_root,
    safe_target,
)
from ..domain.content_proofs import (
    ProofDocument,
    SourceProofRecord,
    target_directory_key,
)
from ..domain.workshop import SUCCESS_OUTCOMES, CacheProof, WorkshopObservation
from ..repositories.applied_mod_state import AppliedModStateRepository
from ..repositories.content_proofs import ContentProofStore
from ..repositories.tree_metadata import tree_metadata_digest
from ..repositories.workshop_cache import WorkshopCacheVerifier
from .content_proof_records import PROOF_ERRORS, ContentProofRecorder, source_record


class ContentProofResolver:
    """Decide per item whether a stored proof is valid now; a miss means a full hash."""

    def __init__(
        self, store: ContentProofStore, legacy: AppliedModStateRepository | None = None,
    ) -> None:
        """Store the proof store and the optional read-only legacy file."""
        self._store = store
        self._legacy = legacy
        self._recorder = ContentProofRecorder(store)

    def record_full_hash(self, verifier: WorkshopCacheVerifier, proof: CacheProof) -> None:
        """Record the source proof of an item that was fully hashed in this operation."""
        self._recorder.record_full_hash(verifier, proof)

    def resolve(self, settings, profile, items, outcomes,
                verifier: WorkshopCacheVerifier) -> dict[str, CacheProof]:
        """Return a stored proof for every successful item that has a valid one."""
        wanted = [item for item in items if outcomes[item.workshop_id] in SUCCESS_OUTCOMES]
        if not wanted:
            return {}
        # Capture the cache identity and the current observations once
        try:
            cache_identity = verifier.root_identity()
            after = {value.workshop_id: value for value in verifier.observe(
                tuple(item.workshop_id for item in wanted),
            )}
        except PROOF_ERRORS:
            return {}
        document = self._store.load()
        root, root_identity = _dayz_root(settings)
        # Map Workshop source ids to profile mod directories for target resolution
        directories = {
            mod.source.workshop_id: mod.directory for mod in profile.values.mods
            if mod.source.kind == "workshop"
        }
        resolved: dict[str, CacheProof] = {}
        seeded_sources: dict[str, SourceProofRecord] = {}
        for item in wanted:
            workshop_id = item.workshop_id
            observation = after[workshop_id]
            # An item without an installed record has nothing to prove
            if observation.installed_manifest_id is None:
                continue
            try:
                # Any read, path or fingerprint error means "no stored proof" for this item
                manifest_digest = verifier.manifest_record_digest(workshop_id)
                source = verifier.content_root / workshop_id
                directory = directories[workshop_id]
                proof = _stored_proof(
                    document, cache_identity, observation, manifest_digest, source,
                    root, root_identity, directory,
                )
                if ((proof is None or proof.target_metadata_digest is None)
                        and self._legacy is not None and root is not None):
                    # The legacy file keeps its profile-bound rule and serves this resolution only
                    legacy = self._legacy.find(
                        profile_id=profile.values.profile_id,
                        semantic_profile_digest=profile.semantic_digest,
                        dayz_root_identity=root_identity, workshop_id=workshop_id,
                        target_relative=directory, source=source,
                        target=safe_target(root, directory),
                        manifest_record_digest=manifest_digest,
                        installed_manifest_id=observation.installed_manifest_id,
                    )
                    # A hit seeds the source record only. No hash of this version stands behind
                    # the legacy target fingerprint, so it never becomes a store target record
                    seed = (source_record(cache_identity, observation, legacy)
                            if legacy is not None and proof is None else None)
                    if seed is not None:
                        seeded_sources[workshop_id] = seed
                    if legacy is not None and (proof is None or (
                            proof.content_inventory_digest == legacy.content_inventory_digest)):
                        proof = legacy
            except PROOF_ERRORS:
                proof = None
            if proof is not None:
                resolved[workshop_id] = proof
        if seeded_sources:
            self._store.record(sources=seeded_sources)
        return resolved


def _dayz_root(settings) -> tuple[Path | None, str | None]:
    """Return the safe DayZ root and its identity, or (None, None) when unusable."""
    if getattr(settings, "dayz_root", None) is None:
        return None, None
    try:
        root = safe_dayz_root(Path(settings.dayz_root))
        return root, dayz_root_identity(root)
    except PROOF_ERRORS:
        return None, None


def _stored_proof(
    document: ProofDocument, cache_identity: str, observation: WorkshopObservation,
    manifest_digest: str, source: Path, root: Path | None, root_identity: str | None,
    directory: str,
) -> CacheProof | None:
    """Return the proof built from valid store records, or None without a valid source."""
    record = document.sources.get(observation.workshop_id)
    # Source proof: same cache root, same installed manifest id, same fingerprint
    if (record is None or observation.installed_manifest_id is None
            or record.cache_root_identity != cache_identity
            or record.installed_manifest_id != observation.installed_manifest_id
            or tree_metadata_digest(source) != record.metadata_inventory_digest):
        return None
    target_digest = _valid_target(document, record, observation.workshop_id,
                                  root, root_identity, directory)
    # The proof always carries the current manifest-record digest
    return CacheProof(
        observation.workshop_id, manifest_digest, record.content_inventory_digest,
        record.regular_file_count, record.total_regular_bytes, record.verified_at,
        record.installed_manifest_id, record.metadata_inventory_digest,
        "APPLIED_STATE" if target_digest is not None else "STORED_SOURCE", target_digest,
    )


def _valid_target(
    document: ProofDocument, source: SourceProofRecord, workshop_id: str,
    root: Path | None, root_identity: str | None, directory: str,
) -> str | None:
    """Return the target fingerprint when the target proof is valid, else None."""
    if root is None or root_identity is None:
        return None
    record = document.targets.get((root_identity, target_directory_key(directory)))
    # Target proof: same item, manifest id and content digest as the valid source proof
    if (record is None or record.workshop_id != workshop_id
            or record.installed_manifest_id != source.installed_manifest_id
            or record.content_inventory_digest != source.content_inventory_digest):
        return None
    try:
        # The current target fingerprint must equal the recorded one
        if tree_metadata_digest(safe_target(root, directory)) != record.target_metadata_digest:
            return None
    except PROOF_ERRORS:
        return None
    return record.target_metadata_digest
