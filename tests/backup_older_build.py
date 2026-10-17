"""Frozen copies of the two checks with which an older build refuses a schema 4 backup (D18, A10).

Their sources are never imported, so this build's code cannot stand in for an older build (Architect
2026-10-16 21:00:06 in evidence 10.10).

- The schema check of `BackupManifest.parse` before 10.9 accepts schemas 1 to 3 only.
  - Source: `domain/backups.py` after the 10.3 split, SHA-256 prefix 076d2ebf369457c9 (evidence 10.3).
  - Since 10.9 the file accepts schema 4 (0083ec8dc0fe00e3f1d8e7dc00a68142cabdb8aae621ff008d36f5e4a97f536e).
- `restore_compatibility` before 10.10.
  - Source: `domain/backup_entries.py`, SHA-256 e3be76fd97f418fbbff77a03ffbb5f16a227f47ea0f607d588aa4542d9c20633.

This module imports nothing from `domain/backups.py`.
"""

from __future__ import annotations

from typing import Any

# The schemas that the manifest parse of an older build accepts
OLDER_SCHEMAS = (1, 2, 3)
RESTORE_COMPATIBLE = "COMPATIBLE"
RESTORE_PENDING_RUNTIME = "PENDING_RUNTIME_PROFILE_SUPPORT"
RESTORE_LEGACY = "LEGACY_PROFILE_SCHEMA"


def older_parse_accepts(raw: dict[str, Any]) -> bool:
    """Return whether the manifest parse of an older build accepts the schema of a raw manifest."""
    version = raw.get("schema_version")
    return isinstance(version, int) and not isinstance(version, bool) and version in OLDER_SCHEMAS


def older_restore_compatibility(manifest: Any) -> tuple[str, str]:
    """Return what `restore_compatibility` of an older build gives; the body is the frozen copy."""
    if manifest.schema_version == 1:
        return (
            RESTORE_LEGACY,
            "This legacy backup cannot be restored with a profile-v2 context.",
        )
    if any(entry.path.split("/", 1)[0] not in {"payload", "runtime-profile"}
           for entry in manifest.entries):
        return (
            RESTORE_PENDING_RUNTIME,
            "This backup contains a target form that the current restore mapper does not support.",
        )
    return (RESTORE_COMPATIBLE, "This backup is compatible with the current restore mapper.")
