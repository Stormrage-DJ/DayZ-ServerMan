"""Byte splices of the mapgroup capacity candidates (leaf 6.8), in the style of the 6.3 medical patcher.

Each helper returns an edit (start, end, replacement) on the exact baseline
bytes, or the bytes of one element. The file is never serialized again, so
every byte outside an edit stays as it is.
"""

from __future__ import annotations

import re

from .medical_zone_scan import Node, is_space

# One attribute of a start tag; a quoted value can hold `>` and white space
ATTRIBUTE = re.compile(rb"""\s+([^\s=/>]+)\s*=\s*("[^"]*"|'[^']*')""")
# The tag name at the start of a start tag
TAG_NAME = re.compile(rb"<[^\s/>]+")
# Indentation of an inserted element when the file shows none
DEFAULT_INDENT = b"    "

Edit = tuple[int, int, bytes]


class LayoutError(ValueError):
    """Raised when a file has a layout that the splices cannot change safely."""


def apply_edits(data: bytes, edits: list[Edit]) -> bytes:
    """Return the bytes with every edit applied, from the end so earlier offsets stay valid.

    Raises LayoutError when two edits overlap, so no edit can land on bytes that another edit moved.
    """
    ordered = sorted(edits, key=lambda edit: (edit[0], edit[1]))
    for before, after in zip(ordered, ordered[1:]):
        if after[0] < before[1]:
            raise LayoutError("two changes overlap")
    result = data
    for start, end, replacement in reversed(ordered):
        result = result[:start] + replacement + result[end:]
    return result


def merge_removals(data: bytes, parent: Node, edits: list[Edit]) -> list[Edit]:
    """Return removal edits with overlapping or touching spans joined, so the result has disjoint spans.

    A joined span that leaves only white space on its line takes the whole line.
    """
    merged: list[Edit] = []
    for start, end, _replacement in sorted(edits):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]), b"")
        else:
            merged.append((start, end, b""))
    result = []
    for start, end, _replacement in merged:
        line_start = data.rfind(b"\n", 0, start) + 1
        line_end = data.find(b"\n", end)
        line_end = len(data) if line_end < 0 else line_end + 1
        if line_start > parent.tag_end and is_space(data[line_start:start]) and is_space(data[end:line_end]):
            start, end = line_start, line_end
        result.append((start, end, b""))
    return result


def attribute_edit(data: bytes, node: Node, name: str, value: str) -> Edit:
    """Return the edit that sets one attribute of an element's start tag to an ASCII value.

    An existing value keeps its quotes and position. An absent attribute goes
    after the last attribute, in double quotes.
    """
    tag = data[node.start:node.tag_end]
    position = TAG_NAME.match(tag).end()
    while (match := ATTRIBUTE.match(tag, position)) is not None:
        if match.group(1) == name.encode("ascii"):
            return node.start + match.start(2) + 1, node.start + match.end(2) - 1, value.encode("ascii")
        position = match.end()
    insert = node.start + position
    return insert, insert, b" " + name.encode("ascii") + b'="' + value.encode("ascii") + b'"'


def with_attribute(data: bytes, node: Node, name: str, value: str) -> bytes:
    """Return the exact bytes of one element with one attribute set."""
    start, end, replacement = attribute_edit(data, node, name, value)
    return data[node.start:start] + replacement + data[end:node.end]


def removal_edit(data: bytes, parent: Node, index: int) -> Edit:
    """Return the edit that removes the child element at one index; the span never leaves the element's lines.

    An element first on its line goes with the spaces after it. Any other
    element goes with the spaces before it on its line. merge_removals then
    gives a line with nothing else left its whole line.
    """
    node = parent.children[index]
    line_start = data.rfind(b"\n", 0, node.start) + 1
    if line_start > parent.tag_end and is_space(data[line_start:node.start]):
        end = node.end
        while data[end:end + 1] in (b" ", b"\t"):
            end += 1
        return node.start, end, b""
    start = node.start
    while start > line_start and data[start - 1:start] in (b" ", b"\t"):
        start -= 1
    return start, node.end, b""


def insertion_edit(data: bytes, root: Node, lines: list[bytes], newline: bytes) -> Edit:
    """Return the edit that adds lines before the root end tag; the end tag keeps its own line."""
    if root.self_closing:
        raise LayoutError("the root element has no end tag")
    close = data.rfind(b"</", root.tag_end, root.end)
    line_start = data.rfind(b"\n", 0, close) + 1
    if line_start > root.tag_end and is_space(data[line_start:close]):
        return line_start, line_start, b"".join(line + newline for line in lines)
    return close, close, b"".join(newline + line for line in lines) + newline


def with_newline(element: bytes, newline: bytes) -> bytes:
    """Return element bytes whose line breaks use one line-ending form."""
    return element.replace(b"\r\n", b"\n").replace(b"\n", newline)


def newline_of(data: bytes) -> bytes:
    """Return the one line-ending form of a file; a file without a line break uses LF.

    Raises LayoutError when the file has more than one form.
    """
    crlf = data.count(b"\r\n")
    lone_lf, lone_cr = data.count(b"\n") - crlf, data.count(b"\r") - crlf
    if lone_cr or (crlf and lone_lf):
        raise LayoutError("the file has more than one line-ending form")
    return b"\r\n" if crlf else b"\n"


def indent_of(data: bytes, node: Node | None) -> bytes:
    """Return the white space before an element on its line, or the default for none."""
    if node is None:
        return DEFAULT_INDENT
    line_start = data.rfind(b"\n", 0, node.start) + 1
    indent = data[line_start:node.start]
    return indent if indent and is_space(indent) else DEFAULT_INDENT
