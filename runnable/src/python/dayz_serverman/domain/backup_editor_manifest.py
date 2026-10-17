"""The `editor` field of a schema 4 backup manifest and the layout of its `editor/` entries (D18, 10.9)."""

from __future__ import annotations

from typing import Any, Iterable

from .backup_entries import SHA256, BackupManifestError, ManifestEntry
from .mission_map_values import TARGET_KEY
from .profiles import ProfileValidationError, validate_relative_path


# Manifest schema that carries editor records, and the version of its `editor` field
EDITOR_SCHEMA_VERSION = 4
EDITOR_FIELD_VERSION = 1
# First segment of every editor entry; an older restore mapper knows only `payload` and `runtime-profile`
EDITOR_PREFIX = "editor"
ASSOCIATION_PREFIX = "editor/association/"
ORIGINALS_PREFIX = "editor/originals/"
# Record kinds of one association, in their fixed order
RECORD_KINDS = ("plan", "baseline", "applied")
# Fields of one `originals` item; each one comes from the carried original record
ORIGINAL_ITEM_FIELDS = ("target_class", "target_key", "manifest_sha256", "display_path", "mission_root")
TARGET_CLASSES = ("mission", "runtime")


def is_editor_entry(path: str) -> bool:
    """Return whether an entry path is below the editor prefix, in any letter case."""
    return path.split("/", 1)[0].casefold() == EDITOR_PREFIX


def record_entry(kind: str) -> str:
    """Return the entry path of one association record."""
    return f"{ASSOCIATION_PREFIX}{kind}.json"


def association_content(digest: str) -> str:
    """Return the entry path of one content file of the association."""
    return f"{ASSOCIATION_PREFIX}contents/{digest}"


def original_entry(key: str) -> str:
    """Return the entry path of the original record of one target key."""
    return f"{ORIGINALS_PREFIX}{key}/original.json"


def original_content(key: str, digest: str) -> str:
    """Return the entry path of one content file of the original of one target key."""
    return f"{ORIGINALS_PREFIX}{key}/contents/{digest}"


def editor_field(profile_id: str, mission_key: str, mission_root: str, records: Iterable[str],
                 originals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return the `editor` field: the listed record kinds and one item for each carried original."""
    return {"version": EDITOR_FIELD_VERSION, "profile_id": profile_id,
            "association": {"mission_key": mission_key, "mission_root": mission_root, "records": list(records)},
            "originals": sorted(({name: item[name] for name in ORIGINAL_ITEM_FIELDS} for item in originals),
                                key=lambda item: item["target_key"])}


def parse_editor_field(value: object, profile_id: object, entries: tuple[ManifestEntry, ...]) -> dict[str, Any]:
    """Return a copy of a valid `editor` field whose entries match its layout; raise BackupManifestError.

    The record bytes are checked against the field when the archive is verified (`backup_editor_records.py`).
    """
    field = _exact(value, {"version", "profile_id", "association", "originals"}, "editor")
    if field["version"] != EDITOR_FIELD_VERSION or isinstance(field["version"], bool):
        raise BackupManifestError("editor field version is unsupported")
    if field["profile_id"] != profile_id:
        raise BackupManifestError("editor records name another profile")
    association = _exact(field["association"], {"mission_key", "mission_root", "records"}, "editor association")
    mission_key = _key(association["mission_key"])
    mission_root = _relative(association["mission_root"])
    records = association["records"]
    # At least one record, each kind once and in the fixed order
    if not isinstance(records, list) or not records or records != [kind for kind in RECORD_KINDS if kind in records]:
        raise BackupManifestError("editor records must list plan, baseline or applied once each, in order")
    originals = [_original(item, mission_key) for item in _list(field["originals"])]
    keys = [item["target_key"] for item in originals]
    classes = [item["target_class"] for item in originals]
    if keys != sorted(set(keys)) or len(classes) != len(set(classes)):
        raise BackupManifestError("editor originals must be sorted with one target of each class")
    _check_entries(records, keys, entries)
    return {"version": EDITOR_FIELD_VERSION, "profile_id": field["profile_id"],
            "association": {"mission_key": mission_key, "mission_root": mission_root, "records": list(records)},
            "originals": originals}


def _check_entries(records: list[str], keys: list[str], entries: tuple[ManifestEntry, ...]) -> None:
    """Require one entry for each listed record, and only content entries of a carried folder besides them."""
    expected = {record_entry(kind) for kind in records} | {original_entry(key) for key in keys}
    folders = {ASSOCIATION_PREFIX + "contents/", *(f"{ORIGINALS_PREFIX}{key}/contents/" for key in keys)}
    found = set()
    for entry in entries:
        if not is_editor_entry(entry.path):
            continue
        if entry.path in expected:
            found.add(entry.path)
            continue
        # A content entry is named by its digest, so its name proves its bytes
        folder, _, name = entry.path.rpartition("/")
        if folder + "/" not in folders or SHA256.fullmatch(name) is None or entry.sha256 != name:
            raise BackupManifestError("an editor entry belongs to no listed record")
    if found != expected:
        raise BackupManifestError("a listed editor record has no entry")


def _original(value: object, mission_key: str) -> dict[str, Any]:
    """Return one valid `originals` item; the mission original belongs to the association's mission key."""
    item = _exact(value, set(ORIGINAL_ITEM_FIELDS), "editor original")
    if item["target_class"] not in TARGET_CLASSES:
        raise BackupManifestError("editor original target class is invalid")
    key = _key(item["target_key"])
    if item["target_class"] == "mission" and key != mission_key:
        raise BackupManifestError("the mission original names another mission key")
    if not isinstance(item["manifest_sha256"], str) or SHA256.fullmatch(item["manifest_sha256"]) is None:
        raise BackupManifestError("editor original digest is invalid")
    if not isinstance(item["display_path"], str) or not item["display_path"]:
        raise BackupManifestError("editor original display path is invalid")
    return {**{name: item[name] for name in ORIGINAL_ITEM_FIELDS}, "mission_root": _relative(item["mission_root"])}


def _exact(value: object, fields: set[str], name: str) -> dict[str, Any]:
    """Return a mapping that has exactly the given fields."""
    if not isinstance(value, dict) or set(value) != fields:
        raise BackupManifestError(f"{name} fields are invalid")
    return value


def _list(value: object) -> list[Any]:
    """Return a list value, or raise."""
    if not isinstance(value, list):
        raise BackupManifestError("editor originals must be a list")
    return value


def _key(value: object) -> str:
    """Return a target key: 32 lowercase hexadecimal digits."""
    if not isinstance(value, str) or TARGET_KEY.fullmatch(value) is None:
        raise BackupManifestError("editor target key is invalid")
    return value


def _relative(value: object) -> str:
    """Return a mission root relative to the DayZ root, unchanged."""
    try:
        validate_relative_path(value, "mission_root")
    except ProfileValidationError as error:
        raise BackupManifestError("editor mission root is invalid") from error
    return value
