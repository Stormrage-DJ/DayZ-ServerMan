"""Deterministic current-schema outputs for a reviewed legacy import."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from ..domain.migrations import ConvertedProfile
from ..domain.models import ManagerSettings, SettingsInput
from ..domain.profiles import ProfileRecord
from ..repositories.legacy_backup_index import LegacyBackupIndex
from .settings import SETTINGS_FIELDS, SettingsService


def settings_output(
    service: SettingsService, current: ManagerSettings,
    updates: Mapping[str, str], expected_revision: int | None,
) -> tuple[bytes, int]:
    """Build the versioned settings payload and its next revision."""
    # Lay the reviewed updates over the current settings values
    values = SettingsInput(**{
        field: updates.get(field, getattr(current, field)) for field in SETTINGS_FIELDS
    })
    # Validate through the settings service so failures match interactive saves
    prepared = service.prepare_save(values, expected_revision)
    # Continue the existing revision chain; a fresh document starts at zero
    revision = 0 if expected_revision is None else expected_revision + 1
    fields = service.storage_fields(prepared)
    return versioned_json(1, revision, fields), revision


def profile_output(converted: ConvertedProfile) -> tuple[bytes, str]:
    """Build the versioned profile payload and return its profile identifier."""
    # A profile item must carry a converted profile
    if converted.profile is None:
        raise ValueError("selected profile conversion is unavailable")
    # Wrap the profile as revision zero of the current profile schema
    record = ProfileRecord(0, converted.profile)
    return versioned_json(2, 0, record.fields()), converted.profile.profile_id


def legacy_index_output(
    index: LegacyBackupIndex, expected_revision: int | None,
) -> tuple[bytes, int]:
    """Build the versioned legacy backup-index payload and its next revision."""
    # Continue the existing revision chain; a fresh index starts at zero
    revision = 0 if expected_revision is None else expected_revision + 1
    return versioned_json(1, revision, index.fields()), revision


def report_output(
    migration_id: str, source_digest: str, source_files: Iterable[dict[str, object]],
    selected: Iterable[str], skipped: Iterable[str], published: list[dict[str, Any]],
    warnings: Iterable[str], backup_index: LegacyBackupIndex | None,
    backup_index_selected: bool,
) -> bytes:
    """Build the deterministic migration report payload."""
    # Assemble the review evidence that will be published with the migration
    fields = {
        "migration_id": migration_id,
        "source_identity": source_digest,
        "source_files": list(source_files),
        "selected_items": sorted(selected),
        "skipped_items": sorted(skipped),
        "published": published,
        "warnings": sorted(set(warnings)),
        "backup_history": {
            "count": len(backup_index.entries) if backup_index else 0,
            "size": sum(item.size for item in backup_index.entries) if backup_index else 0,
            "status": "EXTERNAL_REFERENCE", "copied": False,
            "indexed": backup_index_selected,
            "inventory_digest": backup_index.inventory_digest if backup_index else None,
        },
        "result": "SUCCEEDED",
    }
    # Reports are immutable evidence, so they always publish as revision zero
    return versioned_json(1, 0, fields)


def versioned_json(schema_version: int, revision: int, fields: Mapping[str, Any]) -> bytes:
    """Encode a document as canonical pretty-printed UTF-8 JSON."""
    # Merge the schema envelope over the payload fields
    document = {"schema_version": schema_version, "revision": revision, **fields}
    # Sort keys and terminate with a newline so published digests stay stable
    return (
        json.dumps(
            document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
        ) + "\n"
    ).encode("utf-8")
