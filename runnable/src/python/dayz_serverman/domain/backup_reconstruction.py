"""Strict profile reconstruction metadata and directory inventory validation."""

from pathlib import PureWindowsPath
from typing import Any

from .profiles import ProfileInput, ProfileRecord, semantic_profile_digest, validate_relative_path


def validate_reconstruction(value: object, profile_id: str, semantic_digest: str, runtime_profile: str,
                            entries: tuple, directories: tuple[str, ...]) -> dict[str, Any]:
    """Validate signed identity and payload coverage without inferring missing fields."""
    fields = {"profile_schema_version", "profile", "resolved_mission_root", "mission_template",
              "instance_id", "config_entry", "mission_prefix", "selected_storage_relative",
              "selected_storage_present", "runtime_present"}
    if not isinstance(value, dict) or set(value) != fields or type(value["profile_schema_version"]) is not int or value["profile_schema_version"] != 2:
        raise ValueError("Reconstruction metadata fields are invalid.")
    profile = ProfileInput.parse(value["profile"])
    if profile.profile_id != profile_id or semantic_profile_digest(profile) != semantic_digest or profile.runtime_profile != runtime_profile:
        raise ValueError("Reconstruction profile identity does not match the manifest.")
    root = validate_relative_path(value["resolved_mission_root"], "resolved_mission_root")
    template = value["mission_template"]
    if not isinstance(template, str) or PureWindowsPath(root).name != template or len(PureWindowsPath(template).parts) != 1:
        raise ValueError("Reconstruction mission identity is invalid.")
    if profile.mission_root is not None and profile.mission_root.casefold() != root.casefold():
        raise ValueError("Profile and resolved mission disagree.")
    instance = value["instance_id"]
    if type(instance) is not int or instance <= 0 or value["selected_storage_relative"] != f"storage_{instance}":
        raise ValueError("Reconstruction storage identity is invalid.")
    config = "payload/" + "/".join(PureWindowsPath(profile.server_config).parts)
    prefix = "payload/" + "/".join(PureWindowsPath(root).parts) + "/"
    if value["config_entry"] != config or value["mission_prefix"] != prefix or config not in {entry.path for entry in entries}:
        raise ValueError("Reconstruction payload mapping is invalid.")
    if any(type(value[key]) is not bool for key in ("selected_storage_present", "runtime_present")):
        raise ValueError("Reconstruction presence flags are invalid.")
    if not value["runtime_present"] or "runtime-profile" not in directories:
        raise ValueError("Reconstruction runtime directory is missing.")
    selected = prefix + value["selected_storage_relative"]
    if (selected in directories) != value["selected_storage_present"]:
        raise ValueError("Reconstruction world presence disagrees with inventory.")
    if prefix.rstrip("/") not in directories:
        raise ValueError("Reconstruction mission directory is missing.")
    allowed = lambda path: path == config or path.startswith(prefix) or path.startswith("runtime-profile/")
    if any(not allowed(entry.path) for entry in entries):
        raise ValueError("Reconstruction contains an unrelated payload.")
    ancestors = {"/".join(path.split("/")[:index]) for path in (config, prefix.rstrip("/")) for index in range(1, len(path.split("/")))}
    if any(path not in ancestors and path not in {prefix.rstrip("/"), "runtime-profile"} and not path.startswith(prefix) and not path.startswith("runtime-profile/") for path in directories):
        raise ValueError("Reconstruction contains an unrelated directory.")
    return dict(value)


def reconstruction_metadata(profile: ProfileInput, mission_root: str, configuration: Any, directories: tuple[str, ...]) -> dict[str, Any]:
    """Build complete metadata from a validated captured profile and configuration."""
    prefix = "payload/" + "/".join(PureWindowsPath(mission_root).parts) + "/"
    return {"profile_schema_version": 2, "profile": dict(ProfileRecord(0, profile).fields()),
            "resolved_mission_root": mission_root, "mission_template": configuration.mission_template,
            "instance_id": configuration.instance_id,
            "config_entry": "payload/" + "/".join(PureWindowsPath(profile.server_config).parts),
            "mission_prefix": prefix, "selected_storage_relative": f"storage_{configuration.instance_id}",
            "selected_storage_present": prefix + f"storage_{configuration.instance_id}" in directories,
            "runtime_present": "runtime-profile" in directories}


def validate_directories(directories: object, entries: tuple, normalize: Any, order: Any) -> tuple[str, ...]:
    """Reject case collisions and file ancestors while preserving empty directories."""
    if not isinstance(directories, list):
        raise ValueError("Backup directories must be an array.")
    paths = tuple(normalize(path) for path in directories)
    folded = {path.casefold() for path in paths}
    if len(folded) != len(paths) or list(paths) != sorted(paths, key=order):
        raise ValueError("Backup directories must be sorted and unique.")
    files = {entry.path.casefold() for entry in entries}
    for path in (*paths, *(entry.path for entry in entries)):
        parts = path.casefold().split("/")
        if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
            raise ValueError("Backup file is an ancestor of another entry.")
    if folded & files or any(path.split("/")[0] not in {"payload", "runtime-profile"} for path in paths):
        raise ValueError("Backup directory conflicts with its payload.")
    return paths
