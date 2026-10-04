"""Write points of the content proof store: after a full hash and after a publication.

Rule of every write: a stored fingerprint is either the one that a full hash
of this operation produced, or one that equals an earlier proven fingerprint.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ..adapters.windows.publication_paths import (
    PublicationPathError,
    dayz_root_identity,
    safe_dayz_root,
    safe_target,
)
from ..domain.content_proofs import SourceProofRecord, TargetProofRecord
from ..domain.mod_publication import (
    GroupState,
    ManagedModSource,
    PublicationIntent,
    PublicationJournal,
    TargetRole,
)
from ..domain.workshop import CacheProof, WorkshopObservation
from ..repositories.content_proofs import ContentProofStore
from ..repositories.mod_publication_inventory import PublicationInventoryError, inventory_tree
from ..repositories.tree_metadata import TreeMetadataError, tree_metadata_digest
from ..repositories.workshop_cache import CacheVerificationError, WorkshopCacheVerifier

# Errors that make one tree or the cache unusable as a proof subject
PROOF_ERRORS = (OSError, ValueError, TypeError, KeyError, CacheVerificationError,
                TreeMetadataError, PublicationPathError)


def source_record(
    cache_root_identity: str, observation: WorkshopObservation, proof: CacheProof,
) -> SourceProofRecord | None:
    """Return the source record of a proof, or None when the manifest moved on."""
    # A proof only speaks for the manifest id that is installed now
    if (observation.installed_manifest_id is None
            or observation.installed_manifest_id != proof.installed_manifest_id
            or observation.installed_time_updated is None
            or proof.metadata_inventory_digest is None):
        return None
    return SourceProofRecord(
        cache_root_identity, proof.installed_manifest_id, observation.installed_time_updated,
        proof.content_inventory_digest, proof.regular_file_count, proof.total_regular_bytes,
        proof.metadata_inventory_digest, proof.verified_at,
    )


class ContentProofRecorder:
    """Record new proofs; every method runs on the lane and never raises."""

    def __init__(self, store: ContentProofStore) -> None:
        """Store the proof store that receives the records."""
        self._store = store

    def record_full_hash(self, verifier: WorkshopCacheVerifier, proof: CacheProof) -> None:
        """Record the source proof of an item that was fully hashed in this operation."""
        if proof.verification_kind != "FULL_CONTENT":
            return
        try:
            observation = verifier.observe((proof.workshop_id,))[0]
            record = source_record(verifier.root_identity(), observation, proof)
        except PROOF_ERRORS:
            return
        if record is not None:
            self._store.record(sources={proof.workshop_id: record})

    def record_publication(
        self, intent: PublicationIntent, dayz_root: Path, cache_root: Path,
        journal: PublicationJournal | None = None,
        hashed_fingerprints: Mapping[str, str | None] | None = None,
    ) -> None:
        """Record source and target proofs of every managed source of a committed publication.

        A target record is written only with a fingerprint that a full hash of this
        run justifies, and it names that hash as its basis (see `_proven_fingerprint`).
        An unchanged target keeps its stored record untouched. A target whose tree is
        no longer the proven one loses its stored record, so the next resolution falls
        back to a full hash. `journal` names the groups that this run copied, and
        `hashed_fingerprints` holds the fingerprint that the publication measured for
        each of them directly before its post-commit hash; without both, a group does
        not count as copied and the recorder hashes it itself.
        """
        try:
            # Bind the records to the cache and the server folder as they are now
            verifier = WorkshopCacheVerifier(cache_root)
            cache_identity = verifier.root_identity()
            observations = {value.workshop_id: value for value in verifier.observe(
                tuple(source.workshop_id for source in intent.managed_sources),
            )}
            root = safe_dayz_root(dayz_root)
            if dayz_root_identity(root) != intent.dayz_root_identity:
                return
        except PROOF_ERRORS:
            return
        # Mod folders that this run copied, with the fingerprint taken before their last full hash
        copied = {group.target_relative: (hashed_fingerprints or {}).get(group.target_relative)
                  for group in (journal.groups if journal else ())
                  if group.role == TargetRole.MANAGED_MOD_DIRECTORY
                  and group.state == GroupState.OUTPUT_VERIFIED}
        sources: dict[str, SourceProofRecord] = {}
        targets: dict[tuple[str, str], TargetProofRecord] = {}
        unproven: list[tuple[str, str]] = []
        for source in intent.managed_sources:
            proof = source.cache_proof
            key = (intent.dayz_root_identity, source.target_relative)
            try:
                # The source record repeats the fingerprint of the proof; it measures nothing
                record = source_record(cache_identity, observations[source.workshop_id], proof)
            except PROOF_ERRORS:
                continue
            if record is None:
                continue
            sources[source.workshop_id] = record
            try:
                proven = _proven_fingerprint(
                    source, safe_target(root, source.target_relative),
                    copied.get(source.target_relative))
            except PROOF_ERRORS:
                proven = None
            if proven is None:
                # No hash speaks for this tree now: drop what the store says about it
                unproven.append(key)
            elif proven[1] is not None:
                targets[key] = TargetProofRecord(
                    source.workshop_id, record.installed_manifest_id,
                    record.content_inventory_digest, proven[0], _utc_now(), proven[1],
                )
        if sources:
            self._store.record(sources=sources, targets=targets, remove_targets=unproven)


def _proven_fingerprint(
    source: ManagedModSource, target: Path, hashed_before: str | None,
) -> tuple[str, str | None] | None:
    """Return the fingerprint of the target tree and the basis of a new record, or None.

    Unchanged target: the fingerprint of the proof while the tree still has it, with
    no basis: this run hashed nothing, so the stored record stays as it is.
    Copied target: `hashed_before`, the fingerprint that the publication measured
    directly before its post-commit hash, only while the tree still has it. The
    hash that followed proved the content, so the pair brackets that hash.
    Any other target (found equal by the staging hash, or copied without a
    fingerprint): the fingerprint that did not move while the tree was hashed
    again here and found equal to the proven content.
    None: the tree is not the proven one.
    """
    measured = tree_metadata_digest(target)
    if source.target_current:
        # The tree must still be the one that the reviewed proof described
        return (measured, None) if measured == source.cache_proof.target_metadata_digest else None
    if hashed_before is not None:
        # A tree that changed after the post-commit hash is not the hashed one
        return (measured, "COPIED") if measured == hashed_before else None
    # No fingerprint was kept with an earlier hash of this tree: hash it once more
    try:
        digest = inventory_tree(target)
    except PublicationInventoryError:
        return None
    if digest != source.output_digest or tree_metadata_digest(target) != measured:
        return None
    return measured, "HASHED"


def _utc_now() -> str:
    """Return the current UTC time as a millisecond-precision ISO string."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")
