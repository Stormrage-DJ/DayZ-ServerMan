"""The restore point and the working baseline of one Apply commit (8.7, R16, R17, D3).

Architect ruling of 2026-10-15 01:18:47 in evidence 8.7 (architecture
"Apply Records and Replaced Versions"):

- Restore point. "Files before" of the new last-applied record lists the
  journal groups with content. A commit without a group whose "files after"
  equal those of the current record keeps the current "files before" and
  `generated_groups_before`: it changes only plan fields. A commit without a
  group but with other "files after" (an incorporation only) has an empty
  "files before", because the previous state equals the current files.
- `generated_groups_before` is the `generated_groups` of the record that the
  commit replaces. It is `[]` when the commit captures the original (D8
  refuses reserved elements there), and null when it is unknown.
- Working baseline. Created from the original at the first Apply. An
  incorporation changes the incorporated file entries and adds an `adoption`
  entry. A Replace that overwrites a file adds a `replaced` entry with the
  stored items of the external versions. Their content goes to the
  association content store. The 50 newest entries stay.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..domain.mission_map_applied import GROUPS_BEFORE_FIELD, GROUPS_FIELD, REPLACED_FILES, REPLACED_SOURCE
from ..domain.mission_map_records import MAX_PROVENANCE, sealed
from ..repositories.mission_map_contents import ContentStore
from ..repositories.mission_map_layout import MISSION_FILE_SET, association_folder
from .mission_map_apply_choices import Choices
from .mission_map_preview import ReviewContext


def restore_point(context: ReviewContext, changed: list[str], after: list[dict[str, Any]],
                  current: dict[str, bytes | None], area: Path) -> tuple[list[dict[str, Any]], dict[str, bytes], Any]:
    """Return "files before", their content and the realization of that state."""
    key = context.association["mission_key"]
    previous = None if context.records.applied is None else context.records.applied[0]
    if not changed and previous is not None and previous["files_after"] == after:
        # Only plan fields change, so the restore point of the current record stays valid
        kept = previous["files_before"]
        store = ContentStore(association_folder(area, context.association["profile_id"], key))
        contents = {item["sha256"]: store.read(item["sha256"]) for item in kept if item["existed"]}
        return kept, contents, previous.get(GROUPS_BEFORE_FIELD)
    if previous is None:
        before = [] if context.records.original is None else None
    else:
        # A version 1 record has no realization, so it is unknown
        before = previous.get(GROUPS_FIELD)
    files = [dict(stored(path, current[path]), target_key=key) for path in changed]
    return files, contents_of(current[path] for path in changed), before


def working_baseline(context: ReviewContext, original: dict[str, Any], base: dict[str, bytes | None],
                     choices: Choices, replaced: list[str], current: dict[str, bytes | None], operation_id: str,
                     now: str, area: Path) -> tuple[dict[str, Any], int, int | None, dict[str, bytes]]:
    """Return the baseline record, its revision after the commit, the revision read before it and its content."""
    key = context.association["mission_key"]
    entries = []
    if choices.incorporated:
        entries.append({"time": now, "source": "adoption", "operation_id": operation_id,
                        "paths": choices.incorporated})
    if replaced:
        # The external versions that the groups overwrite are kept with their content (R17)
        entries.append({"time": now, "source": REPLACED_SOURCE, "operation_id": operation_id, "paths": replaced,
                        REPLACED_FILES: [dict(stored(path, current[path]), target_key=key) for path in replaced]})
    contents = contents_of(base.values())
    contents.update(contents_of(current[path] for path in replaced))
    found = context.records.baseline
    if found is None:
        provenance = [{"time": now, "source": "original", "operation_id": operation_id,
                       "paths": list(MISSION_FILE_SET)}] + entries
        record = {"profile_id": context.association["profile_id"], "mission_key": key,
                  "mission_root": context.association["mission_root"], "target_keys": [key],
                  "originals": [{"target_key": key, "manifest_sha256": original["manifest_sha256"]}],
                  "files": [dict(stored(path, base[path]), target_key=key) for path in MISSION_FILE_SET],
                  "provenance": provenance[-MAX_PROVENANCE:]}
        return sealed(record), 0, None, contents
    record, revision = found
    # The content of earlier Replace backups stays referenced by the new record
    store = ContentStore(association_folder(area, context.association["profile_id"], key))
    for entry in record["provenance"]:
        contents.update({item["sha256"]: store.read(item["sha256"])
                         for item in entry.get(REPLACED_FILES, []) if item["existed"]})
    if not entries:
        return record, revision, revision, contents
    files = [dict(stored(item["path"], base[item["path"]]), target_key=key)
             if item["target_key"] == key and item["path"] in choices.incorporated else item for item in record["files"]]
    provenance = (record["provenance"] + entries)[-MAX_PROVENANCE:]
    content = {name: value for name, value in record.items() if name != "manifest_sha256"}
    return sealed(dict(content, files=files, provenance=provenance)), revision + 1, revision, contents


def stored(path: str, data: bytes | None) -> dict[str, Any]:
    """Return one stored file entry without a target key: existence, digest and size."""
    return {"path": path, "existed": data is not None, "sha256": sha(data), "size": None if data is None else len(data)}


def contents_of(values: Any) -> dict[str, bytes]:
    """Return the content of every present file by its digest."""
    return {hashlib.sha256(data).hexdigest(): data for data in values if data is not None}


def sha(data: bytes | None) -> str | None:
    """Return SHA-256 of the bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()
