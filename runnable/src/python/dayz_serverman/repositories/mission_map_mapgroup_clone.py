"""Generated capacity groups of `mapgroupproto.xml`: D8 full key, name and element bytes (leaf 6.8).

A generated group copies its ordinary source group: geometry, points, tags,
other children and unowned attributes. Only its name, its ceilings and its
medical children differ. The medical children come from the 6.3 clone rule
and the off children of the source in the medical baseline, never from the
generated name. The element is canonical: equal inputs give equal bytes.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from ..domain.medical_zone_rule import Fragment, MedicalChildren, medical_children, qualifies
from ..domain.mission_map_plan import canonical_json
from .atomic_file import ContentChangedError
from .medical_zone_fragments import DIFFERS
from .medical_zone_scan import Node, children_of, is_space, scan_groups

# Prefix of a generated capacity group and the version and role of its full key (D8)
GENERATED_PREFIX = "ServerManMap_G_"
KEY_VERSION, KEY_ROLE = 1, "native_capacity"
# Number of hexadecimal digits of the key digest in the name
NAME_DIGITS = 12
# Comments between children carry no meaning and are not copied
COMMENT = re.compile(rb"<!--.*?-->", re.DOTALL)


class CloneSourceError(ValueError):
    """Raised when a source group holds text that a generated copy could not keep."""


def full_key(source: tuple[str, str], containers: tuple[str, ...], group: int,
             ceilings: tuple[int, ...]) -> list[object]:
    """Return the D8 key version 1 of a generated group, with canonical decimal ceilings."""
    return [KEY_VERSION, KEY_ROLE, list(source), list(containers), str(group), [str(value) for value in ceilings]]


def generated_name(key: list[object]) -> str:
    """Return the reserved name: the prefix and the first 12 hexadecimal digits of the key digest."""
    return GENERATED_PREFIX + hashlib.sha256(canonical_json(key)).hexdigest()[:NAME_DIGITS]


def clone_children(base: bytes, node: Node, medical_baseline: bytes | None,
                   enabled: Callable[[], bool]) -> MedicalChildren:
    """Return the medical children of a copy of one source group through the 6.3 clone rule.

    The off children of a medical source come from the group of the same name
    in the medical baseline. A source that the baseline lacks, or that does not
    qualify there, has no medical owner, and its copy keeps its working-baseline
    children. enabled reads the medical state; it is called only for a
    qualified source. Raises ContentChangedError when the result differs from
    the source group, because the copy must have the medical children of its
    source.
    """
    off = current = children_of(base, node)
    # Without a medical baseline the working baseline is the off state, as in 6.3
    owned = medical_baseline is None
    if medical_baseline is not None:
        name = node.attributes.get("name", "")
        matches = [item for item in scan_groups(medical_baseline) if item.attributes.get("name") == name]
        if len(matches) > 1:
            raise ContentChangedError(DIFFERS)
        if matches and qualifies(children_of(medical_baseline, matches[0])):
            off, owned = children_of(medical_baseline, matches[0]), True
    result = medical_children(off, enabled() if owned and qualifies(off) else False)
    if result.shape() != current.shape():
        raise ContentChangedError(DIFFERS)
    return result


def clone_element(base: bytes, node: Node, name: str, group: int, ceilings: tuple[int, ...],
                  children: MedicalChildren, indent: bytes, step: bytes, newline: bytes) -> bytes:
    """Return the canonical bytes of a generated copy of one source group, without a final line break.

    The group and every container carry explicit ceilings. Usages and the
    categories of each container take the place of the first such child of
    the source. Raises CloneSourceError for text other than white space.
    """
    _check_text(base, node)
    usages = [_fragment("usage", item) for item in children.usages]
    containers = iter(zip(children.containers, ceilings))
    lines = [indent + _start(node, {"name": name, "lootmax": str(group)})]
    placed = False
    for child in node.children:
        if child.tag == "usage":
            if not placed:
                lines += [indent + step + usage for usage in usages]
                placed = True
        elif child.tag == "container":
            medical, ceiling = next(containers)
            lines += _container(child, ceiling, medical.categories, indent + step, step)
        else:
            lines += _copy(child, indent + step, step)
    lines.append(indent + b"</group>")
    return newline.join(lines)


def _container(node: Node, ceiling: int, categories: tuple[Fragment, ...], indent: bytes,
               step: bytes) -> list[bytes]:
    """Return the lines of one container with its ceiling and its medical categories."""
    items = [indent + step + _fragment("category", item) for item in categories]
    body, placed = [], False
    for child in node.children:
        if child.tag == "category":
            if not placed:
                body += items
                placed = True
        else:
            body += _copy(child, indent + step, step)
    # A container without a category in the source gets the categories first
    if not placed:
        body = items + body
    start = _start(node, {"lootmax": str(ceiling)})
    if not body:
        return [indent + start[:-1] + b" />"]
    return [indent + start, *body, indent + b"</container>"]


def _copy(node: Node, indent: bytes, step: bytes) -> list[bytes]:
    """Return the lines of a copied element and its children, attributes in source order."""
    start = _start(node, {})
    if not node.children:
        return [indent + start[:-1] + b" />"]
    lines = [indent + start]
    for child in node.children:
        lines += _copy(child, indent + step, step)
    return lines + [indent + b"</" + node.tag.encode("ascii") + b">"]


def _start(node: Node, values: dict[str, str]) -> bytes:
    """Return a start tag with the source attributes, the given values set in place or appended."""
    attributes = dict(node.attributes)
    for key, value in values.items():
        attributes[key] = value
    # name first, then lootmax, as in the installed files, then the source order
    ordered = sorted(attributes.items(), key=lambda item: {"name": 0, "lootmax": 1}.get(item[0], 2))
    text = "".join(f' {key}="{_escape(value)}"' for key, value in ordered)
    return f"<{node.tag}{text}>".encode("ascii", "xmlcharrefreplace")


def _fragment(tag: str, fragment: Fragment) -> bytes:
    """Return one usage or category element in canonical form."""
    text = "".join(f' {key}="{_escape(value)}"' for key, value in fragment.attributes)
    return f"<{tag}{text} />".encode("ascii", "xmlcharrefreplace")


def _escape(value: str) -> str:
    """Escape an attribute value for double quotes; white space controls stay as references."""
    for raw, reference in (("&", "&amp;"), ("<", "&lt;"), ('"', "&quot;"), ("\t", "&#9;"), ("\n", "&#10;"),
                           ("\r", "&#13;")):
        value = value.replace(raw, reference)
    return value


def _check_text(data: bytes, node: Node) -> None:
    """Refuse a source element that holds text other than white space and comments."""
    if node.self_closing:
        return
    close = data.rfind(b"</", node.tag_end, node.end)
    offset = node.tag_end
    for child in node.children:
        _check_text(data, child)
        _check_gap(data[offset:child.start], node)
        offset = child.end
    _check_gap(data[offset:close], node)


def _check_gap(gap: bytes, node: Node) -> None:
    """Refuse text between children; comments and white space are allowed."""
    if not is_space(COMMENT.sub(b"", gap)):
        raise CloneSourceError(f"element {node.tag} holds text that a generated group cannot copy")
