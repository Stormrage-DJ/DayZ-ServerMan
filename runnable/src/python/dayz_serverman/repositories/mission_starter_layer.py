"""Starter items as a layer over the mission's own starting setup in init.c.

DayZ-ServerMan never edits the statements of a mission's `StartingEquipSetup`. It adds three marked parts:

- its own method with the chosen items, placed before `StartingEquipSetup`;
- one call of that method as the last statement of `StartingEquipSetup`;
- when the operator turns the mission's starting items off, an `if ( false )` block around the mission's
  statements, which removing its marker lines undoes exactly.

The mission's statements are only read, to describe what they give a new character.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .configuration_common import ConfigurationFileError, newline_for
from .mission_starter import END as INLINE_END, ITEM, START as INLINE_START
from .mission_starter import SIGNATURE, _function_bounds, _generated, _masked_code

METHOD_START = "// DayZ-ServerMan starter items begin"
METHOD_END = "// DayZ-ServerMan starter items end"
METHOD_NAME = "ServerManStarterItems"
CALL_MARKER = "// DayZ-ServerMan starter items call"
OFF_START = "// DayZ-ServerMan mission starting items off begin"
OFF_END = "// DayZ-ServerMan mission starting items off end"
# Statements of a mission routine that the description names, by what they do for a new character
EFFECTS = (
    (r"\bRemoveAllItems\s*\(", "removes the default clothing first"),
    (r"\bSetQuickBarEntityShortcut\s*\(", "puts items on the quickbar"),
    (r"\bGetStat(?:Water|Energy)\s*\(", "sets the start values of water or energy"),
    (r"\bSetTemporaryResistanceToAgent\s*\(", "gives a temporary disease resistance"),
    (r"\bSet(?:Random|Low)Health\s*\(|\bSetHealth01\s*\(", "sets the condition of items or clothing"),
    (r"\bSetQuantity(?:Max)?\s*\(", "sets item quantities"),
    (r"\bSetLiquidType\s*\(", "fills drinks"),
)
# A string array of class names, from which a routine picks one at random
CHOICE = re.compile(r"string\s+\w+\s*\[\s*\]\s*=\s*\{([^}]*)\}")
NAME = re.compile(r'"([A-Za-z0-9_]+)"')


class StarterLayoutError(ConfigurationFileError):
    """The mission's starting setup has a form that the layer cannot be added to safely."""


@dataclass(frozen=True)
class MissionStarter:
    """What the mission's own statements give a new character, as far as reading them shows."""

    items: tuple[tuple[str, int], ...] = ()
    choices: tuple[tuple[str, ...], ...] = ()
    random: bool = False
    effects: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the description for the bridge."""
        return {"items": [{"name": name, "count": count} for name, count in self.items],
                "choices": [list(choice) for choice in self.choices], "random": self.random,
                "effects": list(self.effects)}


@dataclass(frozen=True)
class StarterLayer:
    """The items of the layer, whether the mission's own items stay on, and the mission's own setup."""

    items: tuple[str, ...]
    mission_items: bool
    mission: MissionStarter = field(default_factory=MissionStarter)


def read_layer(text: str) -> StarterLayer:
    """Read the layer and describe the mission's own starting setup."""
    items, mission_items, clean = _strip(text)
    return StarterLayer(items, mission_items, describe(_body(clean)))


def write_layer(text: str, items: list[str], mission_items: bool) -> str:
    """Return init.c with the layer rewritten; with no items and the mission's items on, without any layer."""
    _, _, clean = _strip(text)
    if not items and mission_items:
        return clean
    newline = newline_for(clean)
    opening, closing = _function_bounds(clean)
    body = clean[opening + 1:closing]
    if re.search(r"\breturn\b", _masked_code(body)):
        raise StarterLayoutError("The starting setup of this mission ends early, so DayZ-ServerMan cannot add "
                                 "its starter items after it.")
    # The call goes on its own line before the closing brace of the routine
    close_line = _line_start(clean, closing)
    indent = re.match(r"[ \t]*", clean[close_line:closing]).group(0)
    inner = indent + "\t"
    body_start = clean.find(newline, opening) + len(newline)
    statements = clean[body_start:close_line] if body_start <= close_line else ""
    if not mission_items:
        statements = (f"{inner}{OFF_START}{newline}{inner}if ( false ){newline}{inner}{{{newline}"
                      f"{statements}{inner}}}{newline}{inner}{OFF_END}{newline}")
    call = f"{inner}{METHOD_NAME}( player ); {CALL_MARKER}{newline}"
    routine = clean[:body_start] + statements + call + clean[close_line:]
    # The method goes before the line that declares the routine, at the same indentation
    signature_line = _line_start(routine, _signature(routine))
    member = re.match(r"[ \t]*", routine[signature_line:]).group(0)
    method = (f"{member}{METHOD_START}{newline}{member}void {METHOD_NAME}( PlayerBase player ){newline}"
              f"{member}{{{newline}{member}\tEntityAI itemEnt;"
              f"{_generated(items, newline)}{member}}}{newline}{member}{METHOD_END}{newline}")
    return routine[:signature_line] + method + routine[signature_line:]


def describe(body: str) -> MissionStarter:
    """Describe what the statements of a starting setup give: items, random choices and other effects."""
    code = _masked_code(body)
    counts = Counter(match.group(1) for match in ITEM.finditer(body) if code[match.start()] != " ")
    choices = tuple(tuple(NAME.findall(match.group(1))) for match in CHOICE.finditer(body)
                    if code[match.start()] != " " and NAME.search(match.group(1)))
    random = bool(re.search(r"\bMath\.Random", code))
    effects = tuple(label for pattern, label in EFFECTS if re.search(pattern, code))
    return MissionStarter(tuple(counts.items()), choices, random, effects)


def _strip(text: str) -> tuple[tuple[str, ...], bool, str]:
    """Remove every layer part; return the layer items, whether the mission's items are on, and the clean text.

    A starter block that an earlier version placed among the mission's statements is removed too, and its
    items become the layer items.
    """
    lines = text.splitlines(keepends=True)
    in_code = _code_lines(lines)
    items: list[str] = []
    mission_items = True
    kept: list[str] = []
    index = 0
    while index < len(lines):
        # A marker counts only on a line that starts outside strings and block comments
        stripped = lines[index].strip() if in_code[index] else ""
        if stripped == METHOD_START or stripped == INLINE_START:
            end = METHOD_END if stripped == METHOD_START else INLINE_END
            closing = next((position for position in range(index + 1, len(lines))
                            if lines[position].strip() == end), None)
            if closing is None:
                raise ConfigurationFileError("init.c starter items markers are incomplete")
            items.extend(ITEM.findall("".join(lines[index + 1:closing])))
            index = closing + 1
            continue
        if stripped.endswith(CALL_MARKER):
            index += 1
            continue
        if stripped == OFF_START:
            # The two lines that follow open the block: `if ( false )` and `{`
            if index + 2 >= len(lines) or lines[index + 1].strip() != "if ( false )" or lines[index + 2].strip() != "{":
                raise ConfigurationFileError("init.c mission starting items markers are damaged")
            mission_items = False
            index += 3
            continue
        if stripped == OFF_END:
            # The line before closes the block
            if not kept or kept[-1].strip() != "}":
                raise ConfigurationFileError("init.c mission starting items markers are damaged")
            kept.pop()
            index += 1
            continue
        kept.append(lines[index])
        index += 1
    return tuple(dict.fromkeys(items)), mission_items, "".join(kept)


def _code_lines(lines: list[str]) -> list[bool]:
    """Return for each line whether its first non-blank character is script code, not a string or block comment."""
    result: list[bool] = []
    state = "code"
    for line in lines:
        offset = len(line) - len(line.lstrip())
        result.append(state == "code" and offset < len(line))
        index = 0
        while index < len(line):
            pair, char = line[index:index + 2], line[index]
            if state == "code" and pair == "//":
                break
            if state == "code" and pair == "/*":
                state, index = "block", index + 2
                continue
            if state == "block" and pair == "*/":
                state, index = "code", index + 2
                continue
            if state == "code" and char in ('"', "'"):
                state = char
            elif state in ('"', "'") and char == "\\":
                index += 2
                continue
            elif state in ('"', "'") and char == state:
                state = "code"
            index += 1
        # A string does not continue past the end of its line
        if state in ('"', "'"):
            state = "code"
    return result


def _body(text: str) -> str:
    """Return the statements of the routine."""
    opening, closing = _function_bounds(text)
    return text[opening + 1:closing]


def _signature(text: str) -> int:
    """Return the position of the routine's declaration."""
    return SIGNATURE.search(_masked_code(text)).start()


def _line_start(text: str, position: int) -> int:
    """Return the start of the line that holds a position."""
    return max(text.rfind("\n", 0, position), text.rfind("\r", 0, position)) + 1
