"""Safe ZIP encoding, verification, and materialization for backups."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path

from ..domain.backups import (
    BACKUP_SCHEMA_VERSION,
    BackupManifest,
    BackupManifestError,
    normalized_entry_path,
    manifest_digest,
)


class BackupArchiveError(RuntimeError):
    """Raised when a backup ZIP is malformed, unsafe, or inconsistent."""
    pass


class FutureBackupArchive(RuntimeError):
    """Raised when an archive declares a newer schema than this version supports."""
    pass


def write_archive(source: Path, target: Path, manifest: BackupManifest) -> None:
    """Write a ZIP archive from a staging tree that holds the signed manifest."""
    # Create the archive exclusively so publication never reuses an old file
    with zipfile.ZipFile(
        target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True,
    ) as archive:
        # Store the manifest first, then each declared payload entry
        archive.write(source / "manifest.json", "manifest.json")
        for entry in manifest.entries:
            archive.write(source / entry.path, entry.path)


def read_archive_manifest(path: Path, expected_backup_id: str | None = None) -> BackupManifest:
    """Read and validate the manifest of a backup archive."""
    try:
        # Reject unsafe or duplicate members before parsing the manifest
        with zipfile.ZipFile(path, "r") as archive:
            infos = _validated_infos(archive)
            # Parse the manifest JSON from the validated member
            raw = json.loads(archive.read(infos["manifest.json"]).decode("utf-8"))
            # Reject archives written by a newer, unsupported schema
            if isinstance(raw, dict) and isinstance(raw.get("schema_version"), int):
                if raw["schema_version"] > BACKUP_SCHEMA_VERSION:
                    raise FutureBackupArchive
            # Validate the manifest identity, digest, and member list
            manifest = BackupManifest.parse(raw)
            expected = expected_backup_id or path.stem
            if manifest.backup_id != expected:
                raise BackupManifestError("backup identifier does not match its archive")
            if manifest.manifest_digest != manifest_digest(manifest.unsigned_dict()):
                raise BackupManifestError("backup manifest digest does not match")
            _require_members(infos, manifest)
            return manifest
    except (zipfile.BadZipFile, KeyError, UnicodeError) as error:
        raise BackupArchiveError("Backup ZIP structure is invalid.") from error


def verify_archive(path: Path, manifest: BackupManifest) -> None:
    """Verify every archive member against the manifest before use."""
    try:
        # Reject unsafe or duplicate members before checking payloads
        with zipfile.ZipFile(path, "r") as archive:
            infos = _validated_infos(archive)
            _require_members(infos, manifest)
            # Compare each member size and digest with the manifest
            for entry in manifest.entries:
                info = infos[entry.path]
                if info.file_size != entry.size:
                    raise BackupArchiveError("Backup ZIP member size does not match the manifest.")
                # Stream the member in bounded chunks and compare its digest
                digest = hashlib.sha256()
                with archive.open(info, "r") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != entry.sha256:
                    raise BackupArchiveError("Backup ZIP member checksum does not match.")
    except zipfile.BadZipFile as error:
        raise BackupArchiveError("Backup ZIP structure is invalid.") from error


def materialize_archive(path: Path, target: Path, manifest: BackupManifest) -> None:
    """Extract a verified archive into a target directory."""
    try:
        # Reject unsafe or duplicate members before extraction
        with zipfile.ZipFile(path, "r") as archive:
            infos = _validated_infos(archive)
            _require_members(infos, manifest)
            # Extract the manifest and payload members with exclusive creation
            for name in ("manifest.json", *(entry.path for entry in manifest.entries)):
                destination = target.joinpath(*name.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(infos[name], "r") as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except zipfile.BadZipFile as error:
        raise BackupArchiveError("Backup ZIP structure is invalid.") from error


def _validated_infos(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Return archive members keyed by validated name, rejecting unsafe entries."""
    result: dict[str, zipfile.ZipInfo] = {}
    folded: set[str] = set()
    # Validate every member, rejecting directories, encrypted members, and links
    for info in archive.infolist():
        if info.is_dir() or info.flag_bits & 0x1:
            raise BackupArchiveError("Backup ZIP contains an unsupported member.")
        # Symbolic links appear in the Unix external attribute bits
        mode = info.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            raise BackupArchiveError("Backup ZIP contains a symbolic link.")
        try:
            name = info.filename if info.filename == "manifest.json" else normalized_entry_path(info.filename)
        except BackupManifestError as error:
            raise BackupArchiveError("Backup ZIP contains an unsafe member path.") from error
        # Reject names that would collide under Windows case folding
        key = name.casefold()
        if key in folded:
            raise BackupArchiveError("Backup ZIP contains duplicate member paths.")
        folded.add(key)
        result[name] = info
    return result


def _require_members(infos: dict[str, zipfile.ZipInfo], manifest: BackupManifest) -> None:
    """Raise when the archive members do not exactly match the manifest."""
    # Require exactly the manifest plus the declared payload entries
    expected = {"manifest.json", *(entry.path for entry in manifest.entries)}
    if set(infos) != expected:
        raise BackupArchiveError("Backup ZIP contents do not match the manifest.")
