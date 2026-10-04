"""Read-only verification of the external Steam Workshop cache."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ..domain.workshop import CacheProof, WorkshopObservation
from .tree_metadata import TreeMetadataError, has_alternate_stream
from .workshop_manifest import (  # noqa: F401 - re-exported for existing importers
    APP_ID,
    MANIFEST_NAME,
    CacheVerificationError,
    manifest_id,
    manifest_record,
    observe_items,
    steam_reports_complete,
)

# Each file is hashed in chunks of this size, so peak memory per file is one chunk
HASH_CHUNK_BYTES = 1024 * 1024


class HashingCancelled(RuntimeError):
    """Raised when the cancellation probe fires between two files; the hash is abandoned."""


def _has_alternate_stream(path: Path) -> bool:
    """Return True when the file carries an alternate data stream."""
    try:
        return has_alternate_stream(path)
    except TreeMetadataError as error:
        raise CacheVerificationError(str(error)) from error


def _is_reparse(path: Path) -> bool:
    """Return True when the path is a reparse point, such as a symlink."""
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    # Fall back to the raw attribute value when the constant is unavailable
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


class WorkshopCacheVerifier:
    """Verify and observe the external Steam Workshop cache without writing."""
    def __init__(self, application_content_root: Path) -> None:
        """Store the application workshop content root to verify."""
        self._root = application_content_root

    @property
    def manifest_path(self) -> Path:
        """Return the Workshop manifest kept above the content root."""
        # The manifest lives two folders above the application content folder
        return self._root.parent.parent / MANIFEST_NAME

    @property
    def content_root(self) -> Path:
        """Return the workshop content root under verification."""
        return self._root

    def verify(
        self, workshop_id: str, cancellation_probe: Callable[[], bool] | None = None,
    ) -> CacheProof:
        """Verify one workshop item and return its cache proof.

        The optional probe is asked before each file; when it returns True the
        hash is abandoned with HashingCancelled.
        """
        # Reject non-numeric identifiers before they reach the filesystem
        if not workshop_id.isdecimal():
            raise CacheVerificationError("Workshop item identifier is invalid")
        root = self._validated_root()
        item = root / workshop_id
        # Require a safe item directory inside the validated root
        if (not item.is_dir() or item.is_symlink() or _is_reparse(item)
                or _has_alternate_stream(item)):
            raise CacheVerificationError("Workshop item directory is missing or unsafe")
        manifest = self._validated_manifest()
        first_record = self._manifest_record(manifest, workshop_id)
        # Scan the item content while holding the first manifest reading
        inventory = self._inventory(item, root, cancellation_probe)
        # Re-read the manifest so a mid-scan change is caught
        second_record = self._manifest_record(manifest, workshop_id)
        if first_record != second_record:
            raise CacheVerificationError("Workshop cache changed during verification")
        digest, metadata_digest, count, total = inventory
        # Refuse empty or incomplete items so a proof always covers content
        if count == 0 or total == 0:
            raise CacheVerificationError("Workshop item cache is empty or incomplete")
        return CacheProof(
            workshop_id=workshop_id,
            manifest_record_digest=hashlib.sha256(first_record).hexdigest(),
            content_inventory_digest=digest,
            regular_file_count=count,
            total_regular_bytes=total,
            verified_at=datetime.now(UTC).isoformat(timespec="milliseconds"),
            installed_manifest_id=manifest_id(first_record),
            metadata_inventory_digest=metadata_digest,
        )

    def observe(self, workshop_ids: tuple[str, ...]) -> tuple[WorkshopObservation, ...]:
        """Return the current observations for the requested Workshop items."""
        if any(not workshop_id.isdecimal() for workshop_id in workshop_ids):
            raise CacheVerificationError("Workshop item identifier is invalid")
        self._validated_root()
        # Parsing lives in the manifest module; a missing record group fails closed
        return observe_items(self._validated_manifest(), workshop_ids)

    def steam_reports_complete(self) -> bool:
        """Return whether Steam reports no pending update or download for the application."""
        try:
            self._validated_root()
            return steam_reports_complete(self._validated_manifest())
        except CacheVerificationError:
            return False

    def source_directory_exists(self, workshop_id: str) -> bool:
        """Return whether the item has a content directory below the cache root."""
        try:
            return workshop_id.isdecimal() and (self._root / workshop_id).is_dir()
        except OSError:
            return False

    def root_identity(self) -> str:
        """Derive a stable identity for the validated content root."""
        try:
            canonical = self._validated_root()
            state = canonical.stat()
        except OSError as error:
            raise CacheVerificationError("Workshop cache root is missing or unsafe") from error
        # Bind identity to path, volume, and creation time so a replaced folder is detected
        payload = json.dumps({
            "domain": "workshop-cache-root/v1", "path": str(canonical).casefold(),
            "device": state.st_dev, "file_identity": state.st_ino,
            "created_ns": state.st_ctime_ns,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def manifest_record_digest(self, workshop_id: str) -> str:
        """Return the digest of one item's raw manifest record."""
        if not workshop_id.isdecimal():
            raise CacheVerificationError("Workshop item identifier is invalid")
        self._validated_root()
        return hashlib.sha256(
            self._manifest_record(self._validated_manifest(), workshop_id)
        ).hexdigest()

    def _validated_root(self) -> Path:
        """Resolve the content root and reject unsafe or misnamed locations."""
        try:
            # Prove no link or reparse point sits above the root before resolving
            self._require_safe_chain(self._root.absolute())
            root = self._root.resolve(strict=True)
        except OSError as error:
            raise CacheVerificationError("Workshop cache root is missing or unsafe") from error
        # The folder chain must identify a DayZ workshop content root
        if tuple(part.casefold() for part in root.parts[-4:]) != (
            "steamapps", "workshop", "content", APP_ID,
        ):
            raise CacheVerificationError("Workshop cache role is invalid")
        return root

    def _validated_manifest(self) -> Path:
        """Return the manifest path after verifying it is a plain file."""
        manifest = self.manifest_path
        if not manifest.is_file() or manifest.is_symlink() or _is_reparse(manifest):
            raise CacheVerificationError("Steam Workshop manifest is missing or unsafe")
        if _has_alternate_stream(manifest):
            raise CacheVerificationError("Steam Workshop manifest has an alternate stream")
        return manifest

    @staticmethod
    def _manifest_record(path: Path, workshop_id: str) -> bytes:
        """Return the canonical JSON bytes of one installed item record."""
        return manifest_record(path, workshop_id)

    @staticmethod
    def _require_safe_chain(path: Path) -> None:
        """Reject symlinks and reparse points on any path component."""
        current = path
        while True:
            # Check every existing component up to the drive root
            if current.exists() and (current.is_symlink() or _is_reparse(current)):
                raise CacheVerificationError("Workshop root contains a link or reparse point")
            if current.parent == current:
                return
            current = current.parent

    @staticmethod
    def _inventory(
        item: Path, root: Path, cancellation_probe: Callable[[], bool] | None = None,
    ) -> tuple[str, str, int, int]:
        """Digest the item's files and metadata and count regular bytes."""
        entries: list[tuple[object, ...]] = []
        metadata_entries: list[tuple[object, ...]] = []
        identities: set[str] = set()
        count = 0
        total = 0
        for directory, names, files in os.walk(item, followlinks=False):
            base = Path(directory)
            # Refuse linked or streamed directories before descending further
            if base.is_symlink() or _is_reparse(base) or _has_alternate_stream(base):
                raise CacheVerificationError("Workshop item contains an unsafe directory")
            # Every subdirectory must be a real directory with a unique name
            for name in names:
                child = base / name
                if (":" in name or child.is_symlink() or _is_reparse(child)
                        or _has_alternate_stream(child)):
                    raise CacheVerificationError("Workshop item contains an unsafe directory")
                relative = unicodedata.normalize("NFC", child.relative_to(item).as_posix())
                identity = relative.casefold()
                if identity in identities:
                    raise CacheVerificationError("Workshop item has a path identity collision")
                identities.add(identity)
                entries.append(("D", relative))
                metadata_entries.append(("D", relative))
            # Every file must be a regular file below the configured root
            for name in files:
                # Stop only between files, never inside one
                if cancellation_probe is not None and cancellation_probe():
                    raise HashingCancelled("Workshop item hashing was cancelled")
                child = base / name
                if (":" in name or child.is_symlink() or _is_reparse(child)
                        or not child.is_file() or _has_alternate_stream(child)):
                    raise CacheVerificationError("Workshop item contains an unsafe file")
                resolved = child.resolve(strict=True)
                try:
                    relative = resolved.relative_to(root).as_posix()
                except ValueError as error:
                    raise CacheVerificationError("Workshop item escapes its configured root") from error
                normalized = unicodedata.normalize(
                    "NFC", resolved.relative_to(item).as_posix(),
                )
                identity = normalized.casefold()
                if identity in identities:
                    raise CacheVerificationError("Workshop item has a path identity collision")
                identities.add(identity)
                # Stream the bytes through one running hash; the size is what was read
                digest = hashlib.sha256()
                size = 0
                with child.open("rb") as stream:
                    while chunk := stream.read(HASH_CHUNK_BYTES):
                        digest.update(chunk)
                        size += len(chunk)
                entries.append(("F", normalized, size, digest.hexdigest()))
                metadata_entries.append(("F", normalized, size, child.stat().st_mtime_ns))
                count += 1
                total += size
        # Sort deterministically so both digests are stable across runs
        entries.sort(key=lambda value: (str(value[1]).casefold(), str(value[1]), value[0]))
        metadata_entries.sort(
            key=lambda value: (str(value[1]).casefold(), str(value[1]), value[0]),
        )
        payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
        metadata_payload = json.dumps(
            metadata_entries, ensure_ascii=False, separators=(",", ":"),
        )
        return (
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            hashlib.sha256(metadata_payload.encode("utf-8")).hexdigest(),
            count,
            total,
        )
