"""Immutable, contained source inventory for verified backups."""

from __future__ import annotations

import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ..domain.backups import entry_path_key, normalized_entry_path
from .backup_verification import is_reparse, path_has_reparse, sha256_file


class BackupSourceError(RuntimeError):
    """Raised when a backup source is unsafe, missing, unreadable, or changed."""

    def __init__(self, code: str, message: str) -> None:
        """Store the diagnostic code with the human-readable message."""
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class BackupSource:
    """One verified source file with its archive entry path, size, and digest."""

    source: Path
    entry_path: str
    size: int
    sha256: str
    mtime_ns: int


def safe_directory(path: Path, label: str, *, writable: bool = True) -> Path:
    """Return a resolved local directory after containment and access checks."""
    # Reject relative paths and network shares before any filesystem access
    raw = str(path)
    if raw.replace("/", "\\").startswith("\\\\") or not path.is_absolute():
        raise BackupSourceError("UNSUPPORTED_NETWORK", f"{label} must be an absolute local path.")
    # Resolve the directory, requiring it to exist
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BackupSourceError("PATH_INVALID", f"{label} is missing or inaccessible.") from error
    # Require a real directory without links or reparse points
    if not resolved.is_dir() or path_has_reparse(path):
        raise BackupSourceError("PATH_INVALID", f"{label} must be a local directory without reparse points.")
    # Confirm the access level the caller needs
    access = os.R_OK | (os.W_OK if writable else 0)
    if not os.access(resolved, access):
        state = "readable and writable" if writable else "readable"
        raise BackupSourceError("PATH_NOT_WRITABLE", f"{label} is not {state}.")
    return resolved


def single_source(dayz_root: Path, relative: str) -> BackupSource:
    """Return the verified source record for one payload file."""
    # Resolve the read-only DayZ root and the requested relative path
    root = safe_directory(dayz_root, "DayZ root", writable=False)
    windows = _relative(relative)
    # Confirm the file stays inside the root, then record it as a payload entry
    candidate = root.joinpath(*windows.parts)
    resolved = _contained(root, candidate, file=True)
    return _source(resolved, "payload/" + "/".join(windows.parts))


def directory_sources(
    dayz_root: Path, relative: str, entry_prefix: str,
) -> tuple[BackupSource, ...]:
    """Return verified source records for every file in one contained directory tree."""
    # Resolve the read-only DayZ root and the runtime profile directory
    root = safe_directory(dayz_root, "DayZ root", writable=False)
    windows = _relative(relative)
    directory = _contained(root, root.joinpath(*windows.parts), file=False)
    prefix = normalized_entry_path(entry_prefix).rstrip("/")
    sources: list[BackupSource] = []
    # Walk without following links and capture every regular file
    try:
        for parent, directories, files in os.walk(directory, followlinks=False):
            parent_path = Path(parent)
            for name in directories:
                _reject_node(parent_path / name)
            for name in files:
                path = parent_path / name
                _reject_node(path)
                relative_path = path.relative_to(directory)
                entry = normalized_entry_path(prefix + "/" + "/".join(relative_path.parts))
                sources.append(_source(path.resolve(strict=True), entry))
    except OSError as error:
        raise BackupSourceError(
            "BACKUP_SOURCE_INVALID", "The backup source tree is missing or inaccessible."
        ) from error
    # Order entries canonically and reject Windows case collisions
    sources.sort(key=lambda item: entry_path_key(item.entry_path))
    folded = [item.entry_path.casefold() for item in sources]
    if len(folded) != len(set(folded)):
        raise BackupSourceError(
            "BACKUP_SOURCE_COLLISION",
            "The backup source tree contains names that collide on Windows.",
        )
    return tuple(sources)


def runtime_sources(dayz_root: Path, relative: str) -> tuple[BackupSource, ...]:
    """Return verified source records for every file under the runtime profile tree."""
    return directory_sources(dayz_root, relative, "runtime-profile")


def copy_verified(source: BackupSource, target: Path) -> None:
    """Copy one verified source file, confirming it stays unchanged throughout."""
    # Confirm the recorded size and timestamp still describe the source
    before = _stat(source.source)
    if (before.st_size, before.st_mtime_ns) != (source.size, source.mtime_ns):
        raise BackupSourceError("BACKUP_SOURCE_CHANGED", "A backup source changed during backup creation.")
    # Stage the copy beside the target and flush it to disk
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    # Re-check the source and the copy against the recorded digest
    after = _stat(source.source)
    if (
        (after.st_size, after.st_mtime_ns) != (source.size, source.mtime_ns)
        or sha256_file(source.source) != source.sha256
        or sha256_file(target) != source.sha256
    ):
        raise BackupSourceError("BACKUP_SOURCE_CHANGED", "A backup source changed during backup creation.")


def _relative(value: str) -> PureWindowsPath:
    """Return the NFC-normalized relative path, rejecting unsafe shapes."""
    # Normalize separators for the Windows path rules
    windows = PureWindowsPath(unicodedata.normalize("NFC", value))
    # Reject alternate data streams, absolute paths, and parent or dot segments
    if any(":" in part for part in windows.parts):
        raise BackupSourceError("PATH_INVALID", "Alternate data streams are not supported.")
    if (
        windows.is_absolute() or windows.drive or windows.root or ".." in windows.parts
        or any(part in ("", ".") for part in windows.parts)
    ):
        raise BackupSourceError("PATH_INVALID", "A backup source path is invalid.")
    return windows


def _contained(root: Path, candidate: Path, *, file: bool) -> Path:
    """Return the resolved candidate once it is confirmed inside the root."""
    # Reject links and streams along the path chain before resolving
    _reject_chain(root, candidate)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise BackupSourceError(
            "BACKUP_SOURCE_INVALID", "A required backup source is missing or unsafe."
        ) from error
    # Require the expected file or directory type without links or reparse points
    if is_reparse(resolved) or (file and not resolved.is_file()) or (not file and not resolved.is_dir()):
        raise BackupSourceError("BACKUP_SOURCE_INVALID", "A required backup source has the wrong type.")
    return resolved


def _reject_chain(root: Path, candidate: Path) -> None:
    """Reject a candidate that escapes the root or crosses a link or stream."""
    # Refuse candidates outside the allowed root outright
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise BackupSourceError("PATH_OUTSIDE_ALLOWED_ROOT", "A backup source escapes the DayZ root.") from error
    # Check the root and every existing segment for links or streams
    current = root
    _reject_node(current)
    for part in relative.parts:
        current /= part
        if current.exists():
            _reject_node(current)


def _reject_node(path: Path) -> None:
    """Reject a path whose name hides a link, reparse point, or data stream."""
    if ":" in path.name or is_reparse(path):
        raise BackupSourceError("PATH_INVALID", "Backup sources cannot contain links, reparse points, or streams.")


def _source(path: Path, entry: str) -> BackupSource:
    """Return the verified source record for one regular file."""
    # Require a regular file and capture its identity metadata
    metadata = _stat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise BackupSourceError("BACKUP_SOURCE_INVALID", "A required backup source is not a regular file.")
    return BackupSource(path, normalized_entry_path(entry), metadata.st_size, sha256_file(path), metadata.st_mtime_ns)


def _stat(path: Path) -> os.stat_result:
    """Return file metadata without following links."""
    # Report inaccessible sources as domain errors
    try:
        return path.stat(follow_symlinks=False)
    except OSError as error:
        raise BackupSourceError("BACKUP_SOURCE_INVALID", "A backup source is inaccessible.") from error
