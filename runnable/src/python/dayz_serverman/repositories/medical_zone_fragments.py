"""Element-level state and byte-preserving patches of the medical loot-zone feature in `mapgroupproto.xml`.

The off children of each medical group come from the whole-file baseline. A patch changes only the usage
and category children of the qualified ordinary groups. `lootmax`, editor groups, comments, layout and all
other bytes stay as they are.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..domain.medical_zone_rule import (
    ADDED_CATEGORIES, MEDIC_USAGE, MEDICAL_CATEGORIES, MedicalChildren, is_reserved, medical_children, qualifies,
)
from .atomic_file import ContentChangedError
from .medical_zone_scan import (
    SPLIT_RUN, TEXT_RUN, Node, SplitRun, TextRun, check_run, children_of, fit_line_endings, is_space,
    layout_error, line_form, run_span, scan_groups,
)

# Refusal for a group that matches neither its original nor its managed children
DIFFERS = "medical feature target differs from both its original and managed state"
# Refusals for a medical group that only one of the two files holds; the class is the one of DIFFERS
ADDED = ("mapgroupproto.xml differs from its stored original: group {group} is not in the stored original. "
         "No file was changed.")
MISSING = ("mapgroupproto.xml differs from its stored original: group {group} is missing from the file. "
           "No file was changed.")


@dataclass(frozen=True)
class GroupPair:
    """One qualified group: its original node and children, and the node and children of the current file."""

    original: Node
    off: MedicalChildren
    on: MedicalChildren
    current: Node
    children: MedicalChildren


def zones_enabled(current: bytes, baseline: bytes) -> bool:
    """Return whether the feature is on: every changeable group off is off, every one on is on.

    A file without a group whose on and off children differ reads as on, as the whole-file form did.
    Raises ContentChangedError when a group matches neither form or the groups disagree.
    """
    return pairs_enabled(zone_pairs(current, baseline))


def pairs_enabled(pairs: list[GroupPair]) -> bool:
    """Return the state of already matched groups, with the refusals of zones_enabled."""
    states = {_state(pair) for pair in pairs}
    if len(states) > 1:
        raise ContentChangedError(DIFFERS)
    return False not in states


def has_zone_changes(current: bytes, baseline: bytes) -> bool:
    """Return whether any qualified group has on children that differ from its off children."""
    return bool(zone_pairs(current, baseline))


def patch_zones(current: bytes, baseline: bytes, enabled: bool) -> bytes:
    """Return the current bytes with each qualified group set to its on or off children.

    A file without a group that changes stays as it is. Raises ValueError when the file already has the
    requested state, and ConfigurationFileError naming the group when its children cannot be spliced.
    """
    result = current
    for start, end, replacement in sorted(zone_edits(current, baseline, zone_pairs(current, baseline), enabled),
                                          reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def zone_edits(current: bytes, baseline: bytes, pairs: list[GroupPair],
               enabled: bool) -> list[tuple[int, int, bytes]]:
    """Return the splices of a toggle to `enabled` over already matched groups, with every patch refusal.

    The classification uses it to test the next toggle without a second scan or a copy of the file.
    """
    states = {_state(pair) for pair in pairs}
    if len(states) > 1:
        raise ContentChangedError(DIFFERS)
    if not pairs:
        return []
    if states == {enabled}:
        raise ValueError("medical feature already has the requested state")
    # Collect the splices of every group, then write them from the end so earlier offsets stay valid
    edits: list[tuple[int, int, bytes]] = []
    # An off write counts the line endings of the file once for all of its runs
    form = "" if enabled else line_form(current)
    for pair in pairs:
        try:
            # Both files must hold each run as one clean run before any splice, in either direction
            _check_runs(baseline, pair.original); _check_runs(current, pair.current)
            edits += _enable_edits(current, pair) if enabled else _disable_edits(current, baseline, pair, form)
        except TextRun:
            raise layout_error(TEXT_RUN.format(group=pair.off.group)) from None
        except SplitRun:
            raise layout_error(SPLIT_RUN.format(group=pair.off.group)) from None
    return edits


def holds_reserved_group(data: bytes) -> bool:
    """Return whether the file holds a group with the map editor's reserved prefix."""
    return any(is_reserved(node.attributes.get("name", "")) for node in scan_groups(data))


def zone_pairs(current: bytes, baseline: bytes, groups: list[Node] | None = None) -> list[GroupPair]:
    """Match each qualified original group with the current group of the same name and position.

    Only groups whose on children differ from their off children take part. A different number of groups
    with that name, or different containers, refuses the whole file. `groups` are the already scanned
    groups of `current`; without them the file is scanned here.
    """
    present: dict[str, list[Node]] = defaultdict(list)
    for node in scan_groups(current) if groups is None else groups:
        present[node.attributes.get("name", "")].append(node)
    originals = scan_groups(baseline)
    counts: dict[str, int] = defaultdict(int)
    for node in originals:
        counts[node.attributes.get("name", "")] += 1
    _check_group_sets(current, baseline, present, originals, counts)
    pairs, seen = [], defaultdict(int)
    for node in originals:
        name = node.attributes.get("name", ""); position = seen[name]; seen[name] += 1
        off = children_of(baseline, node)
        # The ordinary patcher owns only qualified groups whose children change
        if is_reserved(name) or not qualifies(off):
            continue
        on = medical_children(off, True)
        if on.shape() == off.shape():
            continue
        # Match by name and position; the structure must be the same in both files
        if len(present[name]) != counts[name]:
            raise ContentChangedError(DIFFERS)
        current_node = present[name][position]
        children = children_of(current, current_node)
        if [item.name for item in children.containers] != [item.name for item in off.containers]:
            raise ContentChangedError(DIFFERS)
        pairs.append(GroupPair(node, off, on, current_node, children))
    return pairs


def _check_group_sets(current: bytes, baseline: bytes, present: dict[str, list[Node]], originals: list[Node],
                      counts: dict[str, int]) -> None:
    """Refuse a medical group that the stored original has and the file lacks, or the other way round."""
    # A qualified original group that the file holds fewer times is missing from the file
    for node in originals:
        name = node.attributes.get("name", "")
        if _medical(baseline, node) and len(present[name]) < counts[name]:
            raise ContentChangedError(MISSING.format(group=name))
    # A qualified group that the file holds more times than the stored original is not in that original
    for name, nodes in present.items():
        if len(nodes) > counts.get(name, 0) and any(_medical(current, node) for node in nodes):
            raise ContentChangedError(ADDED.format(group=name))


def _medical(data: bytes, node: Node) -> bool:
    """Return whether a group is an ordinary group that the medical rule qualifies."""
    return not is_reserved(node.attributes.get("name", "")) and qualifies(children_of(data, node))


def _state(pair: GroupPair) -> bool:
    """Return whether one group is on, comparing parsed children so any layout of them counts."""
    shape = pair.children.shape()
    if shape == pair.off.shape():
        return False
    if shape == pair.on.shape():
        return True
    raise ContentChangedError(DIFFERS)


def _enable_edits(data: bytes, pair: GroupPair) -> list[tuple[int, int, bytes]]:
    """Return the splices that remove the non-medical children of one group that is off."""
    group = pair.current
    usages = [node for node in group.children if node.tag == "usage"]
    edits = [_keep(data, group, usages, lambda node: node.attributes.get("name") == MEDIC_USAGE)]
    for container in (node for node in group.children if node.tag == "container"):
        categories = [node for node in container.children if node.tag == "category"]
        if any(node.attributes.get("name") in MEDICAL_CATEGORIES for node in categories):
            edits.append(_keep(data, container, categories,
                               lambda node: node.attributes.get("name") in MEDICAL_CATEGORIES))
        elif categories:
            start, end = _span(container, "category")
            edits.append((start, end, _line_separator(data, start).join(ADDED_CATEGORIES)))
        elif container.self_closing:
            # Open an empty-element container so it can hold the added categories
            edits.append((container.tag_end - 2, container.tag_end,
                          b">" + b"".join(ADDED_CATEGORIES) + b"</container>"))
        else:
            # Insert the added categories first, each on its own line in the layout of the first child
            following = data[container.tag_end:container.end]
            gap = following[:len(following) - len(following.lstrip(b" \t\r\n"))]
            edits.append((container.tag_end, container.tag_end, b"".join(gap + item for item in ADDED_CATEGORIES)))
    return edits


def _disable_edits(current: bytes, baseline: bytes, pair: GroupPair, form: str) -> list[tuple[int, int, bytes]]:
    """Return the splices that put back the exact original children of one group that is on."""
    start, end = _span(pair.original, "usage")
    # Each original run takes the line endings of the current file
    edits = [(*_span(pair.current, "usage"), fit_line_endings(baseline[start:end], current, form))]
    originals = [node for node in pair.original.children if node.tag == "container"]
    containers = [node for node in pair.current.children if node.tag == "container"]
    for original, container in zip(originals, containers):
        span = run_span(original, "category")
        if span is not None:
            edits.append((*_span(container, "category"), fit_line_endings(baseline[span[0]:span[1]], current, form)))
        elif original.self_closing and _only_categories(current, container):
            # Close the container again as one empty-element tag
            edits.append((container.start, container.end, current[container.start:container.tag_end - 1] + b"/>"))
        else:
            # Remove the added categories with the white space before each of them
            for index, node in enumerate(container.children):
                if node.tag == "category":
                    before = container.children[index - 1].end if index else container.tag_end
                    edits.append((before if is_space(current[before:node.start]) else node.start, node.end, b""))
    return edits


def _keep(data: bytes, parent: Node, nodes: list[Node], keep) -> tuple[int, int, bytes]:
    """Return the splice of a run of children that keeps only the children that `keep` accepts.

    A removed child takes the white space before it; text that is not white space stays.
    """
    start, end = _span(parent, nodes[0].tag)
    out, first = bytearray(), True
    for index, node in enumerate(nodes):
        gap = data[nodes[index - 1].end:node.start] if index else b""
        if keep(node):
            out += b"" if first and is_space(gap) else gap
            out += data[node.start:node.end]
            first = False
        elif not is_space(gap):
            out += gap
    return start, end, bytes(out)


def _check_runs(data: bytes, group: Node) -> None:
    """Check the usage run of a group and the category run of each of its containers."""
    check_run(data, group, "usage")
    for container in (node for node in group.children if node.tag == "container"):
        check_run(data, container, "category")


def _span(parent: Node, tag: str) -> tuple[int, int]:
    """Return the run span of `tag` children that the classification proved to exist."""
    span = run_span(parent, tag)
    if span is None:
        raise ContentChangedError(DIFFERS)
    return span


def _only_categories(data: bytes, container: Node) -> bool:
    """Return whether a container holds only category children and white space."""
    if any(node.tag != "category" for node in container.children):
        return False
    close = data.rfind(b"</", container.tag_end, container.end)
    pieces, offset = [], container.tag_end
    for node in container.children:
        pieces.append(data[offset:node.start]); offset = node.end
    pieces.append(data[offset:close])
    return is_space(b"".join(pieces))


def _line_separator(data: bytes, start: int) -> bytes:
    """Return a line break and the indentation of the line at `start`, or nothing for text on one line."""
    line = data.rfind(b"\n", 0, start)
    indent = data[line + 1:start]
    if line < 0 or not is_space(indent):
        return b""
    return (b"\r\n" if data[line - 1:line] == b"\r" else b"\n") + indent
