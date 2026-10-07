"""Atomic ZIP backup publication, discovery, and materialization."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import replace_file
from ..domain.backups import (
    BACKUP_ID, BACKUP_SCHEMA_VERSION, BackupManifest, BackupManifestError,
    ManifestEntry, RESTORE_COMPATIBLE, restore_compatibility,
)
from .backup_archives import (
    BackupArchiveError, FutureBackupArchive, materialize_archive, read_archive_manifest,
    verify_archive, write_archive,
)
from .backup_sources import (
    BackupSource, BackupSourceError, copy_verified, directory_sources, runtime_sources,
    safe_directory, single_source,
)
from .backup_verification import (
    BackupVerificationError, FutureBackupSchema, is_reparse, read_manifest,
    sha256_file, verify_directory,
)


from .backup_errors import BackupStorageError
from .backup_publication import _available_id, _verify_staging, _verify_zip, _write_manifest, _summary, _diagnostic

class BackupStorage:
    """Publish, discover, verify, and materialize ZIP backups for one manager."""

    def __init__(self, *, disk_usage: Callable[[Path], Any] = shutil.disk_usage,
                 phase_hook: Callable[[str], None] | None = None) -> None:
        """Store the disk-usage probe and the optional phase observer."""
        self._disk_usage = disk_usage
        # Default to a no-op observer so callers can skip phase reporting
        self._phase_hook = phase_hook or (lambda _phase: None)

    def source(self, dayz_root: Path, relative: str) -> BackupSource:
        """Return the verified source record for one payload file."""
        # Preserve the source error code in the storage error
        try:
            return single_source(dayz_root, relative)
        except BackupSourceError as error:
            raise BackupStorageError(error.code, str(error)) from error

    def runtime_sources(self, dayz_root: Path, relative: str) -> tuple[BackupSource, ...]:
        """Return verified source records for every file under the runtime profile tree."""
        # Preserve the source error code in the storage error
        try:
            return runtime_sources(dayz_root, relative)
        except BackupSourceError as error:
            raise BackupStorageError(error.code, str(error)) from error

    def directory_sources(
        self, dayz_root: Path, relative: str, entry_prefix: str,
    ) -> tuple[BackupSource, ...]:
        """Return verified sources for one contained recursive payload tree."""
        try:
            return directory_sources(dayz_root, relative, entry_prefix)
        except BackupSourceError as error:
            raise BackupStorageError(error.code, str(error)) from error

    def create(
        self, root: Path, backup_id: str, profile_id: str, profile_revision: int,
        settings_revision: int, created_at: str, semantic_profile_digest: str,
        runtime_profile: str, sources: Sequence[BackupSource],
        checkpoint: Callable[[str, int], None],
        *, reconstruction: dict | None = None, directories: tuple[str, ...] = (),
        validate_context: Callable[[], None] | None = None,
    ) -> BackupManifest:
        """Create, verify, and publish one backup archive."""
        destination = self._root(root)
        checkpoint("DISCOVER", 10)
        self._phase_hook("DISCOVER")
        # Require twice the payload size plus one MiB of headroom for staging
        required = (sum(item.size for item in sources) * 2) + 1_048_576
        if self._disk_usage(destination).free < required:
            raise BackupStorageError("INSUFFICIENT_SPACE", "The backup root does not have enough free space.")
        # Reserve a free identifier and private staging names
        selected_id = _available_id(destination, backup_id)
        final = destination / f"{selected_id}.zip"
        staging = destination / f".staging-{selected_id}-{uuid.uuid4().hex}"
        partial = destination / f".{selected_id}-{uuid.uuid4().hex}.partial"
        staging.mkdir()
        published = False
        try:
            for directory in directories:
                staging.joinpath(*directory.split("/")).mkdir(parents=True, exist_ok=True)
            # Copy every verified source into the staging tree
            for item in sources:
                try:
                    copy_verified(item, staging / item.entry_path)
                except BackupSourceError as error:
                    raise BackupStorageError(error.code, str(error)) from error
            checkpoint("STAGE", 35)
            self._phase_hook("STAGE")
            # Hash the staged files into manifest entries
            entries = tuple(
                ManifestEntry(item.entry_path, item.size, sha256_file(staging / item.entry_path))
                for item in sources
            )
            checkpoint("HASH", 55)
            self._phase_hook("HASH")
            # Sign the staging manifest and verify the staged tree
            manifest = BackupManifest(
                selected_id, profile_id, profile_revision, settings_revision, created_at, entries,
                semantic_profile_digest=semantic_profile_digest, runtime_profile=runtime_profile,
                schema_version=3 if reconstruction is not None else 2,
                reconstruction=reconstruction, directories=directories,
            ).signed()
            _write_manifest(staging / "manifest.json", manifest)
            checkpoint("WRITE_MANIFEST", 70)
            self._phase_hook("WRITE_MANIFEST")
            _verify_staging(staging, selected_id, manifest)
            # Build the archive and verify it before and after publication
            write_archive(staging, partial, manifest)
            _verify_zip(partial, selected_id, manifest, "staged")
            checkpoint("VERIFY", 85)
            self._phase_hook("VERIFY")
            checkpoint("PUBLISH", 95)
            self._phase_hook("PUBLISH")
            if validate_context is not None:
                validate_context()
            # Publish the verified archive under its final name
            replace_file(partial, final)
            published = True
            return _verify_zip(final, selected_id, manifest, "published")
        finally:
            # Clean up staging leftovers and unpublish a failed archive
            if staging.exists():
                shutil.rmtree(staging)
            if partial.exists():
                partial.unlink()
            if not published and final.exists():
                final.unlink()

    def history(self, root: Path, profile_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return usable backups and diagnostics for one profile."""
        destination = self._root(root, writable=False)
        usable: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        # Scan archives in stable name order, collecting diagnostics instead of failing
        for archive in sorted(destination.glob("*.zip"), key=lambda item: item.name.casefold()):
            try:
                manifest = read_archive_manifest(archive)
            except (FutureBackupSchema, FutureBackupArchive):
                diagnostics.append(_diagnostic("FUTURE_SCHEMA", "A backup uses an unsupported schema."))
                continue
            except (BackupManifestError, BackupArchiveError, OSError, ValueError, json.JSONDecodeError):
                diagnostics.append(_diagnostic("CORRUPT", "A backup has an invalid manifest."))
                continue
            # Skip archives that belong to other profiles
            if manifest.profile_id != profile_id:
                continue
            # Confirm content before listing a backup as usable
            try:
                verify_archive(archive, manifest)
                usable.append(_summary(manifest))
            except (BackupManifestError, BackupArchiveError, OSError, ValueError):
                diagnostics.append(_diagnostic(
                    "CORRUPT", "A backup for this profile failed content verification.",
                    profile_id=profile_id,
                ))
        # List the newest usable backups first
        usable.sort(key=lambda item: (str(item["created_at"]), str(item["backup_id"])), reverse=True)
        return {"backups": usable, "legacy_backups": [], "diagnostics": diagnostics}

    def delete_profile(self, root: Path, profile_id: str) -> int:
        """Delete every manager-created archive whose identifier belongs to a profile."""
        destination = self._root(root)
        removed = 0
        for archive in destination.glob(f"{profile_id}_*.zip"):
            if not archive.is_file() or is_reparse(archive):
                raise BackupStorageError(
                    "BACKUP_SOURCE_INVALID", "A profile backup cannot be deleted safely."
                )
            archive.unlink()
            removed += 1
        return removed

    @contextmanager
    def open_verified(
        self, root: Path, backup_id: object, profile_id: str,
    ) -> Iterator[tuple[Path, BackupManifest]]:
        """Yield an extracted, re-verified backup directory for restore work."""
        # Verify the selected archive before any extraction work
        destination = self._root(root)
        manifest = self.verified_manifest(destination, backup_id, profile_id)
        archive = destination / f"{manifest.backup_id}.zip"
        with tempfile.TemporaryDirectory(prefix=f".restore-{manifest.backup_id}-", dir=destination) as value:
            directory = Path(value)
            try:
                materialize_archive(archive, directory, manifest)
                extracted = read_manifest(directory, manifest.backup_id)
                verify_directory(directory, extracted)
            except (BackupManifestError, BackupArchiveError, BackupVerificationError,
                    FutureBackupSchema, FutureBackupArchive, OSError, ValueError) as error:
                raise BackupStorageError(
                    "BACKUP_INTEGRITY_FAILED", "The selected backup failed integrity verification.",
                ) from error
            # Consumer failures retain their own meaning; they are not archive corruption.
            yield directory, extracted

    def verified_manifest(
        self, root: Path, backup_id: object, profile_id: str,
    ) -> BackupManifest:
        """Return the manifest of a backup once it passes full integrity checks."""
        # Resolve the backup root read-only for verification
        destination = self._root(root, writable=False)
        # Reject malformed identifiers before touching the filesystem
        if not isinstance(backup_id, str) or BACKUP_ID.fullmatch(backup_id) is None:
            raise BackupStorageError("INVALID_REQUEST", "The backup identifier is invalid.")
        archive = destination / f"{backup_id}.zip"
        # Require a real archive file without reparse points
        if not archive.is_file() or is_reparse(archive):
            raise BackupStorageError("BACKUP_SOURCE_INVALID", "The selected backup is unavailable.")
        # Validate schema, profile ownership, and content before returning
        try:
            manifest = read_archive_manifest(archive, backup_id)
            if manifest.profile_id != profile_id:
                raise BackupStorageError("PROFILE_MISMATCH", "The backup belongs to another profile.")
            verify_archive(archive, manifest)
            return manifest
        except BackupStorageError:
            raise
        # Translate remaining failures into one storage error
        except (
            BackupManifestError, BackupArchiveError, BackupVerificationError,
            FutureBackupSchema, OSError, ValueError, json.JSONDecodeError,
        ) as error:
            raise BackupStorageError(
                "BACKUP_INTEGRITY_FAILED", "The selected backup failed integrity verification.",
            ) from error

    @staticmethod
    def require_restore_compatible(manifest: BackupManifest) -> None:
        """Raise when the manifest is not fully restorable on this server."""
        compatibility, reason = restore_compatibility(manifest)
        if compatibility == RESTORE_COMPATIBLE:
            return
        raise BackupStorageError(compatibility, reason)

    @staticmethod
    def _root(root: Path, *, writable: bool = True) -> Path:
        """Resolve the backup root, mapping source errors to storage errors."""
        # Re-raise source failures with their stable code preserved
        try:
            return safe_directory(root, "Backup root", writable=writable)
        except BackupSourceError as error:
            raise BackupStorageError(error.code, str(error)) from error
