"""Read-only inventory for an explicitly selected legacy manager root."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backup_verification import is_reparse, path_has_reparse
from .external_root import external_root_identity, validate_external_root
from .legacy_backup_proof import (
    backup_format, backup_path_identity, backup_reference_id,
    normalize_backup_relative, utc_timestamp,
)
from .legacy_workshop import resolve_legacy_workshop_ids


# Directory and file names that identify a supported legacy manager root
PROFILE_DIRECTORY = "dayz_server_manager-profiles"
STATE_FILE = "dayz_server_manager-state.json"
BACKUP_DIRECTORY = "dayz_server_manager-backups"
SETTINGS_FILE = "dayz_server_manager-steam.json"
# Refuse JSON documents above this byte bound to keep inspection memory-bounded
MAX_JSON_SIZE = 1024 * 1024


class LegacySourceError(RuntimeError):
    """Raised when the selected legacy root is unsafe or unsupported."""
    pass


@dataclass(frozen=True)
class LegacyFile:
    """One inventoried legacy source file with its role, size, and digest."""
    role: str
    relative_path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the file record as a plain JSON-ready mapping."""
        return dict(vars(self))


@dataclass(frozen=True)
class LegacyBackupReference:
    """One legacy backup with its identity, format, and verification proof."""
    reference_id: str
    relative_path: str
    size: int
    sha256: str
    format: str
    warnings: tuple[str, ...]
    verified_at: str

    def to_dict(self) -> dict[str, object]:
        """Return the full reference including its verification timestamp."""
        return {
            "reference_id": self.reference_id, "relative_path": self.relative_path,
            "size": self.size, "sha256": self.sha256, "format": self.format,
            "warnings": list(self.warnings), "verified_at": self.verified_at,
        }

    def proof_dict(self) -> dict[str, object]:
        """Return the verification-time-free proof used in the source digest."""
        # Exclude the timestamp so an unchanged backup keeps a stable proof
        value = self.to_dict()
        value.pop("verified_at")
        return value


@dataclass(frozen=True)
class LegacyInventory:
    """Immutable, digest-proven snapshot of a selected legacy manager root."""
    root: Path
    source_label: str
    files: tuple[LegacyFile, ...]
    profiles: tuple[tuple[str, dict[str, Any]], ...]
    state: dict[str, Any] | None
    has_legacy_settings: bool
    ignored: tuple[str, ...]
    backup_root: Path | None
    source_root_identity: str
    backups: tuple[LegacyBackupReference, ...]
    workshop_ids: tuple[tuple[str, str], ...]

    @property
    def backup_count(self) -> int:
        """Return how many backup references were verified."""
        return len(self.backups)

    @property
    def backup_size(self) -> int:
        """Return the combined byte size of all verified backups."""
        return sum(item.size for item in self.backups)

    @property
    def source_digest(self) -> str:
        """Return the canonical digest that binds every inventory record."""
        # Serialize deterministically so equal content yields one stable digest
        payload = json.dumps(
            {
                "files": [item.to_dict() for item in self.files],
                "ignored": list(self.ignored),
                "backups": [item.proof_dict() for item in self.backups],
                "workshop_ids": list(self.workshop_ids),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def inspect_legacy_root(root_value: object, manager_root: Path, manager_data: Path) -> LegacyInventory:
    """Inspect an explicitly selected legacy root and return its read-only inventory."""
    # Reject values that are not plain local paths, including alternate data streams
    if not isinstance(root_value, str) or not root_value:
        raise LegacySourceError("A legacy root must be selected.")
    if root_value.replace("/", "\\").startswith("\\\\") or ":" in root_value[2:]:
        raise LegacySourceError("The legacy root must be a local path without alternate streams.")
    # Require an existing absolute directory that resolves to a stable identity
    root = Path(root_value)
    if not root.is_absolute() or not root.is_dir():
        raise LegacySourceError("The legacy root must be an existing absolute directory.")
    root = root.resolve(strict=True)
    root_identity = legacy_root_identity(root)
    if path_has_reparse(Path(root_value)):
        raise LegacySourceError("The legacy root must not contain links or reparse points.")
    # Refuse overlap with manager-owned data so inspection stays strictly read-only
    for forbidden in (manager_root.resolve(strict=False), manager_data.resolve(strict=False)):
        if root == forbidden or _inside(root, forbidden) or _inside(forbidden, root):
            raise LegacySourceError("The legacy root must be separate from manager-owned data.")
    # Confirm the signature directories of a supported legacy manager
    signature = root / "dayz_server_manager"
    profiles_root = root / PROFILE_DIRECTORY
    if not signature.is_dir() or not profiles_root.is_dir():
        raise LegacySourceError("The selected folder does not contain a supported legacy manager.")
    if path_has_reparse(signature) or path_has_reparse(profiles_root):
        raise LegacySourceError("The legacy signature contains an unsupported reparse point.")

    # Inventory every sorted profile together with its file record
    files: list[LegacyFile] = []
    profiles: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(profiles_root.glob("*.json"), key=lambda item: (item.name.casefold(), item.name)):
        _safe_file(root, path)
        raw = _read_json(path)
        if not isinstance(raw, dict):
            raise LegacySourceError("A legacy profile is not a JSON object.")
        relative = path.relative_to(root).as_posix()
        files.append(_file_record("PROFILE", relative, path))
        profiles.append((relative, raw))

    # Resolve workshop ids and record the metadata files that produced them
    workshop_ids, metadata_paths = resolve_legacy_workshop_ids(root, profiles)
    for path in metadata_paths:
        _safe_file(root, path)
        files.append(_file_record("MOD_METADATA", path.relative_to(root).as_posix(), path))

    # Read the legacy manager state when present and type-check it
    state_path = root / STATE_FILE
    state: dict[str, Any] | None = None
    if state_path.exists():
        _safe_file(root, state_path)
        raw_state = _read_json(state_path)
        if not isinstance(raw_state, dict):
            raise LegacySourceError("Legacy manager state is not a JSON object.")
        state = raw_state
        files.append(_file_record("MANAGER_STATE", STATE_FILE, state_path))

    # Read legacy settings when present; the file signals an import candidate
    settings_path = root / SETTINGS_FILE
    has_legacy_settings = False
    if settings_path.exists():
        _safe_file(root, settings_path)
        if not isinstance(_read_json(settings_path), dict):
            raise LegacySourceError("Legacy settings are not a JSON object.")
        files.append(_file_record("LEGACY_SETTINGS", SETTINGS_FILE, settings_path))
        has_legacy_settings = True

    # Inventory the legacy backup history without following links
    backups = root / BACKUP_DIRECTORY
    backup_files: list[Path] = []
    backup_references: list[LegacyBackupReference] = []
    if backups.exists():
        # Validate the backup root itself before walking any entries
        try:
            canonical_backup_root = validate_external_root(
                str(backups), require_existing=True,
            )
        except ValueError as error:
            raise LegacySourceError("Legacy backup history is unsafe to inventory.") from error
        backup_root_identity = external_root_identity(canonical_backup_root)
        # Walk manually so unsafe directories are rejected before files are read
        for parent, directories, names in os.walk(backups, followlinks=False):
            parent_path = Path(parent)
            for name in directories:
                if is_reparse(parent_path / name) or ":" in name:
                    raise LegacySourceError("Legacy backup history contains an unsafe directory.")
            for name in names:
                path = parent_path / name
                _safe_file(backups, path)
                backup_files.append(path)
        # Reject duplicate or ambiguous backup identities before recording proofs
        identities: set[str] = set()
        for path in sorted(
            backup_files,
            key=lambda item: (item.relative_to(backups).as_posix().casefold(),
                              item.relative_to(backups).as_posix()),
        ):
            relative = normalize_backup_relative(
                path.relative_to(backups).as_posix(), require_canonical=False,
            )
            identity = backup_path_identity(relative)
            if identity in identities or ":" in relative:
                raise LegacySourceError("Legacy backup paths are ambiguous or unsafe.")
            identities.add(identity)
            files.append(_file_record(
                "LEGACY_BACKUP", path.relative_to(root).as_posix(), path,
            ))
            size = path.stat().st_size
            digest = _sha256(path)
            archive_format, warnings = backup_format(relative)
            reference_id = backup_reference_id(backup_root_identity, relative)
            backup_references.append(LegacyBackupReference(
                reference_id, relative, size, digest, archive_format, warnings,
                utc_timestamp(),
            ))

    # Record top-level entries outside the recognized legacy layout as ignored
    recognized = {
        "dayz_server_manager", PROFILE_DIRECTORY, STATE_FILE, SETTINGS_FILE, BACKUP_DIRECTORY,
    }
    ignored = tuple(sorted(
        (path.name for path in root.iterdir() if path.name not in recognized),
        key=lambda value: (value.casefold(), value),
    ))
    # Assemble the immutable inventory result
    return LegacyInventory(
        root,
        "Selected legacy manager",
        tuple(files),
        tuple(profiles),
        state,
        has_legacy_settings,
        ignored,
        backups if backups.exists() else None,
        root_identity,
        tuple(backup_references),
        workshop_ids,
    )


def _read_json(path: Path) -> Any:
    """Read a bounded legacy JSON document that must not contain duplicate fields."""
    # Refuse oversized payloads before reading them into memory
    if path.stat().st_size > MAX_JSON_SIZE:
        raise LegacySourceError("A legacy JSON file exceeds the supported size.")
    # Parse with duplicate detection so ambiguous documents are refused
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise LegacySourceError("A legacy JSON file is malformed or ambiguous.") from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate keys."""
    result: dict[str, Any] = {}
    # A repeated field would make the document ambiguous, so refuse it
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _file_record(role: str, relative: str, path: Path) -> LegacyFile:
    """Build a digest-proven record for one inventoried file."""
    return LegacyFile(role, relative, path.stat().st_size, _sha256(path))


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file read in bounded blocks."""
    digest = hashlib.sha256()
    # Stream fixed-size blocks so large backup archives stay memory-bounded
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def legacy_root_identity(root: Path) -> str:
    """Return a stable identity for the root based on its path and filesystem ids."""
    metadata = root.stat()
    # Mix path, device, and inode so the same directory keeps one identity
    payload = f"{os.path.normcase(str(root))}\0{metadata.st_dev}\0{metadata.st_ino}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_file(root: Path, path: Path) -> None:
    """Raise when a candidate file is not a regular file inside the selected root."""
    # Require the resolved path to stay inside the selected root
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as error:
        raise LegacySourceError("A legacy source file escapes the selected root.") from error
    # Reject links, reparse points, and anything that is not a regular file
    if not path.is_file() or is_reparse(path) or path_has_reparse(path.parent):
        raise LegacySourceError("A legacy source file is not a safe regular file.")
    # Compare raw common paths as a second guard against escaped joins
    if os.path.commonpath((str(root), str(path.resolve(strict=True)))) != str(root):
        raise LegacySourceError("A legacy source file escapes the selected root.")


def _inside(path: Path, root: Path) -> bool:
    """Return whether the path lies beneath the given root."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
