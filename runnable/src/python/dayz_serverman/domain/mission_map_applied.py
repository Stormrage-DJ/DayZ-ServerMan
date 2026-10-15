"""Fields of the last-applied record version 2: the applied plan and the committed realization (8.7, D3).

Version 1 records stay readable as they are. They have none of these fields,
so the preview lists every current object as added and the loot report gets
no current generated groups (6.9 ruling of 2026-10-13 15:18:59). Version 2
adds three fields:

- `global_abundance`: the global value of the applied plan; null after a restore.
- `plan_objects`: the loot and encounter objects of the applied plan, in plan
  order, as the plan stores them; null after a restore.
- `generated_groups`: the realization of each generated group, sorted by name
  (D3 "Native Realization in the Applied Record"); null only for a restore
  whose realization is unknown.
- `generated_groups_before`: the realization of the "files before" state;
  null when it is unknown (Architect 2026-10-15 01:18:47 in evidence 8.7).

A working baseline is version 2 when it has a provenance entry of source
`replaced`. That entry lists the external versions that a Replace overwrote.

The envelope `schema_version` of a stored record is its version. A record is
never converted; the next commit replaces it.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from .mission_map_plan import canonical_json
from .mission_map_values import GLOBAL_ABUNDANCE_STEPS, IDENTIFIER

# Envelope version of a last-applied record with the realization fields
APPLIED_VERSION = 2
# Field names of version 2; the preview reads them through domain/mission_map_preview_changes.py
GLOBAL_FIELD = "global_abundance"
OBJECTS_FIELD = "plan_objects"
GROUPS_FIELD = "generated_groups"
GROUPS_BEFORE_FIELD = "generated_groups_before"
REALIZATION_FIELDS = frozenset((GLOBAL_FIELD, OBJECTS_FIELD, GROUPS_FIELD, GROUPS_BEFORE_FIELD))
# Provenance source of a Replace backup and the field that lists its stored file items (baseline version 2)
REPLACED_SOURCE, REPLACED_FILES = "replaced", "files"
# Fields of one generated group and of one detached placement in it
GROUP_FIELDS = frozenset(("name", "key", "source", "containers", "group_ceiling", "container_ceilings", "detached"))
DETACHED_FIELDS = frozenset(("file", "name", "position", "rotation", "angle", "multiplicity"))
# The file that a detached placement comes from: only mapgrouppos.xml placements move (6.8)
DETACHED_ORIGIN = "mapgrouppos.xml"
# Name of a generated group (D8): prefix and the first 12 hexadecimal digits of the key digest
GENERATED_NAME = re.compile(r"ServerManMap_G_[0-9a-f]{12}")
# D8 key version 1 and its role
KEY_VERSION, KEY_ROLE = 1, "native_capacity"
# A canonical decimal ceiling: no sign, no leading zero
CEILING = re.compile(r"0|[1-9][0-9]*", re.ASCII)


class RealizationError(ValueError):
    """Raised when the version 2 fields of a last-applied record break their shape."""


def record_version(record: object) -> int:
    """Return the version of a manager record from its fields.

    It is 2 for an applied record with the realization fields, and for a
    working baseline with a provenance entry that lists stored files.
    """
    if not isinstance(record, dict):
        return 1
    provenance = record.get("provenance")
    replaced = isinstance(provenance, list) and any(
        isinstance(item, dict) and REPLACED_FILES in item for item in provenance)
    return APPLIED_VERSION if REALIZATION_FIELDS & set(record) or replaced else 1


def check_realization(record: dict[str, Any], *, apply: bool) -> None:
    """Check the version 2 fields; an apply names its plan, a restore names none (null values)."""
    value, objects = record[GLOBAL_FIELD], record[OBJECTS_FIELD]
    if apply:
        if isinstance(value, bool) or value not in GLOBAL_ABUNDANCE_STEPS:
            raise RealizationError("global_abundance must be a global step")
        if not isinstance(objects, list) or not all(isinstance(item, dict) for item in objects):
            raise RealizationError("plan_objects must list plan objects")
        identifiers = [item.get("id") for item in objects]
        if not all(isinstance(item, str) and IDENTIFIER.fullmatch(item) for item in identifiers) or len(
                set(identifiers)) != len(identifiers):
            raise RealizationError("plan_objects must have unique object identifiers")
    elif (value, objects) != (None, None):
        raise RealizationError("a restore record has no plan objects and no global value")
    # Null means unknown: allowed for the groups of a restore and for the groups before any commit
    if record[GROUPS_FIELD] is not None or apply:
        _groups(record[GROUPS_FIELD], GROUPS_FIELD)
    if record[GROUPS_BEFORE_FIELD] is not None:
        _groups(record[GROUPS_BEFORE_FIELD], GROUPS_BEFORE_FIELD)


def _groups(groups: object, field: str) -> None:
    """Check one realization: generated groups sorted by unique name."""
    if not isinstance(groups, list):
        raise RealizationError(f"{field} must be a list")
    names = [_group(item) for item in groups]
    if names != sorted(set(names)):
        raise RealizationError(f"{field} must be sorted by unique name")


def generated_name(key: list[object]) -> str:
    """Return the D8 name of a key: the same rule as the 6.8 builder."""
    return "ServerManMap_G_" + hashlib.sha256(canonical_json(key)).hexdigest()[:12]


def _group(item: object) -> str:
    """Check one generated group and return its name; the derived fields must equal its key."""
    if not isinstance(item, dict) or set(item) != GROUP_FIELDS:
        raise RealizationError("a generated group has other fields")
    name, key = item["name"], item["key"]
    if not isinstance(name, str) or GENERATED_NAME.fullmatch(name) is None:
        raise RealizationError("a generated group has an invalid name")
    if not isinstance(key, list) or len(key) != 6 or key[:2] != [KEY_VERSION, KEY_ROLE]:
        raise RealizationError("a generated group has an invalid key")
    if generated_name(key) != name:
        raise RealizationError("a generated group name differs from its key")
    source, containers = item["source"], item["containers"]
    if not (isinstance(source, list) and len(source) == 2 and all(_text(part) for part in source)):
        raise RealizationError("a generated group has an invalid source")
    if not (isinstance(containers, list) and all(_text(part) for part in containers)):
        raise RealizationError("a generated group has invalid containers")
    ceilings = item["container_ceilings"]
    if not (isinstance(ceilings, list) and len(ceilings) == len(containers)
            and all(_ceiling(value) for value in [item["group_ceiling"], *ceilings])):
        raise RealizationError("a generated group has invalid ceilings")
    if key[2:] != [source, containers, item["group_ceiling"], ceilings]:
        raise RealizationError("a generated group differs from its key")
    detached = item["detached"]
    if not isinstance(detached, list):
        raise RealizationError("detached placements must be a list")
    for binding in detached:
        _detached(binding)
    return name


def _detached(binding: object) -> None:
    """Check one detached placement: origin file, name, finite geometry and a positive multiplicity."""
    if not isinstance(binding, dict) or set(binding) != DETACHED_FIELDS:
        raise RealizationError("a detached placement has other fields")
    if binding["file"] != DETACHED_ORIGIN or not _text(binding["name"]):
        raise RealizationError("a detached placement has an invalid origin")
    rotation, angle, count = binding["rotation"], binding["angle"], binding["multiplicity"]
    if not _vector(binding["position"]) or not (rotation is None or _vector(rotation)):
        raise RealizationError("a detached placement has invalid geometry")
    if not (angle is None or _number(angle)):
        raise RealizationError("a detached placement has an invalid angle")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise RealizationError("a detached placement has an invalid multiplicity")


def _vector(value: object) -> bool:
    """Return whether a value is three finite numbers."""
    return isinstance(value, list) and len(value) == 3 and all(_number(part) for part in value)


def _number(value: object) -> bool:
    """Return whether a value is a finite JSON number and not a boolean."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _ceiling(value: object) -> bool:
    """Return whether a value is a canonical decimal ceiling text."""
    return isinstance(value, str) and CEILING.fullmatch(value) is not None


def _text(value: object) -> bool:
    """Return whether a value is non-empty text."""
    return isinstance(value, str) and bool(value)


def content_items(kind: str, record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the stored file items whose content lives beside a record; the content sweep keeps them (D2).

    An original and a baseline store their files, and a baseline also the
    versions that its `replaced` provenance entries keep. A last-applied
    record stores its "files before". A ledger entry stores no content.
    """
    if kind == "applied":
        return list(record.get("files_before", []))
    if kind not in ("original", "baseline"):
        return []
    items = list(record["files"])
    for entry in record.get("provenance", []) if kind == "baseline" else []:
        items += entry.get(REPLACED_FILES, [])
    return items
