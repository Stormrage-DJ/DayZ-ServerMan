"""Synthetic versioned migration payloads for legacy import tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

from dayz_serverman.application.migration_outputs import versioned_json
from dayz_serverman.repositories.external_root import external_root_identity


def profile_bytes(profile_id: str, *, revision: int = 0, marker: str = "") -> bytes:
    """Return a versioned profile payload for the given profile id."""
    return versioned_json(2, revision, {
        "profile_id": profile_id,
        "display_name": f"Profile {profile_id} {marker}".strip(),
        "server_executable": "DayZServer_x64.exe",
        "server_config": "serverDZ.cfg",
        "runtime_profile": None,
        "mission_root": None,
        "game_port": 2302,
        "mods": [],
        "extra_arguments": [],
    })


def settings_bytes(*, revision: int = 0) -> bytes:
    """Return a versioned settings payload with no configured paths."""
    return versioned_json(1, revision, {
        "dayz_root": None,
        "dayz_executable": None,
        "steamcmd_root": None,
        "steamcmd_executable": None,
        "workshop_content_root": None,
        "custom_backup_root": None,
        "last_validated_paths": {},
    })


def index_bytes(source_root: Path, *, revision: int = 0) -> bytes:
    """Return an external-root index payload for the given root path."""
    # Create the root so root identity inspection finds a real directory
    source_root.mkdir(parents=True, exist_ok=True)
    canonical = str(source_root)
    return versioned_json(1, revision, {
        "source_root": canonical,
        "source_root_identity": external_root_identity(canonical),
        "inventory_digest": hashlib.sha256(b"[]").hexdigest(),
        "verification_version": 1,
        "last_verified_at": "1970-01-01T00:00:00Z",
        "entries": [],
    })


def report_bytes(migration_id: str) -> bytes:
    """Return a completed migration report payload for the given id."""
    return versioned_json(1, 0, {
        "migration_id": migration_id,
        "source_identity": hashlib.sha256(b"source").hexdigest(),
        "source_files": [],
        "selected_items": [],
        "skipped_items": [],
        "published": [],
        "warnings": [],
        "backup_history": {
            "count": 0, "size": 0, "status": "EXTERNAL_REFERENCE",
            "copied": False, "indexed": False, "inventory_digest": None,
        },
        "result": "SUCCEEDED",
    })
