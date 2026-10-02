"""Verified archive publication helpers and public summaries."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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

def _available_id(root: Path, base: str) -> str:
    """Return the first free backup identifier, suffixing duplicates with a number."""
    # Search for the first free name, bounding the suffix attempts
    for index in range(1, 10_000):
        candidate = base if index == 1 else f"{base}_{index:02d}"
        if not (root / f"{candidate}.zip").exists():
            return candidate
    raise BackupStorageError("BACKUP_EXISTS", "No available backup filename could be generated.")


def _verify_staging(directory: Path, backup_id: str, expected: BackupManifest) -> None:
    """Re-verify the staged directory against the in-memory manifest."""
    # Re-read and re-verify the staged tree before publication
    try:
        manifest = read_manifest(directory, backup_id)
        if manifest != expected:
            raise BackupVerificationError("Persisted staging manifest changed unexpectedly.")
        verify_directory(directory, manifest)
    except (BackupManifestError, BackupVerificationError, OSError, ValueError, json.JSONDecodeError) as error:
        raise BackupStorageError("BACKUP_INTEGRITY_FAILED", "The staged backup failed verification.") from error


def _verify_zip(path: Path, backup_id: str, expected: BackupManifest, state: str) -> BackupManifest:
    """Re-verify an archive against the in-memory manifest for one stage."""
    # Re-read and re-verify the archive at its current location
    try:
        manifest = read_archive_manifest(path, backup_id)
        if manifest != expected:
            raise BackupArchiveError("Persisted archive manifest changed unexpectedly.")
        verify_archive(path, manifest)
        return manifest
    except (BackupManifestError, BackupArchiveError, OSError, ValueError, json.JSONDecodeError) as error:
        # Distinguish post-publication failures that require recovery
        code = "RECOVERY_REQUIRED" if state == "published" else "BACKUP_INTEGRITY_FAILED"
        raise BackupStorageError(code, f"The {state} backup ZIP failed verification.") from error


def _write_manifest(path: Path, manifest: BackupManifest) -> None:
    """Write the manifest JSON exclusively and flush it to disk."""
    # Serialize with sorted keys so the digest is reproducible
    payload = json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    # Create the manifest file exclusively and fsync its bytes
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _summary(manifest: BackupManifest) -> dict[str, Any]:
    """Return the public listing record for one manifest."""
    # Include restore compatibility so the UI can flag unusable backups
    compatibility, reason = restore_compatibility(manifest)
    return {
        "backup_id": manifest.backup_id, "profile_id": manifest.profile_id,
        "profile_revision": manifest.profile_revision, "settings_revision": manifest.settings_revision,
        "created_at": manifest.created_at, "entry_count": len(manifest.entries),
        "total_size": sum(item.size for item in manifest.entries),
        "manifest_digest": manifest.manifest_digest,
        "semantic_profile_digest": manifest.semantic_profile_digest,
        "runtime_profile": manifest.runtime_profile, "schema_version": manifest.schema_version,
        "status": "USABLE", "restore_compatibility": compatibility,
        "restore_compatibility_reason": reason,
    }


def _diagnostic(code: str, message: str, *, profile_id: str | None = None) -> dict[str, Any]:
    """Return a diagnostic record scoped to the destination or one profile."""
    result: dict[str, Any] = {
        "code": code, "message": message,
        "scope": "PROFILE" if profile_id is not None else "DESTINATION", "usable": False,
    }
    # Attach the profile identifier when the scan was profile-scoped
    if profile_id is not None:
        result["profile_id"] = profile_id
    return result
