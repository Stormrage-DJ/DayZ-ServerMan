"""Read the branch facts of one app from noisy SteamCMD `app_info_print` output.

The app block is found at token level with the rules of `parse_vdf`, so a
brace inside a quoted value never ends it. Everything outside the block is
ignored. Only build ids and branch times are kept.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..domain.server_build import BRANCH_PATTERN, MAX_BRANCHES, MAX_BUILD_ID, BranchFact
from .steam_vdf import VdfError, field, parse_vdf

# Upper bound of tokens in one app block, as in `parse_vdf`
MAX_TOKENS = 100_000
_BRANCH = re.compile(BRANCH_PATTERN)
_DECIMAL = re.compile(r"[0-9]{1,10}")
# The line that proves an anonymous connection; without it the answer may come from the local cache
CONNECTED_PREFIX = "Connecting anonymously to Steam Public"


class AppInfoUnreadable(ValueError):
    """Raised when the output holds no single readable app block with a public branch."""


def connected(lines: Iterable[str]) -> bool:
    """Return whether SteamCMD reported a successful anonymous connection."""
    return any(line.strip().startswith(CONNECTED_PREFIX) and line.strip().endswith("OK")
               for line in lines)


def read_branches(lines: Iterable[str], app_id: str) -> dict[str, BranchFact]:
    """Return the branch facts of the app; raise AppInfoUnreadable for anything else."""
    block = extract_app_block("".join(lines), app_id)
    try:
        parsed = parse_vdf(block)
        app = field(parsed, app_id)
        depots = field(app, "depots") if isinstance(app, dict) else None
        branches = field(depots, "branches") if isinstance(depots, dict) else None
    except VdfError as error:
        raise AppInfoUnreadable("the app block cannot be parsed") from error
    if len(parsed) != 1 or not isinstance(branches, dict):
        raise AppInfoUnreadable("the app block has no branch list")
    facts: dict[str, BranchFact] = {}
    for name, entry in list(branches.items())[:MAX_BRANCHES]:
        fact = _branch_fact(name, entry)
        if fact is not None:
            facts[name] = fact
    if "public" not in facts:
        raise AppInfoUnreadable("the public branch is missing or invalid")
    return facts


def extract_app_block(text: str, app_id: str) -> str:
    """Return the one `"<app_id>" { … }` block of the text; raise when none or several exist."""
    found: list[str] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        # A candidate starts at a line that holds only the quoted app id
        if line.strip() == f'"{app_id}"':
            end = _block_end(text, offset, app_id)
            if end is not None:
                found.append(text[offset:end])
        offset += len(line)
    if len(found) != 1:
        raise AppInfoUnreadable("the output holds no single app block")
    return found[0]


def _block_end(text: str, start: int, app_id: str) -> int | None:
    """Return the end offset of a block that starts at `start`, or None when it is not one."""
    index, depth, count = start, 0, 0
    while index < len(text):
        mark = text[index]
        if mark.isspace():
            index += 1
            continue
        count += 1
        if count > MAX_TOKENS:
            return None
        if mark in "{}":
            # Token 1 is the quoted id and token 2 opens the block
            if count == 1 or (count == 2 and mark != "{"):
                return None
            depth += 1 if mark == "{" else -1
            index += 1
            if depth == 0:
                return index
            continue
        # Any other character outside a string means the text is not VDF
        if mark != '"' or count == 2:
            return None
        index, value = _quoted(text, index + 1)
        if index is None or (count == 1 and value != app_id):
            return None
    return None


def _quoted(text: str, index: int) -> tuple[int | None, str]:
    """Read one quoted string body with backslash escapes; return the offset after it."""
    value: list[str] = []
    while index < len(text) and text[index] != '"':
        if text[index] == "\\":
            index += 1
            if index >= len(text):
                return None, ""
        value.append(text[index])
        index += 1
    if index >= len(text):
        return None, ""
    return index + 1, "".join(value)


def _branch_fact(name: str, entry: Any) -> BranchFact | None:
    """Return the fact of one branch entry, or None when it breaks a rule."""
    if _BRANCH.fullmatch(name) is None or not isinstance(entry, dict):
        return None
    build = _number(entry, "buildid")
    if build is None or not 1 <= build <= MAX_BUILD_ID:
        return None
    time_updated = _number(entry, "timeupdated")
    return BranchFact(build, time_updated if time_updated else None)


def _number(entry: dict[str, Any], name: str) -> int | None:
    """Return a decimal field of a branch, or None when it is absent or not decimal."""
    values = [value for key, value in entry.items() if key.casefold() == name.casefold()]
    if len(values) != 1 or not isinstance(values[0], str) or _DECIMAL.fullmatch(values[0]) is None:
        return None
    return int(values[0])
