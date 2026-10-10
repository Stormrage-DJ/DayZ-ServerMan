"""Editor output ledger entries and the D2 capture-check match rule (D2, D3, D5)."""

from __future__ import annotations

import hashlib
from typing import Any, Sequence

from .mission_map_records import (
    UTC_TIME, RecordShapeError, checked_record, require_exact, require_key, require_match, require_profile,
    require_relative, require_text,
)
from .mission_map_values import IDENTIFIER, SHA256


# Operation kinds that add ledger entries; only an apply and a profile restore record editor output
ENTRY_KINDS = frozenset(("apply", "restore_original", "restore_previous", "profile_restore"))
OUTPUT_KINDS = frozenset(("apply", "profile_restore"))
# Fields of one entry and of one changed file inside it
ENTRY_FIELDS = frozenset(("operation_id", "operation_kind", "recorded_at", "profile_id", "target_class",
                          "target_key", "display_path", "mission_root", "output", "carried_operation_id", "files",
                          "manifest_sha256"))
DIGEST_NAMES = ("before_sha256", "after_sha256", "baseline_sha256",
                "owned_before_sha256", "owned_after_sha256", "owned_baseline_sha256")
FILE_FIELDS = frozenset(("path", *DIGEST_NAMES))
# Prefix of the derived operation ID of an entry that a profile restore adds
RESTORE_ENTRY_PREFIX = "pr-"


def restore_entry_id(restore_operation_id: str, carried_operation_id: str) -> str:
    """Return the ledger operation ID of a carried last-applied record: unique for each restore and record."""
    digest = hashlib.sha256(f"{restore_operation_id}|{carried_operation_id}".encode("utf-8")).hexdigest()
    return RESTORE_ENTRY_PREFIX + digest[:32]


def parse_entry(raw: object, file_set: Sequence[str]) -> dict[str, Any]:
    """Return one ledger entry; it lists only managed files whose whole or owned digest changed."""
    entry = checked_record(raw, ENTRY_FIELDS, "ledger entry")
    require_match(entry["operation_id"], IDENTIFIER, "operation_id")
    if entry["operation_kind"] not in ENTRY_KINDS:
        raise RecordShapeError("ledger operation_kind is not known")
    require_match(entry["recorded_at"], UTC_TIME, "recorded_at")
    require_profile(entry["profile_id"])
    if entry["target_class"] not in ("mission", "runtime"):
        raise RecordShapeError("ledger target_class must be mission or runtime")
    require_key(entry["target_key"])
    require_text(entry["display_path"], "display_path")
    require_relative(entry["mission_root"])
    # The output flag follows the kind; only a profile-restore entry names the carried record
    if entry["output"] is not (entry["operation_kind"] in OUTPUT_KINDS):
        raise RecordShapeError("the output flag is true only for an apply and a profile restore")
    if entry["operation_kind"] == "profile_restore":
        require_match(entry["carried_operation_id"], IDENTIFIER, "carried_operation_id")
    elif entry["carried_operation_id"] is not None:
        raise RecordShapeError("only a profile-restore entry names a carried operation")
    # Each listed file is managed, listed once and changed in its whole or owned digest
    files = entry["files"]
    if not isinstance(files, list) or not files:
        raise RecordShapeError("a ledger entry lists at least one changed file")
    paths = []
    for item in files:
        require_exact(item, FILE_FIELDS, "ledger file")
        if item["path"] not in file_set:
            raise RecordShapeError("a ledger file is not in the managed file set")
        for name in DIGEST_NAMES:
            if item[name] is not None:
                require_match(item[name], SHA256, name)
        if item["after_sha256"] == item["before_sha256"] and item["owned_after_sha256"] == item["owned_before_sha256"]:
            raise RecordShapeError("a ledger file must have changed")
        paths.append(item["path"])
    if len(set(paths)) != len(paths):
        raise RecordShapeError("a ledger entry lists a file twice")
    return entry


def file_entry(entry: dict[str, Any], path: str) -> dict[str, Any] | None:
    """Return the changed-file item of one relative path, or None when the entry did not change it."""
    return next((item for item in entry["files"] if item["path"] == path), None)


def matches(entry: dict[str, Any], path: str, current_sha256: str | None, current_owned_sha256: str | None) -> bool:
    """Return whether a candidate file is editor output that this entry wrote (D2 capture check)."""
    item = file_entry(entry, path)
    # Restore entries, unchanged files, absent files and null digests never match
    if item is None or not entry["output"]:
        return False
    whole = (current_sha256 is not None and current_sha256 == item["after_sha256"]
             and item["after_sha256"] not in (item["before_sha256"], item["baseline_sha256"]))
    owned = (current_owned_sha256 is not None and current_owned_sha256 == item["owned_after_sha256"]
             and item["owned_after_sha256"] not in (item["owned_before_sha256"], item["owned_baseline_sha256"]))
    return whole or owned


def refusal(entry: dict[str, Any], existing_profiles: set[str]) -> dict[str, Any]:
    """Return what a capture refusal reports: the operation, the target folder and the profile if it exists."""
    return {"operation_id": entry["operation_id"], "target_key": entry["target_key"],
            "display_path": entry["display_path"], "mission_root": entry["mission_root"],
            "profile_id": entry["profile_id"] if entry["profile_id"] in existing_profiles else None}
