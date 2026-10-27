"""Byte spans of the loot groups in `mapgroupproto.xml`, so the medical patcher can splice exact bytes."""

from __future__ import annotations

import re
import xml.parsers.expat
from dataclasses import dataclass, field

from ..domain.medical_zone_rule import Container, Fragment, MedicalChildren
from .configuration_common import ConfigurationFileError

# Byte order marks of the UTF-16 and UTF-32 forms, whose bytes the ASCII scanner cannot splice
UTF16_MARKS = (b"\xff\xfe", b"\xfe\xff")
# Refusal of a file whose layout the patcher cannot change byte for byte; the cause completes it
LAYOUT = "mapgroupproto.xml has a layout that this setting cannot change safely: {cause}. No file was changed."
# Causes of the layout refusal
NOT_UTF8 = "the file is not UTF-8"
SPLIT_RUN = "group {group} has another element between usage and category"
TEXT_RUN = "group {group} has a comment or text between usage and category"
MIXED_LINES = "the file has more than one line-ending form"
UNKNOWN_ENCODING = "the file declares an unknown encoding"
# One tag up to its closing `>`; a quoted attribute value can hold `>`
TAG = re.compile(rb"""<(?:[^>"']|"[^"]*"|'[^']*')*>""")


class SplitRun(Exception):
    """Raised when the usage or category children of a group have another element between them."""


class TextRun(SplitRun):
    """Raised when the usage or category children of a group have a comment or text between them."""


def layout_error(cause: str) -> ConfigurationFileError:
    """Return the refusal of a file that the patcher cannot change safely, naming the cause."""
    return ConfigurationFileError(LAYOUT.format(cause=cause))


@dataclass
class Node:
    """One element with its byte spans: start of its start tag, end of its start tag, and its end."""

    tag: str
    attributes: dict[str, str]
    start: int
    tag_end: int
    end: int = -1
    children: list["Node"] = field(default_factory=list)

    @property
    def self_closing(self) -> bool:
        """Return whether the element is one empty-element tag such as `<x/>`."""
        return self.end == self.tag_end


def scan_groups(data: bytes) -> list[Node]:
    """Parse the document strictly and return every `group` element, at any depth, in document order.

    Raises ConfigurationFileError for malformed XML or a multi-byte encoding whose bytes cannot be spliced.
    """
    return _scan(data)[1]


def scan_document(data: bytes) -> Node:
    """Parse the document strictly like scan_groups and return its root element with all byte spans."""
    return _scan(data)[0]


def _scan(data: bytes) -> tuple[Node, list[Node]]:
    """Return the root element and every `group` element in document order."""
    # A byte order mark or a zero byte at the start shows a UTF-16 or UTF-32 file
    if data.startswith(UTF16_MARKS) or b"\x00" in data[:4]:
        raise layout_error(NOT_UTF8)
    parser = xml.parsers.expat.ParserCreate()
    stack: list[Node] = []
    groups: list[Node] = []
    roots: list[Node] = []

    def opened(tag: str, attributes: dict[str, str]) -> None:
        """Record the element and its start-tag span; expat reports the byte where the tag starts."""
        start = parser.CurrentByteIndex
        node = Node(tag, attributes, start, _tag_end(data, start))
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
        if tag == "group":
            groups.append(node)

    def closed(_tag: str) -> None:
        """Record the end of the element: after its empty-element tag, or after its end tag."""
        node = stack.pop()
        node.end = node.tag_end if data[node.start:node.tag_end].endswith(b"/>") else _tag_end(data, parser.CurrentByteIndex)

    # Parse in one call so the byte indexes are offsets into the whole document
    parser.StartElementHandler, parser.EndElementHandler = opened, closed
    try:
        parser.Parse(data, True)
    except LookupError as error:
        # An unknown encoding in the XML declaration names its cause and the file (QA-6.5-4, PO 23:22:28)
        raise layout_error(UNKNOWN_ENCODING) from error
    except xml.parsers.expat.ExpatError as error:
        raise ConfigurationFileError("medical feature target is malformed XML") from error
    return roots[0], groups


def children_of(data: bytes, group: Node) -> MedicalChildren:
    """Return the usage children of a group and the category children of each of its containers."""
    usages = tuple(_fragment(data, node) for node in group.children if node.tag == "usage")
    containers = tuple(Container(node.attributes.get("name"),
                                 tuple(_fragment(data, child) for child in node.children if child.tag == "category"))
                       for node in group.children if node.tag == "container")
    return MedicalChildren(group.attributes.get("name", ""), usages, containers)


def run_span(parent: Node, tag: str) -> tuple[int, int] | None:
    """Return the span from the first to the last `tag` child, or None when there is none.

    The children must be one run with no other element between them. Otherwise the patcher cannot
    change the structure safely, and SplitRun is raised for the caller to name the group.
    """
    positions = [index for index, node in enumerate(parent.children) if node.tag == tag]
    if not positions:
        return None
    if positions[-1] - positions[0] + 1 != len(positions):
        raise SplitRun(tag)
    return parent.children[positions[0]].start, parent.children[positions[-1]].end


def check_run(data: bytes, parent: Node, tag: str) -> None:
    """Refuse a run of `tag` children that another element or any text other than white space splits.

    A removed child takes only the white space before it, so a comment inside the run could not come
    back in its place. Raises SplitRun or TextRun for the caller to name the group.
    """
    run_span(parent, tag)
    nodes = [node for node in parent.children if node.tag == tag]
    if any(not is_space(data[before.end:after.start]) for before, after in zip(nodes, nodes[1:])):
        raise TextRun(tag)


def line_form(data: bytes) -> str:
    """Return the line-ending form of a file: "CRLF", "LF", "none", or "mixed" for more than one form."""
    crlf = data.count(b"\r\n")
    lone_lf, lone_cr = data.count(b"\n") - crlf, data.count(b"\r") - crlf
    if lone_cr or (crlf and lone_lf):
        return "mixed"
    return "CRLF" if crlf else "LF" if lone_lf else "none"


def fit_line_endings(text: bytes, data: bytes, form: str | None = None) -> bytes:
    """Return `text` with the line endings of the file `data`, so a baseline run brings no other form.

    A file without a line break keeps `text` as it is. Raises the layout refusal when `text` holds a line
    break and the file has more than one line-ending form. `form` is line_form(data) when the caller has it,
    so a file is counted once and not once for each run.
    """
    if b"\n" not in text and b"\r" not in text:
        return text
    form = form or line_form(data)
    if form == "mixed":
        raise layout_error(MIXED_LINES)
    if form == "none":
        return text
    plain = text.replace(b"\r\n", b"\n")
    return plain.replace(b"\n", b"\r\n") if form == "CRLF" else plain


def is_space(data: bytes) -> bool:
    """Return whether bytes hold only XML white space."""
    return not data.strip(b" \t\r\n")


def _fragment(data: bytes, node: Node) -> Fragment:
    """Return one child as a fragment with its exact bytes."""
    return Fragment(node.attributes.get("name"), tuple(sorted(node.attributes.items())), data[node.start:node.end])


def _tag_end(data: bytes, start: int) -> int:
    """Return the offset after the `>` that closes the tag starting at `start`, outside quoted values."""
    match = TAG.match(data, start)
    if match is None:
        raise ConfigurationFileError("medical feature target is malformed XML")
    return match.end()
