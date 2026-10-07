"""Atomic profile schema migration from version 1 to version 2."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import read_text_shared, replace_file
from ..domain.profiles import ProfileInput, ProfileValidationError


# Observer invoked at named migration publication checkpoints
MigrationHook = Callable[[str, Path], None]


class ProfileMigrationError(RuntimeError):
    """Raised when a profile record cannot be migrated safely."""
    pass


def read_raw_profile(path: Path) -> dict[str, Any]:
    """Load a profile file as a raw JSON object."""
    # Read strict UTF-8 JSON so encoding problems surface as errors
    try:
        value = json.loads(read_text_shared(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileMigrationError("profile record is not valid UTF-8 JSON") from error
    # The record root must be an object
    if not isinstance(value, dict):
        raise ProfileMigrationError("profile record root must be an object")
    return value


def migrate_v1_document(value: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a schema version 1 profile document to version 2."""
    # The exact field set accepted from schema version 1
    expected = {
        "schema_version", "revision", "profile_id", "display_name", "server_executable",
        "server_config", "mission_root", "game_port", "mods", "extra_arguments",
    }
    # Reject anything but an exact schema version 1 record
    schema_version = value.get("schema_version")
    if (
        set(value) != expected
        or not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise ProfileMigrationError("profile schema 1 fields are invalid")
    revision = value.get("revision")
    mods = value.get("mods")
    # Revision must be a non-negative plain integer
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ProfileMigrationError("profile revision is invalid")
    if not isinstance(mods, list):
        raise ProfileMigrationError("profile mods must be an array")
    # Convert workshop mods into the version 2 source form
    converted_mods: list[dict[str, Any]] = []
    for mod in mods:
        if not isinstance(mod, dict) or set(mod) != {"workshop_id", "directory"}:
            raise ProfileMigrationError("schema 1 mod fields are invalid")
        converted_mods.append({
            "directory": mod["directory"],
            "launch_scope": "client",
            "source": {"kind": "workshop", "workshop_id": mod["workshop_id"]},
        })
    # Carry shared fields forward and add the new runtime profile slot
    fields = {key: value[key] for key in expected - {"schema_version", "revision", "mods"}}
    fields.update({"runtime_profile": None, "mods": converted_mods})
    # Validate the converted document through the current profile grammar
    try:
        parsed = ProfileInput.parse(fields)
    except ProfileValidationError as error:
        raise ProfileMigrationError("schema 1 profile cannot be migrated safely") from error
    return {"schema_version": 2, "revision": revision, **parsed_fields(parsed)}


def parsed_fields(value: ProfileInput) -> dict[str, Any]:
    """Project a parsed profile input back into a JSON document."""
    return {
        "profile_id": value.profile_id, "display_name": value.display_name,
        "server_executable": value.server_executable, "server_config": value.server_config,
        "runtime_profile": value.runtime_profile, "mission_root": value.mission_root,
        "game_port": value.game_port, "mods": [item.to_dict() for item in value.mods],
        "extra_arguments": list(value.extra_arguments),
    }


def publish_migration(path: Path, document: Mapping[str, Any], hook: MigrationHook | None) -> None:
    """Write the migrated document through a temporary file and hook checkpoints."""
    # Write beside the target so the final swap stays on one volume
    temporary = migration_temporary(path)
    payload = serialize_profile_document(document)
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    # The checkpoint lets tests interrupt between durable write and swap
    if hook is not None:
        hook("after_temp_fsync", path)
    # Swap only after the payload is fsynced
    replace_file(temporary, path)
    if hook is not None:
        hook("after_replace", path)


def migration_temporary(path: Path) -> Path:
    """Return the hidden temporary path used for profile migration."""
    return path.with_name(f".{path.name}.migration.tmp")


def serialize_profile_document(document: Mapping[str, Any]) -> str:
    """Serialize a document with stable ordering and a trailing newline."""
    return json.dumps(
        document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n"
