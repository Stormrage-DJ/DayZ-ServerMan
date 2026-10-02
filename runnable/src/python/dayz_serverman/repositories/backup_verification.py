"""Backup checksum, manifest, and reparse-point verification."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from ..domain.backups import (
    BACKUP_SCHEMA_VERSION,
    BackupManifest,
    BackupManifestError,
    manifest_digest,
)


class BackupVerificationError(RuntimeError):
    """Raised when a backup snapshot or manifest fails verification."""
    pass


class FutureBackupSchema(RuntimeError):
    """Raised when a manifest declares a newer schema than this version supports."""
    pass


def is_reparse(path: Path) -> bool:
    """Return True when the path itself is a symbolic link or reparse point."""
    try:
        metadata = path.lstat()
    except OSError:
        return False
    # Detect links from the file mode and the Windows reparse-point attribute (0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400)


def path_has_reparse(path: Path) -> bool:
    """Return True when any existing ancestor of the path is a reparse point."""
    current = Path(path.anchor)
    # Walk every existing ancestor from the anchor down
    for part in path.parts[1:]:
        current /= part
        if current.exists() and is_reparse(current):
            return True
    return False


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    # Stream the file in bounded chunks to keep memory flat
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(directory: Path, expected_backup_id: str | None = None) -> BackupManifest:
    """Read and validate a snapshot manifest from a backup directory."""
    # Reject unsafe snapshot directories and manifest files before reading
    if not directory.is_dir() or is_reparse(directory):
        raise BackupVerificationError("Snapshot directory is unsafe.")
    path = directory / "manifest.json"
    if is_reparse(path):
        raise BackupVerificationError("Snapshot manifest is unsafe.")
    # Parse the JSON document and reject newer schemas
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (
        isinstance(raw, dict)
        and isinstance(raw.get("schema_version"), int)
        and not isinstance(raw.get("schema_version"), bool)
    ):
        if raw["schema_version"] > BACKUP_SCHEMA_VERSION:
            raise FutureBackupSchema
    # Validate the manifest identity and digest
    manifest = BackupManifest.parse(raw)
    if manifest.backup_id != (expected_backup_id or directory.name):
        raise BackupManifestError("backup identifier does not match its directory")
    if manifest.manifest_digest != manifest_digest(manifest.unsigned_dict()):
        raise BackupManifestError("backup manifest digest does not match")
    return manifest


def verify_directory(directory: Path, manifest: BackupManifest) -> None:
    """Verify snapshot files against the manifest, rejecting unsafe entries."""
    expected = {item.path for item in manifest.entries}
    actual: set[str] = set()
    # Collect the on-disk payload, rejecting links and streams during the walk
    for parent, directories, files in os.walk(directory, followlinks=False):
        parent_path = Path(parent)
        for name in directories:
            if is_reparse(parent_path / name):
                raise BackupVerificationError("Snapshot contains an unsafe directory.")
        for name in files:
            path = parent_path / name
            if is_reparse(path):
                raise BackupVerificationError("Snapshot contains an unsafe file.")
            relative = path.relative_to(directory).as_posix()
            if relative != "manifest.json":
                actual.add(relative)
    # Require the on-disk payload to match the manifest exactly
    if actual != expected:
        raise BackupVerificationError("Snapshot contents do not match the manifest.")
    # Confirm every declared entry size and digest
    for entry in manifest.entries:
        path = directory / entry.path
        if path.stat().st_size != entry.size or sha256_file(path) != entry.sha256:
            raise BackupVerificationError("Snapshot payload checksum does not match.")
    if manifest.schema_version == 3:
        actual_directories = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_dir()}
        expected_directories = set(manifest.directories)
        for entry in manifest.entries:
            expected_directories.update(parent.as_posix() for parent in Path(entry.path).parents if parent.as_posix() != ".")
        if actual_directories != expected_directories:
            raise BackupVerificationError("Snapshot directories do not match the manifest.")
        from .backup_reconstruction import verify_reconstruction_bytes
        verify_reconstruction_bytes(manifest, (directory / manifest.reconstruction["config_entry"]).read_bytes())
