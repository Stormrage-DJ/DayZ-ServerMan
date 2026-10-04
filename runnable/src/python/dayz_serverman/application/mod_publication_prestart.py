"""Pre-start rule D9: a stored fingerprint may stand in for the full hash of an unchanged mod folder."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ..domain.content_proofs import TargetProofRecord, target_directory_key
from ..domain.mod_publication import (
    GroupState,
    ManagedModSource,
    PublicationGroup,
    PublicationIntent,
    TargetRole,
)
from ..observability.structured_log import StructuredLogger
from ..repositories.content_proofs import ContentProofStore
from ..repositories.tree_metadata import tree_metadata_digest
from .content_proof_records import PROOF_ERRORS


class PrestartCheck:
    """Rule D9 for one pre-start check, and the target records that its full hashes justify.

    The rule authorizes nothing by itself: a miss of any kind means that the
    caller hashes the group in full. Calling the object asks the rule.
    """

    def __init__(self, store: ContentProofStore | None, intent: PublicationIntent | None) -> None:
        """Read the store once; without a store or an intent every group is a miss."""
        self._store = store
        self._root = intent.dayz_root_identity if intent is not None else ""
        try:
            self._records = store.load().targets if store is not None else {}
        except PROOF_ERRORS:
            # An unreadable store is a miss for every group
            self._records = {}
        # The reviewed intent names the managed source of each mod target
        self._sources: dict[str, ManagedModSource] = {
            source.target_relative: source
            for source in (intent.managed_sources if intent is not None else ())
        }
        self._proven: dict[tuple[str, str], TargetProofRecord] = {}

    def __call__(self, group: PublicationGroup, target: Path) -> bool:
        """Return True only when every condition of rule D9 holds for the group."""
        source = self._unchanged_source(group)
        record = self._records.get((self._root, target_directory_key(group.target_relative)))
        if source is None or record is None:
            return False
        proof = source.cache_proof
        # The record must come from a write point that hashed the tree, and it must
        # speak for this item, this manifest id and this content
        if (not record.justified
                or record.workshop_id != source.workshop_id
                or record.installed_manifest_id != proof.installed_manifest_id
                or record.content_inventory_digest != group.output_digest
                or proof.target_metadata_digest is None):
            return False
        current = self.fingerprint(target)
        # The current fingerprint must equal the recorded and the reviewed one
        return (current is not None and current == record.target_metadata_digest
                and current == proof.target_metadata_digest)

    @staticmethod
    def fingerprint(target: Path) -> str | None:
        """Return the fingerprint of the tree; a path or fingerprint error is a miss, never a failure."""
        try:
            return tree_metadata_digest(target)
        except PROOF_ERRORS:
            return None

    def hashed(self, group: PublicationGroup, target: Path, before: str | None) -> None:
        """Note the proof of an unchanged mod folder whose full hash has just matched.

        `before` is the fingerprint measured before that hash. The record is
        kept only when the fingerprint is the same after the hash.
        """
        source = self._unchanged_source(group)
        if source is None or before is None or self.fingerprint(target) != before:
            return
        self._proven[(self._root, group.target_relative)] = TargetProofRecord(
            source.workshop_id, source.cache_proof.installed_manifest_id, group.output_digest,
            before, datetime.now(UTC).isoformat(timespec="milliseconds"), "PRESTART",
        )

    def record(self) -> None:
        """Write the noted records after a passed check; never raise."""
        if self._store is None or not self._proven:
            return
        try:
            self._store.record(targets=self._proven)
        except PROOF_ERRORS:
            return

    def _unchanged_source(self, group: PublicationGroup) -> ManagedModSource | None:
        """Return the managed source of an unchanged mod group that has a manifest id, else None."""
        # The keys directory and a group copied in this run are always hashed and never noted
        if (group.role != TargetRole.MANAGED_MOD_DIRECTORY
                or group.state != GroupState.UNCHANGED_VERIFIED):
            return None
        source = self._sources.get(group.target_relative)
        if source is None or source.cache_proof.installed_manifest_id is None:
            return None
        return source


# Check of a publication without a wired store: every group is hashed and nothing is recorded
UNCHECKED = PrestartCheck(None, None)


class PrestartFingerprints:
    """Build the pre-start check of rule D9 over the proof store, and log its counts."""

    def __init__(self, store: ContentProofStore, logger: StructuredLogger | None = None) -> None:
        """Store the proof store and the optional logger."""
        self._store = store
        self._logger = logger

    def rule(self, intent: PublicationIntent) -> PrestartCheck:
        """Return the check of one pre-start run; the store is read once."""
        return PrestartCheck(self._store, intent)

    def log(self, counts: Mapping[str, int], operation_id: str) -> None:
        """Write the one log record of a finished pre-start check; never raise."""
        if self._logger is None:
            return
        try:
            self._logger.emit(
                "mod_publication.prestart_check", operation_id=operation_id,
                fields={"hashed": counts["hashed"],
                        "fingerprint_accepted": counts["fingerprint_accepted"]},
            )
        except (OSError, TypeError, ValueError, KeyError):
            return
