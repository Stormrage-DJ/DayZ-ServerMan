"""Durable type-specific journal for joint files and profile publication."""

import json
import os
import re
from pathlib import Path
from typing import Any

from ..domain.backups import SHA256
from ..domain.profiles import validate_relative_path
from ..domain.profiles import ProfileInput, validate_profile_id
from .profile_restore_inventory import safe_exists

PHASES = {"PLANNED", "PREPARING", "PREPARED", "PUBLISHING", "FILES_PUBLISHED",
          "PROFILE_PUBLISHING", "PROFILE_PUBLISHED", "COMMITTED", "COMPENSATING", "ROLLED_BACK", "RECOVERY_REQUIRED"}


class ProfileRestoreJournal:
    """Store recoverable transaction documents under a distinct journal root."""

    def __init__(self, root: Path) -> None:
        """Bind the journal directory without creating filesystem state."""
        self.root = root

    def write(self, record: dict[str, Any]) -> None:
        """Flush a complete journal before replacing its prior phase atomically."""
        validate_record(record)
        safe_exists(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / (record["operation_id"] + ".json")
        temporary = path.with_suffix(".pending")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def records(self) -> list[dict[str, Any]]:
        """Read strict documents and reject unaccounted interrupted first writes."""
        if not safe_exists(self.root):
            return []
        result = []
        for path in self.root.glob("*.pending"):
            if not path.with_suffix(".json").exists():
                raise ValueError("Interrupted restore journal requires recovery.")
        for path in sorted(self.root.glob("*.json")):
            safe_exists(path)
            record = json.loads(path.read_text(encoding="utf-8"))
            validate_record(record)
            if path.stem != record["operation_id"]:
                raise ValueError("Restore journal identity is inconsistent.")
            result.append(record)
        return result


def validate_record(record: object) -> None:
    """Reject unknown versions, fields and malformed group roles or paths."""
    fields = {"type", "version", "operation_id", "phase", "dayz_root", "profile_root", "profile_id", "groups", "result", "cleanup_complete"}
    if not isinstance(record, dict) or set(record) != fields or record["type"] != "DIRECT_PROFILE_RESTORE" or type(record["version"]) is not int or record["version"] != 1:
        raise ValueError("Unsupported direct restore journal.")
    if not isinstance(record["operation_id"], str) or re.fullmatch(r"[a-zA-Z0-9-]{1,64}", record["operation_id"]) is None or record["phase"] not in PHASES:
        raise ValueError("Invalid restore journal identity or phase.")
    if not isinstance(record["groups"], list) or len(record["groups"]) != 3:
        raise ValueError("Invalid restore publication groups.")
    identifier = validate_profile_id(record["profile_id"])
    if type(record["cleanup_complete"]) is not bool or not isinstance(record["result"], dict):
        raise ValueError("Invalid restore cleanup or result record.")
    fields = dict(record["result"]["profile"])
    revision = fields.pop("revision")
    if type(revision) is not int or revision != 0:
        raise ValueError("Restored record must have revision zero.")
    fields.pop("semantic_digest")
    profile = ProfileInput.parse(fields)
    if profile.profile_id != identifier or profile.server_config != f"serverman\\{identifier}\\serverDZ.cfg" or profile.runtime_profile != f"serverman\\{identifier}\\profile":
        raise ValueError("Restore journal profile ownership differs.")
    mission = record["result"]["mission_root"]
    if profile.mission_root != mission or type(record["result"]["instance_id"]) is not int or record["result"]["instance_id"] <= 0:
        raise ValueError("Restore journal mission identity differs.")
    policy = record["result"]["storage_policy"]
    if policy not in {"preserve_original", "allocate_new", "replace_existing"}:
        raise ValueError("Restore journal storage policy differs.")
    expected_targets = (f"serverman\\{identifier}", mission + (f"\\storage_{record['result']['instance_id']}" if policy == "replace_existing" else ""), f"{identifier}.json")
    for group, role in zip(record["groups"], ("generated", "mission", "profile")):
        if not isinstance(group, dict) or set(group) != {"role", "relative", "before", "after", "published", "held", "recovery"} or group["role"] != role:
            raise ValueError("Invalid restore publication group.")
        validate_relative_path(group["relative"], "restore journal target")
        if type(group["published"]) is not bool or type(group["held"]) is not bool:
            raise ValueError("Invalid restore publication state.")
        if group["relative"] != expected_targets[("generated", "mission", "profile").index(role)]:
            raise ValueError("Restore journal target ownership differs.")
        if group["before"] is not None and (role != "mission" or policy != "replace_existing"):
            raise ValueError("Restore journal attempts to replace an unowned target.")
        for snapshot in (group["before"], group["after"]):
            validate_inventory(snapshot)


def validate_inventory(value: object) -> None:
    """Validate recovery hashes and paths before any compensation can consume them."""
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("Restore inventory is invalid.")
    if value.get("type") == "file":
        if set(value) != {"type", "sha256", "size"} or type(value["size"]) is not int or value["size"] < 0 or not isinstance(value["sha256"], str) or SHA256.fullmatch(value["sha256"]) is None:
            raise ValueError("Restore file inventory is invalid.")
        return
    if value.get("type") != "directory" or set(value) != {"type", "directories", "files"} or not isinstance(value["directories"], list) or not isinstance(value["files"], dict):
        raise ValueError("Restore tree inventory is invalid.")
    seen = set()
    for path in (*value["directories"], *value["files"]):
        validate_relative_path(path, "restore inventory")
        if path.casefold() in seen:
            raise ValueError("Restore inventory path collision.")
        seen.add(path.casefold())
    for item in value["files"].values():
        if not isinstance(item, dict) or set(item) != {"sha256", "size"}:
            raise ValueError("Restore file digest fields are invalid.")
        validate_inventory({"type": "file", **item})
