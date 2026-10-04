"""Parsing of the Steam Workshop manifest: app object, item records and observations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.workshop import WorkshopObservation
from .steam_vdf import VdfError, field, parse_vdf


# DayZ Steam application identifier; the workshop manifest name derives from it
APP_ID = "221100"
MANIFEST_NAME = f"appworkshop_{APP_ID}.acf"


class CacheVerificationError(RuntimeError):
    """Raised when the Workshop cache cannot be verified safely."""
    pass


def manifest_app(path: Path, *, require_complete: bool) -> dict[str, Any]:
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


def steam_reports_complete(path: Path) -> bool:
    """Return whether the manifest reports no pending update and no pending download."""
    try:
        manifest_app(path, require_complete=True)
    except CacheVerificationError:
        # An unreadable manifest never counts as complete
        return False
    return True


def manifest_record(path: Path, workshop_id: str) -> bytes:
    """Return the canonical JSON bytes of one installed item record."""
    try:
        app = manifest_app(path, require_complete=True)
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


def manifest_id(record: bytes) -> str:
    """Read the installed manifest identifier from a record."""
    # Decode the persisted record and require a numeric manifest id
    parsed = json.loads(record.decode("utf-8"))
    value = field(parsed, "manifest")
    if not isinstance(value, str) or not value.isdecimal():
        raise CacheVerificationError("Steam manifest item state is incomplete")
    return value


def observe_items(path: Path, workshop_ids: tuple[str, ...]) -> tuple[WorkshopObservation, ...]:
    """Return the observations of the requested items from a validated manifest."""
    # Observation tolerates incomplete downloads, unlike proof
    app = manifest_app(path, require_complete=False)
    try:
        installed = field(app, "WorkshopItemsInstalled")
        details = field(app, "WorkshopItemDetails")
    except VdfError as error:
        # A manifest without both record groups cannot be observed
        raise CacheVerificationError(str(error)) from error
    # Coerce malformed record groups to empty mappings for the report
    installed = installed if isinstance(installed, dict) else {}
    details = details if isinstance(details, dict) else {}
    return tuple(_observation(workshop_id, installed, details)
                 for workshop_id in workshop_ids)


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
