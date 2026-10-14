"""Read the working-baseline mapgroup files for the capacity candidates (leaf 6.8, R08, D8).

The reader resolves loot sources in `mapgroupproto.xml` and the placements in
`mapgrouppos.xml` and `mapgroupdirt.xml`. It reads and writes no file; the
caller supplies the bytes. The engine matches names without regard to case,
so a placement resolves to the one group whose name matches without case
(Architect ruling of 6.8). A name that two or more definitions share without
case is ambiguous and never guessed.
"""

from __future__ import annotations

import math
import re
import xml.parsers.expat
from dataclasses import dataclass

from ..domain.medical_zone_rule import is_reserved
from ..domain.mission_map_native_capacity import (
    CONTAINER_AMBIGUOUS, BaselineContainer, BaselineSource, CapacityRefusal, Ceiling,
)
from .configuration_common import ConfigurationFileError
from .medical_zone_scan import NOT_UTF8, Node, scan_document, scan_groups

# Relative paths of the three mapgroup files in the mission folder
PROTO, POS, DIRT = "mapgroupproto.xml", "mapgrouppos.xml", "mapgroupdirt.xml"
# Prefix of the groups that an earlier planner tool generated; their content stays unchanged
PLANNER_PREFIX = "PlannerRich_"
# Supported lexical form of a native ceiling: ASCII decimal digits, leading zeros allowed (R08)
CEILING_TEXT = re.compile(r"[0-9]+", re.ASCII)
# One finite decimal number of a placement position or rotation
NUMBER_TEXT = re.compile(r"[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?", re.ASCII)

# Refusal reasons of the reader
FILE_INVALID = "file_invalid"
RESERVED_CONFLICT = "reserved_conflict"
SOURCE_MISSING = "source_missing"
SOURCE_AMBIGUOUS = "source_ambiguous"
PLACEMENT_GEOMETRY = "placement_geometry"
CEILING_LEXICAL = "ceiling_lexical"
DEFAULT_MISSING = "default_missing"
DEFAULT_DUPLICATE = "default_duplicate"


class MapgroupRefused(ValueError):
    """Refusal of one mapgroup file with a closed reason code and, when known, the source group."""

    def __init__(self, reason: str, path: str, message: str, source: str | None = None) -> None:
        """Keep the reason code, the relative path and the source group for the preview report."""
        self.reason, self.path, self.source = reason, path, source
        super().__init__(message)


@dataclass(frozen=True)
class Placement:
    """One placement binding of mapgrouppos.xml, mapgroupdirt.xml or a mapgroupcluster file.

    index is the position among the bindings of its file. position, rotation
    and angle are None when the attribute is absent. geometry_valid is false
    when a present value is not finite or the position is missing. A cluster
    placement has no node, because the editor never writes cluster files.
    """

    file: str
    index: int
    node: Node | None
    name: str
    position: tuple[float, float, float] | None
    rotation: tuple[float, float, float] | None
    angle: float | None
    geometry_valid: bool
    cluster: bool = False

    @property
    def identity(self) -> tuple[str, int, str]:
        """Return the file, index and name that a refusal or report names."""
        return self.file, self.index, self.name

    @property
    def planner(self) -> bool:
        """Return whether the placement binds a group of the earlier planner tool."""
        return self.name.casefold().startswith(PLANNER_PREFIX.casefold())


@dataclass(frozen=True)
class Prototype:
    """The parsed working-baseline prototype: its root, top-level groups and default ceilings."""

    root: Node
    groups: tuple[Node, ...]
    group_default: Ceiling
    container_default: Ceiling
    # Groups by their name without case, because the engine matches names that way
    buckets: dict[str, tuple[Node, ...]]

    def resolve(self, name: str) -> tuple[Node | None, str | None]:
        """Return the one group whose name matches without case, or None and the reason it does not resolve.

        A case-only match resolves too; the 6.10 probe checks that binding for each family.
        """
        bucket = self.buckets.get(name.casefold(), ())
        if not bucket:
            return None, SOURCE_MISSING
        if len(bucket) > 1:
            return None, SOURCE_AMBIGUOUS
        return bucket[0], None


def parse(path: str, data: bytes) -> Node:
    """Return the root element of one mapgroup file, or refuse a file that is not supported XML."""
    try:
        return scan_document(data)
    except ConfigurationFileError as error:
        raise _invalid(path, error) from None


def check_reserved(path: str, data: bytes) -> None:
    """Refuse a protected original or working baseline that holds a reserved editor name (D8 conflict)."""
    try:
        names = [node.attributes.get("name", "") for node in scan_groups(data)]
    except ConfigurationFileError as error:
        raise _invalid(path, error) from None
    reserved = sorted({name for name in names if is_reserved(name)})
    if reserved:
        raise MapgroupRefused(RESERVED_CONFLICT, path, f"{path} already holds the reserved map editor name "
                                                       f"{reserved[0]}. No file was changed.")


def read_prototype(data: bytes) -> Prototype:
    """Return the parsed prototype with its group and container defaults."""
    root = parse(PROTO, data)
    defaults = [node for section in root.children if section.tag == "defaults"
                for node in section.children if node.tag == "default"]
    groups = tuple(node for node in root.children if node.tag == "group")
    buckets: dict[str, tuple[Node, ...]] = {}
    for node in groups:
        key = node.attributes.get("name", "").casefold()
        buckets[key] = buckets.get(key, ()) + (node,)
    return Prototype(root, groups, _default(defaults, "group"), _default(defaults, "container"), buckets)


def read_placements(path: str, data: bytes) -> tuple[Node, tuple[Placement, ...]]:
    """Return the root and every top-level placement binding of mapgrouppos.xml or mapgroupdirt.xml."""
    root = parse(path, data)
    nodes = [node for node in root.children if node.tag == "group"]
    return root, tuple(_placement(path, index, node, node.attributes) for index, node in enumerate(nodes))


def read_cluster_placements(path: str, data: bytes, names: set[str]) -> tuple[Placement, ...]:
    """Return the placements of one mapgroupcluster file whose name, without case, is in names.

    Only names and geometry are read. The file is never written, so any encoding that expat reads is accepted.
    """
    placements: list[Placement] = []
    count = [0]
    parser = xml.parsers.expat.ParserCreate()

    def opened(tag: str, attributes: dict[str, str]) -> None:
        """Keep each group element whose name matches a prototype group."""
        if tag != "group":
            return
        if attributes.get("name", "").casefold() in names:
            placements.append(_placement(path, count[0], None, attributes, cluster=True))
        count[0] += 1

    parser.StartElementHandler = opened
    try:
        parser.Parse(data, True)
    except xml.parsers.expat.ExpatError:
        raise MapgroupRefused(FILE_INVALID, path, f"{path} is not supported: the file is malformed XML. "
                                                  "No file was changed.") from None
    return tuple(placements)


def baseline_source(prototype: Prototype, node: Node) -> BaselineSource:
    """Return the 6.7 baseline capacity of one ordinary group.

    Raises CapacityRefusal when a container has no name or two containers share one.
    """
    identity = (PROTO, node.attributes.get("name", ""))
    containers = []
    for container in (child for child in node.children if child.tag == "container"):
        name = container.attributes.get("name")
        if name is None:
            raise CapacityRefusal(identity, None, CONTAINER_AMBIGUOUS)
        points = sum(1 for child in container.children if child.tag == "point")
        containers.append(BaselineContainer(name, _ceiling(container, prototype.container_default), points))
    return BaselineSource(identity, _ceiling(node, prototype.group_default), tuple(containers))


def _invalid(path: str, error: ConfigurationFileError) -> MapgroupRefused:
    """Return the refusal of a file that is not supported XML, with its cause."""
    cause = NOT_UTF8 if NOT_UTF8 in str(error) else "the file is malformed XML"
    return MapgroupRefused(FILE_INVALID, path, f"{path} is not supported: {cause}. No file was changed.")


def _ceiling(node: Node, default: Ceiling) -> Ceiling:
    """Return the explicit ceiling of an element, or the default when the attribute is absent."""
    text = node.attributes.get("lootmax")
    if text is None:
        return default
    if CEILING_TEXT.fullmatch(text) is None:
        return Ceiling(None, True, CEILING_LEXICAL)
    return Ceiling(int(text), True)


def _default(defaults: list[Node], kind: str) -> Ceiling:
    """Return the one default ceiling of a group or container; a missing or repeated one stays unresolved."""
    values = [node.attributes["lootmax"] for node in defaults
              if node.attributes.get("name") == kind and "lootmax" in node.attributes]
    if not values:
        return Ceiling(None, False, DEFAULT_MISSING)
    if len(values) > 1:
        return Ceiling(None, False, DEFAULT_DUPLICATE)
    if CEILING_TEXT.fullmatch(values[0]) is None:
        return Ceiling(None, False, CEILING_LEXICAL)
    return Ceiling(int(values[0]), False)


def _placement(path: str, index: int, node: Node | None, attributes: dict[str, str], *,
               cluster: bool = False) -> Placement:
    """Return one binding with its parsed position, rotation and angle."""
    position, rotation, angle = (_numbers(attributes.get(key), count) for key, count in
                                 (("pos", 3), ("rpy", 3), ("a", 1)))
    valid = position not in (None, False) and False not in (rotation, angle)
    return Placement(path, index, node, attributes.get("name", ""),
                     position if valid else None, rotation if valid else None,
                     angle[0] if valid and angle else None, valid, cluster)


def _numbers(text: str | None, count: int):
    """Return the finite numbers of an attribute, None when it is absent, or False when it is malformed."""
    if text is None:
        return None
    parts = text.split()
    if len(parts) != count or not all(NUMBER_TEXT.fullmatch(part) for part in parts):
        return False
    # Adding zero turns -0.0 into 0.0, so equal numbers have one canonical form
    values = tuple(float(part) + 0.0 for part in parts)
    return values if all(math.isfinite(value) for value in values) else False
