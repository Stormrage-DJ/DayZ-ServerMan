"""Non-authoritative persisted evidence for fast unchanged-mod checks."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from ..adapters.windows.shared_files import read_text_shared, replace_file
from ..domain.content_proofs import target_directory_key
from ..domain.mod_publication import PublicationIntent
from ..domain.workshop import CacheProof
from .tree_metadata import TreeMetadataError, tree_metadata_digest


class AppliedModStateRepository:
    """Persist evidence that published mods remain unchanged since verification."""

    def __init__(self, path: Path) -> None:
        """Store the JSON state file path."""
        self._path = path

    def find(
        self, *, profile_id: str, semantic_profile_digest: str,
        dayz_root_identity: str, workshop_id: str, target_relative: str,
        source: Path, target: Path, manifest_record_digest: str,
        installed_manifest_id: str,
    ) -> CacheProof | None:
        """Return the stored cache proof when it matches the current request."""
        try:
            # Look up the stored record for this profile and workshop mod
            profile = self._load()["profiles"][profile_id]
            record = profile["mods"][workshop_id]
            # Compare the recorded identity fields with the current request
            expected = (
                semantic_profile_digest, dayz_root_identity, target_relative,
                manifest_record_digest, installed_manifest_id,
            )
            actual = tuple(record[name] for name in (
                "semantic_profile_digest", "dayz_root_identity", "target_relative",
                "manifest_record_digest", "installed_manifest_id",
            ))
            if actual != expected:
                return None
            # Reject the stored proof when either tree diverges from its metadata
            if (tree_metadata_digest(source) != record["source_metadata_digest"]
                    or tree_metadata_digest(target) != record["target_metadata_digest"]):
                return None
            # Return the stored proof for the caller to reuse as evidence
            return CacheProof(
                workshop_id, manifest_record_digest, record["content_inventory_digest"],
                record["regular_file_count"], record["total_regular_bytes"],
                record["verified_at"], installed_manifest_id,
                record["source_metadata_digest"], "APPLIED_STATE",
                record["target_metadata_digest"],
            )
        except (KeyError, TypeError, ValueError, OSError, TreeMetadataError):
            return None

    def target_records(self) -> frozenset[tuple[str, str, str, str]]:
        """Return (root identity, target directory key, Workshop id, manifest id) of every record.

        The profile binding is left out on purpose: the result serves display only
        and authorizes nothing. An unreadable file yields an empty set.
        """
        records: set[tuple[str, str, str, str]] = set()
        try:
            profiles = self._load()["profiles"]
        except (OSError, ValueError, TypeError, KeyError):
            return frozenset()
        # Walk every profile section and keep the well-formed records
        for profile in profiles.values():
            mods = profile.get("mods") if isinstance(profile, dict) else None
            for workshop_id, record in (mods.items() if isinstance(mods, dict) else ()):
                if not isinstance(record, dict):
                    continue
                values = tuple(record.get(name) for name in (
                    "dayz_root_identity", "target_relative", "installed_manifest_id",
                ))
                if all(isinstance(value, str) for value in values):
                    # The directory key ignores letter case only, as in the store
                    records.add(
                        (values[0], target_directory_key(values[1]), workshop_id, values[2]),
                    )
        return frozenset(records)

    def record(self, intent: PublicationIntent, dayz_root: Path) -> None:
        """Persist fresh verification evidence for every mod in a publication intent."""
        # Start from the stored document, falling back to a fresh one
        try:
            raw = self._load()
        except (OSError, ValueError):
            raw = {"schema_version": 1, "profiles": {}}
        # Capture one evidence record per managed source
        mods: dict[str, object] = {}
        for source in intent.managed_sources:
            source_path = Path(source.source_path)
            target = dayz_root / Path(source.target_relative)
            proof = source.cache_proof
            mods[source.workshop_id] = {
                "semantic_profile_digest": intent.semantic_profile_digest,
                "dayz_root_identity": intent.dayz_root_identity,
                "target_relative": source.target_relative,
                "manifest_record_digest": proof.manifest_record_digest,
                "installed_manifest_id": proof.installed_manifest_id,
                "content_inventory_digest": proof.content_inventory_digest,
                "regular_file_count": proof.regular_file_count,
                "total_regular_bytes": proof.total_regular_bytes,
                "verified_at": proof.verified_at,
                "source_metadata_digest": tree_metadata_digest(source_path),
                "target_metadata_digest": tree_metadata_digest(target),
            }
        # Replace the stored section for this profile with the new evidence
        raw["profiles"][intent.profile_id] = {"mods": mods}
        self._write(raw)

    def delete_profile(self, profile_id: str) -> None:
        """Remove cached evidence that belongs only to a deleted profile."""
        try:
            raw = self._load()
        except (OSError, ValueError) as error:
            raise OSError("applied mod state cannot be cleaned safely") from error
        profiles = raw["profiles"]
        if profile_id in profiles:
            del profiles[profile_id]
            self._write(raw)

    def _load(self) -> dict[str, object]:
        """Return the stored state document, or an empty document when none exists."""
        # Return an empty document before the first evidence write
        if not self._path.exists():
            return {"schema_version": 1, "profiles": {}}
        # Parse the persisted JSON document
        raw = json.loads(read_text_shared(self._path, encoding="utf-8"))
        # Reject documents that do not match the expected shape
        if (not isinstance(raw, dict) or raw.get("schema_version") != 1
                or not isinstance(raw.get("profiles"), dict)):
            raise ValueError("applied mod state is invalid")
        return raw

    def _write(self, value: dict[str, object]) -> None:
        """Write the state document atomically through a temporary file."""
        # Ensure the target directory exists before publishing
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Stage the document beside the target so the swap stays on one volume
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            # Serialize, flush, and fsync the staged bytes before the swap
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Swap the staged file into place
            replace_file(temporary, self._path)
        finally:
            # Remove the staged file when any step fails
            if temporary.exists():
                temporary.unlink()
