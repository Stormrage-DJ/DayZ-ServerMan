"""Read-only verification of the external Steam Workshop cache."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain.workshop import CacheProof, WorkshopObservation
from .steam_vdf import VdfError, field, parse_vdf
from .tree_metadata import TreeMetadataError, has_alternate_stream


# DayZ Steam application identifier; the workshop manifest name derives from it
APP_ID = "221100"
MANIFEST_NAME = f"appworkshop_{APP_ID}.acf"


class CacheVerificationError(RuntimeError):
    """Raised when the Workshop cache cannot be verified safely."""
    pass


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

    def verify(self, workshop_id: str) -> CacheProof:
        """Verify one workshop item and return its cache proof."""
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
        inventory = self._inventory(item, root)
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
            installed_manifest_id=self._manifest_id(first_record),
            metadata_inventory_digest=metadata_digest,
        )

    @staticmethod
    def _manifest_id(record: bytes) -> str:
        """Read the installed manifest identifier from a record."""
        # Decode the persisted record and require a numeric manifest id
        parsed = json.loads(record.decode("utf-8"))
        value = field(parsed, "manifest")
        if not isinstance(value, str) or not value.isdecimal():
            raise CacheVerificationError("Steam manifest item state is incomplete")
        return value

    def observe(self, workshop_ids: tuple[str, ...]) -> tuple[WorkshopObservation, ...]:
        """Return the current observations for the requested Workshop items."""
        if any(not workshop_id.isdecimal() for workshop_id in workshop_ids):
            raise CacheVerificationError("Workshop item identifier is invalid")
        self._validated_root()
        manifest = self._validated_manifest()
        # Observation tolerates incomplete downloads, unlike proof
        app = self._manifest_app(manifest, require_complete=False)
        installed = field(app, "WorkshopItemsInstalled")
        details = field(app, "WorkshopItemDetails")
        # Coerce missing record groups to empty mappings for the report
        installed = installed if isinstance(installed, dict) else {}
        details = details if isinstance(details, dict) else {}
        return tuple(self._observation(workshop_id, installed, details)
                     for workshop_id in workshop_ids)

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
        try:
            app = WorkshopCacheVerifier._manifest_app(path, require_complete=True)
            installed = field(app, "WorkshopItemsInstalled")
            if not isinstance(installed, dict):
                raise VdfError("Steam manifest installed-item set is invalid")
            matches = [value for key, value in installed.items() if key == workshop_id]
            if len(matches) != 1 or not isinstance(matches[0], dict):
                raise VdfError("Steam manifest item identity is missing or ambiguous")
            record: dict[str, Any] = matches[0]
            # Require the persisted identity fields before canonicalizing
            required = {name: field(record, name) for name in ("manifest", "size", "timeupdated")}
            if any(not isinstance(value, str) or not value.isdecimal() for value in required.values()):
                raise VdfError("Steam manifest item state is incomplete")
        except (OSError, UnicodeError, VdfError) as error:
            raise CacheVerificationError(str(error)) from error
        # Canonical JSON keeps the digest stable across key orderings
        return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _manifest_app(path: Path, *, require_complete: bool) -> dict[str, Any]:
        """Parse the manifest and return its AppWorkshop object."""
        try:
            parsed = parse_vdf(path.read_text(encoding="utf-8-sig"))
            app = field(parsed, "AppWorkshop")
            # The manifest must belong to the DayZ application
            if not isinstance(app, dict) or field(app, "appid") != APP_ID:
                raise VdfError("Steam manifest app identity does not match")
            # Proof also requires Steam to report no pending work
            if require_complete and (
                field(app, "NeedsUpdate") != "0" or field(app, "NeedsDownload") != "0"
            ):
                raise VdfError("Steam manifest says that content is incomplete")
            return app
        except (OSError, UnicodeError, VdfError) as error:
            raise CacheVerificationError(str(error)) from error

    @staticmethod
    def _observation(
        workshop_id: str, installed: dict[str, Any], details: dict[str, Any],
    ) -> WorkshopObservation:
        """Build one observation from the installed and latest records."""
        installed_record = installed.get(workshop_id)
        detail_record = details.get(workshop_id)
        # Coerce absent or malformed item records to empty mappings
        installed_record = installed_record if isinstance(installed_record, dict) else {}
        detail_record = detail_record if isinstance(detail_record, dict) else {}

        def decimal(source: dict[str, Any], name: str) -> str | None:
            """Return a numeric field from a record, or None when absent."""
            # Match field names case-insensitively, as Steam does
            value = next((value for key, value in source.items()
                          if key.casefold() == name.casefold()), None)
            return value if isinstance(value, str) and value.isdecimal() else None

        installed_manifest = decimal(installed_record, "manifest")
        installed_time = decimal(installed_record, "timeupdated")
        latest_manifest = decimal(detail_record, "latest_manifest")
        latest_time = decimal(detail_record, "latest_timeupdated")
        return WorkshopObservation(
            workshop_id=workshop_id,
            installed_manifest_id=installed_manifest,
            latest_manifest_id=latest_manifest,
            installed_time_updated=int(installed_time) if installed_time else None,
            latest_time_updated=int(latest_time) if latest_time else None,
        )

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
    def _inventory(item: Path, root: Path) -> tuple[str, str, int, int]:
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
                # Hash the full bytes so the proof covers file content
                data = child.read_bytes()
                size = len(data)
                entries.append(("F", normalized, size, hashlib.sha256(data).hexdigest()))
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
