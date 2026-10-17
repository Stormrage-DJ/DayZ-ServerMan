"""Protected original, working baseline and last-applied record shapes, manifest digests and identities (D3)."""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Sequence

from .medical_editor_texts import MEDICAL_KIND
from .mission_map_applied import (
    APPLIED_VERSION, GROUPS_FIELD, REALIZATION_FIELDS, REPLACED_FILES, REPLACED_SOURCE, RealizationError,
    check_realization, record_version,
)
from .mission_map_plan import canonical_json
# The field checks and the shape error live in their own module (10.3); they are re-exported for existing importers
from .mission_map_record_checks import (  # noqa: F401
    MAX_SAFE_INTEGER, RecordShapeError, _integer, _list, require_exact, require_key, require_match, require_profile,
    require_relative, require_text,
)
from .mission_map_values import IDENTIFIER, SHA256, TERRAIN_ID


# UTC time of a record, for example 2026-10-10T12:30:00Z
UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
# Kinds of an operation that writes a last-applied record (D4); `medical` is the 8.13 zone toggle
APPLIED_KINDS = frozenset(("apply", "restore_original", "restore_previous", MEDICAL_KIND))
# Sources of a working-baseline change: the first apply, adoption of external edits (R17), a Replace backup
# (Architect 2026-10-15 01:18:47 in evidence 8.7), restores
PROVENANCE_SOURCES = frozenset(("original", "adoption", REPLACED_SOURCE, "restore_original", "profile_restore"))
# Newest provenance entries a baseline keeps
MAX_PROVENANCE = 50
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
# Field of the provenance-linked original of a new key, its fields and its adoption cases (10.2 OP-3, 10.8)
PROVENANCE_FIELD = "provenance"
PROVENANCE_FIELDS = frozenset(("case", "source_key", "source_manifest_sha256", "operation_id"))
# A restore as a new profile adds `restore`: its source is the verified archive entry (ruling (c), 10.37)
COPY_CASES = frozenset(("move", "copy", "restore"))
# Fields that the original of a new key takes from its source unchanged; the new key gives the others
COPIED_FIELDS = ("target_class", "terrain_id", "captured_at", "profile_id", "operation_id", "files")


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
    """Return a protected original that captures every file of its class's managed set (D2).

    The original of a new key also holds its provenance: the source key, the source manifest digest and the
    adoption operation ID (10.8).
    """
    linked = isinstance(raw, dict) and PROVENANCE_FIELD in raw
    record = checked_record(raw, ORIGINAL_FIELDS | ({PROVENANCE_FIELD} if linked else set()), "original")
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
    if linked:
        source = require_exact(record[PROVENANCE_FIELD], PROVENANCE_FIELDS, "provenance")
        if source["case"] not in COPY_CASES:
            raise RecordShapeError("original provenance case must be move, copy or restore")
        require_key(source["source_key"])
        # A copy always names another key; a backup original for the same key is a plain original (10.37)
        if source["source_key"] == record["target_key"]:
            raise RecordShapeError("original provenance names another target key")
        require_match(source["source_manifest_sha256"], SHA256, "source_manifest_sha256")
        require_match(source["operation_id"], IDENTIFIER, "operation_id")
    return record


def copied_original(source: dict[str, Any], *, target_key: str, display_path: str, mission_root: str, case: str,
                    operation_id: str) -> dict[str, Any]:
    """Return the sealed original of a new key with the content of a source original and its provenance (10.8).

    The source original stays in place. A copy of a copy names its direct source.
    """
    content = {name: copy.deepcopy(source[name]) for name in COPIED_FIELDS}
    link = {"case": case, "source_key": source["target_key"], "source_manifest_sha256": source["manifest_sha256"],
            "operation_id": operation_id}
    return sealed({**content, "target_key": target_key, "display_path": display_path, "mission_root": mission_root,
                   PROVENANCE_FIELD: link})


def copy_matches(record: dict[str, Any], source: dict[str, Any]) -> bool:
    """Return whether an original of a new key holds the content of the source original that its provenance names."""
    link = record.get(PROVENANCE_FIELD)
    named = None if link is None else (link["source_key"], link["source_manifest_sha256"])
    return (named == (source["target_key"], source["manifest_sha256"])
            and all(record[name] == source[name] for name in COPIED_FIELDS))


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
        # A Replace backup also lists the stored file items of the versions it keeps, with content
        backup = isinstance(item, dict) and item.get("source") == REPLACED_SOURCE
        require_exact(item, {"time", "source", "operation_id", "paths"} | ({REPLACED_FILES} if backup else set()),
                      "provenance item")
        require_match(item["time"], UTC_TIME, "time")
        if item["source"] not in PROVENANCE_SOURCES:
            raise RecordShapeError("provenance source is not known")
        require_match(item["operation_id"], IDENTIFIER, "operation_id")
        if not all(isinstance(path, str) and path for path in _list(item["paths"], "paths")):
            raise RecordShapeError("provenance paths must be relative path texts")
        if backup:
            for entry in _list(item[REPLACED_FILES], REPLACED_FILES):
                _stored_file(entry, keys)
            if [entry["path"] for entry in item[REPLACED_FILES]] != item["paths"]:
                raise RecordShapeError("a replaced entry lists the paths of its files")
    return record


def parse_applied(raw: object) -> dict[str, Any]:
    """Return a last-applied record of version 1 or 2; plan identity and fingerprint are null after a restore.

    Version 2 adds the applied plan and the realization (8.7); its fields decide the version.
    """
    version = record_version(raw)
    record = checked_record(raw, APPLIED_FIELDS | (REALIZATION_FIELDS if version == APPLIED_VERSION else set()),
                            "applied")
    # A medical toggle keeps the plan of the record that it replaces, which is none after a restore (8.13)
    medical = record["operation_kind"] == MEDICAL_KIND
    planned = record["operation_kind"] == "apply" or (medical and record["plan_id"] is not None)
    if version == APPLIED_VERSION:
        try:
            check_realization(record, apply=planned)
        except RealizationError as error:
            raise RecordShapeError(str(error)) from error
    # The toggle needs a known realization, so its record always names one
    if medical and (version != APPLIED_VERSION or record[GROUPS_FIELD] is None):
        raise RecordShapeError("a medical toggle record names its generated groups")
    require_profile(record["profile_id"])
    require_key(record["mission_key"])
    require_relative(record["mission_root"])
    require_match(record["operation_id"], IDENTIFIER, "operation_id")
    if record["operation_kind"] not in APPLIED_KINDS:
        raise RecordShapeError("applied operation_kind is not known")
    require_match(record["applied_at"], UTC_TIME, "applied_at")
    # An apply names its plan revision and fingerprint; a restore names none
    plan_fields = (record["plan_id"], record["plan_revision"], record["configuration_fingerprint"])
    if planned:
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
