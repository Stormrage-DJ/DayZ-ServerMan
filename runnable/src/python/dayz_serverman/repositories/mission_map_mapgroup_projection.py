"""D3 owned-value projections of the three mapgroup files (leaf 6.8; architecture "Standalone Native Owned
Projections").

Each projection depends only on the relative path and the exact bytes, never
on a plan, a baseline index or a detachment list. It is versioned canonical
JSON (D3). Lists keep multiplicity and are sorted by their canonical form, so
no two entries collapse. An absent file has no projection (None).

- mapgroupproto.xml: each ordinary top-level group with its name and its
  explicit ceiling text, or null. Each container of it has its name and its
  ceiling text, or null. Each reserved group is its complete semantic element,
  medical children included.
- mapgrouppos.xml and mapgroupdirt.xml: the multiset of placement bindings.
  A binding is the exact name, position, rotation (`rpy`) and angle (`a`),
  with finite numbers in shortest round-trip form and null when absent.
"""

from __future__ import annotations

import hashlib
from collections import Counter

from ..domain.medical_zone_rule import is_reserved
from ..domain.mission_map_plan import canonical_json
from .medical_zone_scan import Node
from .mission_map_mapgroup_read import DIRT, POS, PROTO, MapgroupRefused, Placement, parse, read_placements

# Version of the projection form; a change of the form needs a new version
PROJECTION_VERSION = 1


def owned_projection(path: str, data: bytes | None) -> bytes | None:
    """Return the canonical projection of one mapgroup file, or None for an absent or unsupported file.

    A placement file with a binding whose geometry is not finite has no
    projection, so Apply refuses to write it (D3).
    """
    if data is None or path not in (PROTO, POS, DIRT):
        return None
    try:
        content = _prototype(parse(path, data)) if path == PROTO else _bindings(read_placements(path, data)[1])
    except MapgroupRefused:
        return None
    if content is None:
        return None
    return canonical_json({"version": PROJECTION_VERSION, "file": path, **content})


def owned_digest(path: str, data: bytes | None) -> str | None:
    """Return the D3 owned-value digest of one mapgroup file, or None when it has no projection."""
    projection = owned_projection(path, data)
    return None if projection is None else hashlib.sha256(projection).hexdigest()


def _prototype(root: Node) -> dict[str, object]:
    """Return the ordinary ceiling identities and the reserved elements of a prototype."""
    ordinary, reserved = [], []
    for node in (child for child in root.children if child.tag == "group"):
        name = node.attributes.get("name", "")
        if is_reserved(name):
            reserved.append(_semantic(node))
            continue
        containers = [[child.attributes.get("name"), child.attributes.get("lootmax")]
                      for child in node.children if child.tag == "container"]
        ordinary.append([name, node.attributes.get("lootmax"), containers])
    return {"ordinary": _sorted(ordinary), "reserved": _sorted(reserved)}


def _bindings(placements: tuple[Placement, ...]) -> dict[str, object] | None:
    """Return the sorted multiset of placement bindings, or None when one has invalid geometry."""
    if not all(item.geometry_valid for item in placements):
        return None
    counts = Counter((item.name, item.position, item.rotation, item.angle) for item in placements)
    return {"bindings": _sorted([[name, list(position), None if rotation is None else list(rotation), angle, count]
                                 for (name, position, rotation, angle), count in counts.items()])}


def _semantic(node: Node) -> list[object]:
    """Return an element as its tag, its sorted attributes and its children in order."""
    return [node.tag, sorted([key, value] for key, value in node.attributes.items()),
            [_semantic(child) for child in node.children]]


def _sorted(items: list[list[object]]) -> list[list[object]]:
    """Return the entries sorted by their canonical form, keeping every repeated entry."""
    return sorted(items, key=canonical_json)
