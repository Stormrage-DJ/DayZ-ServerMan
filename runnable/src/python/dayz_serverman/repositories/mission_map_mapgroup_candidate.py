"""Capacity candidates of `mapgroupproto.xml`, `mapgrouppos.xml` and `mapgroupdirt.xml` (leaf 6.8, R08, D8, A5).

Every call rebuilds the candidates from the working-baseline bytes, so a
reapply never compounds and removing every zone gives the baseline back. The
editor writes only ceilings of ordinary groups, its generated
`ServerManMap_G_` groups and detached placements. Medical children,
`PlannerRich_` content, fixed dispatch, categories, usages, points and all
other bytes stay as they are.

The result is complete or refused as a whole. Publication belongs to the
apply preview and Apply (8.6, 8.7). This module reads and writes no file; the
caller supplies the bytes.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.mission_map_native_capacity import CapacityTarget
from .atomic_file import ContentChangedError
from .configuration_common import ConfigurationFileError
from .medical_zone_fragments import zones_enabled
from .mission_map_mapgroup_clone import CloneSourceError, clone_children, clone_element
from .mission_map_mapgroup_projection import owned_digest
from .medical_zone_scan import Node
from .mission_map_mapgroup_read import (
    DIRT, POS, PROTO, MapgroupRefused, Placement, Prototype, check_reserved, parse, read_placements, read_prototype,
)
from .mission_map_mapgroup_routing import Clone, KeptPlacement, MapgroupRefusal, Routing, neutral, route
from .mission_map_mapgroup_splice import (
    Edit, LayoutError, apply_edits, attribute_edit, indent_of, insertion_edit, merge_removals, newline_of, removal_edit,
    with_attribute, with_newline,
)

# Parsed placement files: the root element and the bindings of each present file
Documents = dict[str, tuple[Node, tuple[Placement, ...]]]

# The three files in the order of the result
FILES = (PROTO, POS, DIRT)
# Refusal reasons of the file assembly
MEDICAL_STATE = "medical_state"
SOURCE_LAYOUT = "source_layout"
FILE_LAYOUT = "file_layout"
DIRT_ABSENT = "dirt_file_absent"


@dataclass(frozen=True)
class FileCandidate:
    """Candidate bytes of one file (None keeps an absent file absent) with its D3 digests."""

    path: str
    content: bytes | None
    changed: bool
    sha256: str | None
    owned_value_sha256: str | None


@dataclass(frozen=True)
class DetachedBinding:
    """One detached placement as the applied record stores it: origin file, name, geometry and multiplicity."""

    file: str
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float] | None
    angle: float | None
    multiplicity: int


@dataclass(frozen=True)
class CloneRealization:
    """The realization of one generated group for the applied record (D3 "Native Realization")."""

    name: str
    key: list[object]
    source: tuple[str, str]
    containers: tuple[str, ...]
    group_ceiling: str
    container_ceilings: tuple[str, ...]
    detached: tuple[DetachedBinding, ...]


@dataclass(frozen=True)
class MapgroupCandidates:
    """The three file candidates, or no file and the refusals; targets and kept placements serve the report."""

    files: tuple[FileCandidate, ...]
    clones: tuple[CloneRealization, ...]
    targets: tuple[CapacityTarget, ...]
    kept: tuple[KeptPlacement, ...]
    refusals: tuple[MapgroupRefusal, ...]

    @property
    def refused(self) -> bool:
        """Return whether Apply must refuse: any refusal refuses the complete candidate."""
        return bool(self.refusals)


def build_mapgroup_candidates(plan: dict[str, Any], baseline: Mapping[str, bytes | None], *,
                              medical_baseline: bytes | None = None, maximum: int | None = None,
                              clusters: Mapping[str, bytes] | None = None) -> MapgroupCandidates:
    """Return the capacity candidates of a validated D1 plan over the working-baseline bytes of the three files.

    medical_baseline is the stored whole-file medical baseline of
    mapgroupproto.xml (6.3), or None when there is none. maximum is the
    supported native ceiling limit when evidence proves one. clusters maps
    each mapgroupcluster*.xml of the mission root to its bytes.
    """
    data = {path: baseline.get(path) for path in FILES}
    refusals: list[MapgroupRefusal] = []
    for path, content in data.items():
        if content is not None:
            try:
                check_reserved(path, content)
            except MapgroupRefused as error:
                refusals.append(_file_refusal(error))
    if refusals:
        return _result((), Routing(refusals=refusals))
    if neutral(plan):
        return _result(tuple(_file(path, content, False) for path, content in data.items()), Routing())
    try:
        prototype = None if data[PROTO] is None else read_prototype(data[PROTO])
        documents = {path: read_placements(path, data[path]) for path in (POS, DIRT) if data[path] is not None}
    except MapgroupRefused as error:
        return _result((), Routing(refusals=[_file_refusal(error)]))
    placements = [item for _root, items in documents.values() for item in items]
    routing = route(plan, prototype, placements, maximum, clusters)
    if routing.refusals:
        return _result((), routing)
    try:
        files = _assemble(data, prototype, documents, routing, medical_baseline)
    except MapgroupRefused as error:
        return _result((), Routing(targets=routing.targets, kept=routing.kept, refusals=[_file_refusal(error)]))
    return _result(files, routing)


def _assemble(data: dict[str, bytes | None], prototype: Prototype, documents: Documents, routing: Routing,
              medical_baseline: bytes | None) -> tuple[FileCandidate, ...]:
    """Return the three candidates; raise MapgroupRefused for a file that cannot change safely."""
    proto, detached = data[PROTO], [item for clone in routing.clones.values() for item in clone.placements]
    edits = []
    for node, target in routing.sources:
        edits += _ceiling_edits(proto, node, target)
    clones = sorted(routing.clones.values(), key=lambda clone: clone.name)
    if clones:
        newline = _newline(PROTO, proto)
        indent = indent_of(proto, prototype.groups[0] if prototype.groups else None)
        enabled = _medical_state(proto, medical_baseline)
        lines = []
        for clone in clones:
            source = clone.source.attributes.get("name")
            try:
                children = clone_children(proto, clone.source, medical_baseline, enabled)
                lines.append(clone_element(proto, clone.source, clone.name, clone.target.group.realized,
                                           tuple(value.realized for value in clone.target.containers),
                                           children, indent, indent, newline))
            except (ContentChangedError, ConfigurationFileError) as error:
                raise MapgroupRefused(MEDICAL_STATE, PROTO, _message(f"group {source}: {error}"), source) from None
            except CloneSourceError as error:
                raise MapgroupRefused(SOURCE_LAYOUT, PROTO, _message(f"group {source}: {error}"), source) from None
        edits.append(_insert(PROTO, proto, prototype.root, lines, newline))
    result = {PROTO: _apply(PROTO, proto, edits) if edits else proto}
    result[POS], result[DIRT] = _placement_files(data, documents, detached, {id(item): clone.name for clone in clones
                                                                              for item in clone.placements})
    _verify(result, data, len(clones), len(detached))
    return tuple(_file(path, result[path], result[path] != data[path]) for path in FILES)


def _ceiling_edits(data: bytes, node: Node, target: CapacityTarget) -> list[Edit]:
    """Return the ceiling edits of one ordinary group: only values that differ from the baseline change."""
    edits = []
    if target.group.realized != target.group.baseline:
        edits.append(attribute_edit(data, node, "lootmax", str(target.group.realized)))
    containers = [child for child in node.children if child.tag == "container"]
    for container, value in zip(containers, target.containers):
        if value.realized != value.baseline:
            edits.append(attribute_edit(data, container, "lootmax", str(value.realized)))
    return edits


def _placement_files(data: dict[str, bytes | None], documents: Documents, detached: list[Placement],
                     names: dict[int, str]) -> tuple[bytes | None, bytes | None]:
    """Return mapgrouppos.xml without the detached placements and mapgroupdirt.xml with their copies."""
    if not detached:
        return data[POS], data[DIRT]
    if data[DIRT] is None:
        # Creating the file needs family-format evidence that the adapter does not have
        raise MapgroupRefused(DIRT_ABSENT, DIRT, f"{DIRT} does not exist, and placements must move to it. "
                                                 "No file was changed.")
    pos, dirt = data[POS], data[DIRT]
    pos_root, dirt_root = documents[POS][0], documents[DIRT][0]
    positions = {id(child): index for index, child in enumerate(pos_root.children)}
    ordered = sorted(detached, key=lambda item: item.index)
    removals = merge_removals(pos, pos_root, [removal_edit(pos, pos_root, positions[id(item.node)])
                                              for item in ordered])
    first = documents[DIRT][1][0].node if documents[DIRT][1] else None
    indent = indent_of(dirt, first) if first is not None else indent_of(pos, ordered[0].node)
    newline = _newline(DIRT, dirt) if b"\n" in dirt else _newline(POS, pos)
    # A copied element that spans lines takes the line endings of mapgroupdirt.xml
    lines = [indent + with_newline(with_attribute(pos, item.node, "name", names[id(item)]), newline)
             for item in ordered]
    return _apply(POS, pos, removals), _apply(DIRT, dirt, [_insert(DIRT, dirt, dirt_root, lines, newline)])


def _medical_state(proto: bytes, medical_baseline: bytes | None) -> Callable[[], bool]:
    """Return a reader of the medical state of the working baseline that reads the file once, when asked."""
    cache: list[bool] = []

    def enabled() -> bool:
        """Return whether the ordinary medical groups of the working baseline are on (6.3 state)."""
        if not cache:
            cache.append(zones_enabled(proto, medical_baseline if medical_baseline is not None else proto))
        return cache[0]
    return enabled


def _insert(path: str, data: bytes, root: Node, lines: list[bytes], newline: bytes) -> Edit:
    """Return the insertion edit before the root end tag, or refuse an unsupported root."""
    try:
        return insertion_edit(data, root, lines, newline)
    except LayoutError as error:
        raise MapgroupRefused(FILE_LAYOUT, path, f"{path} has a layout that cannot change safely: {error}. "
                                                 "No file was changed.") from None


def _apply(path: str, data: bytes, edits: list[Edit]) -> bytes:
    """Return the bytes with the edits applied, or refuse when two edits overlap."""
    try:
        return apply_edits(data, edits)
    except LayoutError as error:
        raise MapgroupRefused(FILE_LAYOUT, path, f"{path} has a layout that cannot change safely: {error}. "
                                                 "No file was changed.") from None


def _newline(path: str, data: bytes) -> bytes:
    """Return the line ending of a file, or refuse a file with more than one form."""
    try:
        return newline_of(data)
    except LayoutError as error:
        raise MapgroupRefused(FILE_LAYOUT, path, f"{path} has a layout that cannot change safely: {error}. "
                                                 "No file was changed.") from None


def _verify(result: dict[str, bytes | None], data: dict[str, bytes | None], clones: int, detached: int) -> None:
    """Parse each changed file again and check the counts of groups and placements."""
    for path, expected in ((PROTO, clones), (POS, -detached), (DIRT, detached)):
        if result[path] is None or result[path] == data[path]:
            continue
        before, after = (sum(1 for node in parse(path, content).children if node.tag == "group")
                         for content in (data[path], result[path]))
        if after != before + expected:
            raise MapgroupRefused(FILE_LAYOUT, path, f"{path} candidate has an unexpected group count. "
                                                     "No file was changed.")


def _result(files: tuple[FileCandidate, ...], routing: Routing) -> MapgroupCandidates:
    """Return the result; a refused result has no file."""
    clones = () if routing.refusals else tuple(_realization(clone) for clone in
                                                sorted(routing.clones.values(), key=lambda clone: clone.name))
    return MapgroupCandidates(() if routing.refusals else files, clones, tuple(routing.targets),
                              tuple(routing.kept), tuple(routing.refusals))


def _realization(clone: Clone) -> CloneRealization:
    """Return the plain realization of one generated group with its detached placement multiset."""
    counts = Counter((item.file, item.name, item.position, item.rotation, item.angle) for item in clone.placements)
    detached = tuple(DetachedBinding(*binding, count) for binding, count in
                     sorted(counts.items(), key=lambda entry: repr(entry[0])))
    key = clone.key
    return CloneRealization(clone.name, key, tuple(key[2]), tuple(key[3]), key[4], tuple(key[5]), detached)


def _file(path: str, content: bytes | None, changed: bool) -> FileCandidate:
    """Return one file candidate with its whole-file and owned-value digests."""
    digest = None if content is None else hashlib.sha256(content).hexdigest()
    return FileCandidate(path, content, changed, digest, owned_digest(path, content))


def _message(text: str) -> str:
    """Return a refusal sentence that ends with the no-change note once."""
    text = text.removesuffix(" No file was changed.").rstrip(".")
    return f"{text}. No file was changed."


def _file_refusal(error: MapgroupRefused) -> MapgroupRefusal:
    """Return the refusal of one whole file."""
    return MapgroupRefusal(error.reason, error.path, error.source, None, (), str(error))
