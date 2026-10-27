"""Medical children of the map editor's generated loot groups for a loot-zone toggle (leaf 8.13).

On an editor-managed mission the zone toggle also regenerates the medical
children of every `ServerManMap_G_` group. The 6.8 builder made each such
group from its ordinary source group with the 6.3 clone rule. The toggle
reads only the realization of the current applied record: the name, the
source group identity, the container identities and the ceilings.

Before it changes a group, the toggle builds that group again with the medical
state before the toggle. The bytes must equal the group in the file, so a file
whose generated groups differ from the realization refuses. The new group bytes
equal what Apply would write with the new medical state (clone parity).
Capacity, routing, keys and names never change; placements are not read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.medical_zone_rule import is_reserved
from .mission_map_mapgroup_clone import CloneSourceError, clone_children, clone_element
from .mission_map_mapgroup_read import PROTO
from .mission_map_mapgroup_splice import LayoutError, indent_of, newline_of
from .medical_zone_scan import MIXED_LINES, Node, layout_error, scan_document, scan_groups

# Reasons of a file whose generated groups differ from the realization in the applied record (R17)
NAMES_DIFFER = "generated_names_differ"
SOURCE_DIFFERS = "generated_source_differs"
GROUP_DIFFERS = "generated_group_differs"


class CloneMismatch(ValueError):
    """Raised when the generated groups of the file differ from the realization of the applied record."""

    def __init__(self, reason: str, detail: str) -> None:
        """Keep the reason code; the detail names the group for the diagnostics."""
        super().__init__(detail)
        self.reason = reason


def regenerate_clones(current: bytes, patched: bytes, medical: bytes, groups: Sequence[Mapping[str, Any]],
                      old: bool, new: bool) -> bytes:
    """Return `patched` with the medical children of every generated group in the new state.

    current is the file before the toggle and patched the same file after
    the 6.3 patcher changed its ordinary groups. medical is the off source of
    the toggle: the stored loot-zone baseline, or the current file without
    one. groups is the realization of the current applied record; old and new
    are the medical states before and after the toggle. Raises CloneMismatch
    when the generated groups of the file differ from the realization.
    """
    before, after = scan_document(current), scan_document(patched)
    realized = {str(item["name"]): item for item in groups}
    generated = _generated(current, before)
    if sorted(generated) != sorted(realized) or sorted(_generated(patched, after)) != sorted(realized):
        raise CloneMismatch(NAMES_DIFFER, "the generated loot groups of the file differ from the applied record")
    try:
        newline = newline_of(current)
    except LayoutError:
        raise layout_error(MIXED_LINES) from None
    indent = indent_of(current, _first_ordinary(before))
    edits = []
    for name, item in realized.items():
        source = item["source"]
        if list(source)[:1] != [PROTO]:
            raise CloneMismatch(SOURCE_DIFFERS, f"generated group {name} names another source file")
        old_source, new_source = _source(before, source[1], item), _source(after, source[1], item)
        group, ceilings = int(item["group_ceiling"]), tuple(int(value) for value in item["container_ceilings"])
        try:
            expected = clone_element(current, old_source, name, group, ceilings,
                                     clone_children(current, old_source, medical, lambda: old),
                                     indent, indent, newline)
            replacement = clone_element(patched, new_source, name, group, ceilings,
                                        clone_children(patched, new_source, medical, lambda: new),
                                        indent, indent, newline)
        except CloneSourceError as error:
            raise CloneMismatch(GROUP_DIFFERS, f"generated group {name}: {error}") from None
        node = generated[name]
        # The element starts after its indent; the indent before it stays as it is
        if current[node.start:node.end] != expected[len(indent):]:
            raise CloneMismatch(GROUP_DIFFERS, f"generated group {name} differs from the applied record")
        target = _generated(patched, after)[name]
        edits.append((target.start, target.end, replacement[len(indent):]))
    result = patched
    for start, end, data in sorted(edits, reverse=True):
        result = result[:start] + data + result[end:]
    return result


def _generated(data: bytes, root: Node) -> dict[str, Node]:
    """Return the top-level generated groups by name; a reserved group elsewhere or twice is a mismatch."""
    top = [node for node in root.children if node.tag == "group" and is_reserved(node.attributes.get("name", ""))]
    every = [node for node in scan_groups(data) if is_reserved(node.attributes.get("name", ""))]
    names = [node.attributes.get("name", "") for node in top]
    if len(every) != len(top) or len(set(names)) != len(names):
        raise CloneMismatch(NAMES_DIFFER, "the generated loot groups of the file differ from the applied record")
    return dict(zip(names, top))


def _first_ordinary(root: Node) -> Node | None:
    """Return the first top-level ordinary group, whose indent the 6.8 builder gives to generated groups."""
    return next((node for node in root.children if node.tag == "group"
                 and not is_reserved(node.attributes.get("name", ""))), None)


def _source(root: Node, name: str, item: Mapping[str, Any]) -> Node:
    """Return the one ordinary source group of a realization, with the recorded containers.

    Sources resolve without case, as the 6.8 routing does; the realization
    names the exact spelling of the one matching group.
    """
    matches = [node for node in root.children if node.tag == "group"
               and not is_reserved(node.attributes.get("name", ""))
               and node.attributes.get("name", "").casefold() == str(name).casefold()]
    if len(matches) != 1 or matches[0].attributes.get("name") != name:
        raise CloneMismatch(SOURCE_DIFFERS, f"source group {name} does not resolve to one group")
    containers = [node.attributes.get("name") for node in matches[0].children if node.tag == "container"]
    if containers != list(item["containers"]):
        raise CloneMismatch(SOURCE_DIFFERS, f"source group {name} has other containers")
    return matches[0]
