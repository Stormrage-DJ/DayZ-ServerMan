"""Protected original, working baseline and last-applied record shapes, manifest digests and identities (D3)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from .mission_map_plan import canonical_json
from .mission_map_values import IDENTIFIER, SHA256, TARGET_KEY, TERRAIN_ID, ObjectRuleError, exact_fields, integer
from .profiles import ProfileValidationError, validate_profile_id, validate_relative_path


# UTC time of a record, for example 2026-10-10T12:30:00Z
UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
# Kinds of an operation that writes a last-applied record (D4)
APPLIED_KINDS = frozenset(("apply", "restore_original", "restore_previous"))
# Sources of a working-baseline change: the first apply, adoption of external edits (R17), restores
PROVENANCE_SOURCES = frozenset(("original", "adoption", "restore_original", "profile_restore"))
# Newest provenance entries a baseline keeps
MAX_PROVENANCE = 50
# Largest file size and revision a record may state: the exact JSON integer range
MAX_SAFE_INTEGER = 2**53 - 1
# Field sets of the three records and of their file entries
ORIGINAL_FIELDS = frozenset(("target_class", "target_key", "display_path", "mission_root", "terrain_id",
                             "captured_at", "profile_id", "operation_id", "files", "manifest_sha256"))
BASELINE_FIELDS = frozenset(("profile_id", "mission_key", "mission_root", "target_keys", "originals", "files",
                             "provenance", "manifest_sha256"))
APPLIED_FIELDS = frozenset(("profile_id", "mission_key", "mission_root", "operation_id", "operation_kind",
                            "applied_at", "plan_id", "plan_revision", "configuration_fingerprint", "baseline_revision",
                            "baseline_manifest_sha256", "files_after", "files_before", "excluded_encounters",
                            "manifest_sha256"))
STORED_FILE_FIELDS = frozenset(("target_key", "path", "existed", "sha256", "size"))


class RecordShapeError(ValueError):
    """Raised when a record breaks its D3 shape or its manifest digest does not match."""
    pass


def utc_text(moment: datetime) -> str:
    """Return the record time text of an aware time."""
    if moment.tzinfo is None:
        raise ValueError("record times need an aware time")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest_digest(record: dict[str, Any]) -> str:
    """Return SHA-256 of the canonical JSON of the record without its own manifest digest."""
    return hashlib.sha256(canonical_json({key: value for key, value in record.items()
                                          if key != "manifest_sha256"})).hexdigest()


def sealed(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the record with its manifest digest filled in."""
    content = {key: value for key, value in record.items() if key != "manifest_sha256"}
    return {**content, "manifest_sha256": manifest_digest(content)}


def parse_original(raw: object, file_set: Sequence[str]) -> dict[str, Any]:
    """Return a protected original that captures every file of its class's managed set (D2)."""
    record = checked_record(raw, ORIGINAL_FIELDS, "original")
    if record["target_class"] not in ("mission", "runtime"):
        raise RecordShapeError("original target_class must be mission or runtime")
    require_key(record["target_key"])
    require_text(record["display_path"], "display_path")
    require_relative(record["mission_root"])
    require_match(record["terrain_id"], TERRAIN_ID, "terrain_id")
    require_match(record["captured_at"], UTC_TIME, "captured_at")
    require_profile(record["profile_id"])
    require_match(record["operation_id"], IDENTIFIER, "operation_id")
    # One entry for each managed file, in the order of the set, also for absent files
    files = record["files"]
    if not isinstance(files, list) or [entry.get("path") if isinstance(entry, dict) else None
                                       for entry in files] != list(file_set):
        raise RecordShapeError("original files must list the managed file set in order")
    for entry in files:
        _sized(require_exact(entry, STORED_FILE_FIELDS - {"target_key"}, "file entry"))
    return record


def parse_baseline(raw: object) -> dict[str, Any]:
    """Return a working baseline: file contents of its targets, the originals it was built on and provenance."""
    record = checked_record(raw, BASELINE_FIELDS, "baseline")
    require_profile(record["profile_id"])
    require_key(record["mission_key"])
    require_relative(record["mission_root"])
    # The mission key comes first; a runtime profile key may follow
    keys = record["target_keys"]
    if not isinstance(keys, list) or not 1 <= len(keys) <= 2 or keys[0] != record["mission_key"]:
        raise RecordShapeError("baseline target_keys must start with the mission key and hold at most two keys")
    for key in keys:
        require_key(key)
    for original in _list(record["originals"], "originals"):
        require_exact(original, {"target_key", "manifest_sha256"}, "originals item")
        if original["target_key"] not in keys:
            raise RecordShapeError("baseline originals must name its target keys")
        require_match(original["manifest_sha256"], SHA256, "manifest_sha256")
    for entry in _list(record["files"], "files"):
        _stored_file(entry, keys)
    # Provenance keeps the 50 newest changes
    provenance = _list(record["provenance"], "provenance")
    if len(provenance) > MAX_PROVENANCE:
        raise RecordShapeError(f"baseline provenance holds at most {MAX_PROVENANCE} entries")
    for item in provenance:
        require_exact(item, {"time", "source", "operation_id", "paths"}, "provenance item")
        require_match(item["time"], UTC_TIME, "time")
        if item["source"] not in PROVENANCE_SOURCES:
            raise RecordShapeError("provenance source is not known")
        require_match(item["operation_id"], IDENTIFIER, "operation_id")
        if not all(isinstance(path, str) and path for path in _list(item["paths"], "paths")):
            raise RecordShapeError("provenance paths must be relative path texts")
    return record


def parse_applied(raw: object) -> dict[str, Any]:
    """Return a last-applied record; plan identity and fingerprint are null after a restore."""
    record = checked_record(raw, APPLIED_FIELDS, "applied")
    require_profile(record["profile_id"])
    require_key(record["mission_key"])
    require_relative(record["mission_root"])
    require_match(record["operation_id"], IDENTIFIER, "operation_id")
    if record["operation_kind"] not in APPLIED_KINDS:
        raise RecordShapeError("applied operation_kind is not known")
    require_match(record["applied_at"], UTC_TIME, "applied_at")
    # An apply names its plan revision and fingerprint; a restore names none
    plan_fields = (record["plan_id"], record["plan_revision"], record["configuration_fingerprint"])
    if record["operation_kind"] == "apply":
        require_match(record["plan_id"], IDENTIFIER, "plan_id")
        _integer(record["plan_revision"], "plan_revision")
        require_match(record["configuration_fingerprint"], SHA256, "configuration_fingerprint")
    elif plan_fields != (None, None, None):
        raise RecordShapeError("a restore record has no plan identity and no fingerprint")
    _integer(record["baseline_revision"], "baseline_revision")
    require_match(record["baseline_manifest_sha256"], SHA256, "baseline_manifest_sha256")
    for entry in _list(record["files_after"], "files_after"):
        require_exact(entry, {"target_key", "path", "existed", "sha256", "owned_sha256"}, "files_after item")
        _file_digests(entry, "sha256")
        if entry["owned_sha256"] is not None:
            require_match(entry["owned_sha256"], SHA256, "owned_sha256")
    for entry in _list(record["files_before"], "files_before"):
        _stored_file(entry, None)
    if not all(isinstance(item, str) and IDENTIFIER.fullmatch(item) for item in _list(
            record["excluded_encounters"], "excluded_encounters")):
        raise RecordShapeError("excluded_encounters must list object identifiers")
    return record


def identity(kind: str, record: dict[str, Any], revision: int | None = None) -> dict[str, Any]:
    """Return the reference form that a stored plan's records object holds for one record (D1, D3)."""
    if kind == "original":
        return {"target_key": record["target_key"], "manifest_sha256": record["manifest_sha256"]}
    if kind == "baseline":
        return {"revision": revision, "manifest_sha256": record["manifest_sha256"]}
    return {"operation_id": record["operation_id"], "manifest_sha256": record["manifest_sha256"]}


def stale_references(plan_records: dict[str, Any], originals: list[dict[str, Any]],
                     baseline: dict[str, Any] | None, applied: dict[str, Any] | None) -> list[str]:
    """Return the names of plan references that do not match the stored identities; load asks for a reload."""
    stale = []
    current = {item["target_key"]: item for item in originals}
    for reference in plan_records["originals"]:
        if current.get(reference["target_key"]) != reference:
            stale.append(f"original {reference['target_key']}")
    if plan_records["baseline"] is not None and plan_records["baseline"] != baseline:
        stale.append("baseline")
    if plan_records["applied"] is not None and plan_records["applied"] != applied:
        stale.append("applied")
    return stale


def checked_record(raw: object, fields: frozenset[str], name: str) -> dict[str, Any]:
    """Return a record with exactly its fields and a matching manifest digest."""
    record = require_exact(raw, fields, name)
    require_match(record["manifest_sha256"], SHA256, "manifest_sha256")
    if manifest_digest(record) != record["manifest_sha256"]:
        raise RecordShapeError(f"{name} manifest digest does not match its content")
    return record


def _stored_file(entry: object, keys: Sequence[str] | None) -> None:
    """Check one file entry of a target whose content is stored beside the record."""
    item = require_exact(entry, STORED_FILE_FIELDS, "file entry")
    require_key(item["target_key"])
    if keys is not None and item["target_key"] not in keys:
        raise RecordShapeError("a file entry names a target outside the record")
    _sized(item)


def _sized(item: dict[str, Any]) -> None:
    """Check the digest and size of a stored file: an absent file has neither."""
    _file_digests(item, "sha256")
    if item["existed"]:
        _integer(item["size"], "size")
    elif item["size"] is not None:
        raise RecordShapeError("an absent file has no size")


def _file_digests(item: dict[str, Any], field: str) -> None:
    """Check a relative path and a digest that is present exactly when the file existed (D3)."""
    path = item["path"]
    if not isinstance(path, str) or not path or path.startswith("/") or {"", ".", ".."} & set(path.split("/")):
        raise RecordShapeError("a file path must be relative")
    if not isinstance(item["existed"], bool):
        raise RecordShapeError("existed must be true or false")
    if item["existed"]:
        require_match(item[field], SHA256, field)
    elif item[field] is not None:
        raise RecordShapeError("an absent file has a null digest")


def _guarded(check: Callable[[], object]) -> object:
    """Run one value check and report its failure as a record shape error."""
    try:
        return check()
    except (ObjectRuleError, ProfileValidationError) as error:
        raise RecordShapeError(str(error)) from error


def require_exact(value: object, fields: set[str] | frozenset[str], name: str) -> dict[str, Any]:
    """Return an object with exactly the fields."""
    return _guarded(lambda: exact_fields(value, name, fields))  # type: ignore[return-value]


def require_match(value: object, pattern: re.Pattern[str], field: str) -> None:
    """Check that a text fully matches the pattern."""
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise RecordShapeError(f"{field} has an invalid value")


def require_key(value: object) -> None:
    """Check a target key."""
    require_match(value, TARGET_KEY, "target key")


def require_profile(value: object) -> None:
    """Check a stable profile ID."""
    _guarded(lambda: validate_profile_id(value))


def require_relative(value: object) -> None:
    """Check a mission root relative to the DayZ root."""
    _guarded(lambda: validate_relative_path(value, "mission_root"))


def require_text(value: object, field: str) -> None:
    """Check a non-empty text."""
    if not isinstance(value, str) or not value:
        raise RecordShapeError(f"{field} must be non-empty text")


def _integer(value: object, field: str) -> None:
    """Check a non-negative JSON integer."""
    _guarded(lambda: integer(value, field, 0, MAX_SAFE_INTEGER))


def _list(value: object, field: str) -> list[Any]:
    """Return a JSON list."""
    if not isinstance(value, list):
        raise RecordShapeError(f"{field} must be a list")
    return value
