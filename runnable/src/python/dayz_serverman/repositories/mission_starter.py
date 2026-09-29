"""Localized starter-loadout marker adoption and generation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .configuration_common import ConfigurationFileError, newline_for

# Marker comments that delimit the managed starter loadout block
START = "// DayZ-ServerMan starter loadout begin"
END = "// DayZ-ServerMan starter loadout end"
# Matches the single StartingEquipSetup override the loader can edit
SIGNATURE = re.compile(
    r"override\s+void\s+StartingEquipSetup\s*\(\s*PlayerBase\s+player\s*,\s*bool\s+clothesChosen\s*\)"
)
# Matches item creation calls inside the setup body
ITEM = re.compile(r'Create(?:InInventory|Attachment)\s*\(\s*"([A-Za-z0-9_]+)"\s*\)')
# Items created as attachments rather than inventory contents
BACKPACK_ITEMS = frozenset({"HipPack_Medical", "TaloonBag_Green", "ChildBag_Green", "ImprovisedBag"})
# Items recognized in legacy starter blocks before conversion
LEGACY_ITEMS = frozenset({
    "Apple", "Pear", "Plum", "WaterBottle", "Canteen", "BandageDressing",
    "TetracyclineAntibiotics", "VitaminBottle", "CharcoalTablets",
    "PainkillerTablets", "DisinfectantSpray", "IodineTincture",
    "PurificationTablets", "SteakKnife", "CanOpener", "BoxedMatches",
    "Heatpack", "Compass", "TouristMap", "HipPack_Medical", "TaloonBag_Green",
    "ChildBag_Green", "ImprovisedBag", "Rag", "DuctTape", "SewingKit",
    "LeatherSewingKit",
})


@dataclass(frozen=True)
class StarterRegion:
    """Managed block bounds and whether marker adoption is required."""
    content_start: int
    content_end: int
    adoption_required: bool


@dataclass(frozen=True)
class MarkerLine:
    """One starter marker comment with its line and comment bounds."""
    kind: str
    line_start: int
    comment_start: int
    comment_end: int


@dataclass(frozen=True)
class LegacyStarterRegion:
    """Bounds, lines, and items of a detected legacy starter block."""
    start: int
    end: int
    start_line: int
    end_line: int
    items: tuple[str, ...]


def read_starter(text: str) -> tuple[list[str], bool]:
    """Return the managed starter items and whether marker adoption is required."""
    legacy = legacy_starter_region(text)
    # An unmarked legacy block takes precedence over managed markers
    if legacy is not None:
        return list(legacy.items), False
    region = locate_region(text)
    if region.adoption_required:
        return [], True
    # Read the item names from the managed region only
    content = text[region.content_start:region.content_end]
    return ITEM.findall(content), False


def patch_starter(text: str, items: list[str]) -> str:
    """Replace the managed starter block content with generated item lines."""
    # Legacy content must be converted first so markers bracket the block
    if legacy_starter_region(text) is not None:
        raise ConfigurationFileError("convert the legacy starter loadout before editing it")
    region = locate_region(text)
    newline = newline_for(text)
    generated = _generated(items, newline)
    # Wrap generated lines in markers when adopting an unmarked function
    if region.adoption_required:
        block = f"{newline}\t\t{START}{generated}\t\t{END}{newline}"
    else:
        block = generated
    return text[:region.content_start] + block + text[region.content_end:]


def convert_legacy_starter(text: str) -> str:
    """Wrap the detected legacy starter block in managed begin and end markers."""
    region = legacy_starter_region(text)
    if region is None:
        raise ConfigurationFileError("init.c has no recognizable legacy starter loadout to convert")
    newline = newline_for(text)
    # Reuse the indentation of the first legacy line for both markers
    line = text[region.start:text.find(newline, region.start) if newline in text[region.start:] else region.end]
    indent = re.match(r"\s*", line).group(0)
    before = f"{indent}{START}{newline}"
    after = f"{indent}{END}{newline}"
    return text[:region.start] + before + text[region.start:region.end] + after + text[region.end:]


def legacy_starter_region(text: str) -> LegacyStarterRegion | None:
    """Detect an unmarked legacy starter block and return its bounds and items."""
    opening, closing = _function_bounds(text)
    # Managed markers take precedence; legacy detection only runs without them
    if _marker_lines(text):
        return None
    body, code = text[opening + 1:closing], _masked_code(text[opening + 1:closing])
    calls = [match for match in ITEM.finditer(body) if code[match.start()] != " "]
    if not calls:
        return None
    # Every created item must be a known legacy loadout item
    unsupported = [match.group(1) for match in calls if match.group(1) not in LEGACY_ITEMS]
    if unsupported:
        raise ConfigurationFileError("init.c legacy starter loadout contains unsupported items")
    first = opening + 1 + calls[0].start()
    start = max(text.rfind("\n", 0, first), text.rfind("\r", 0, first)) + 1
    end, seen, local_depth = start, 0, 0
    # Extend line by line while each line stays within the legacy grammar
    for match in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n|$)", text[start:closing]):
        raw = match.group(0)
        if not raw:
            break
        stripped = raw.strip()
        raw_code = _masked_code(raw)
        create = next((item for item in ITEM.finditer(raw) if raw_code[item.start()] != " "), None)
        allowed = _legacy_line_allowed(stripped, create is not None, local_depth)
        if not allowed:
            if seen < len(calls):
                raise ConfigurationFileError("init.c legacy starter items are not one safe contiguous block")
            break
        if create:
            seen += 1
        # Track brace nesting so only safe contiguous lines are accepted
        if stripped == "{":
            local_depth += 1
        elif stripped == "}":
            local_depth -= 1
        end = start + match.end()
    # The block must contain every call and close all braces it opened
    if seen != len(calls) or local_depth != 0:
        raise ConfigurationFileError("init.c legacy starter block is incomplete or ambiguous")
    items = tuple(match.group(1) for match in calls)
    return LegacyStarterRegion(start, end, _line_number(text, start), _line_number(text, end), items)


def _line_number(text: str, position: int) -> int:
    """Return the one-based line number containing a character position."""
    return len(re.findall(r"\r\n|\r|\n", text[:position])) + 1


def _legacy_line_allowed(line: str, creates_item: bool, local_depth: int) -> bool:
    """Return whether a legacy starter line may remain inside the adopted block."""
    # Blank lines, comments, and item creations are always safe to keep
    if not line or line.startswith("//") or creates_item:
        return True
    if line == "{":
        return True
    # A closing brace is kept only while it closes a nested local block
    if line == "}":
        return local_depth > 0
    # The remaining lines must match the known legacy boilerplate exactly
    patterns = (
        r"SetPristine\s*\(\s*itemEnt\s*\)\s*;",
        r"if\s*\(\s*(?:itemEnt|waterBottle)\s*\)",
        r"itemEnt\.SetHealth01\s*\(.*\)\s*;",
        r"ItemBase\s+waterBottle\s*=\s*ItemBase\.Cast\s*\(\s*itemEnt\s*\)\s*;",
        r"waterBottle\.SetLiquidType\s*\(\s*LIQUID_WATER\s*\)\s*;",
        r"waterBottle\.SetQuantityMax\s*\(\s*\)\s*;",
    )
    return any(re.fullmatch(pattern, line) for pattern in patterns)


def locate_region(text: str) -> StarterRegion:
    """Return the managed or adoptable starter region inside StartingEquipSetup."""
    opening, closing = _function_bounds(text)
    markers = _marker_lines(text)
    # Every marker found must sit inside the recognized function body
    inside = [marker for marker in markers if opening < marker.comment_start < closing]
    if len(inside) != len(markers):
        raise ConfigurationFileError("init.c starter loadout marker is outside StartingEquipSetup")
    starts = [marker for marker in inside if marker.kind == "start"]
    ends = [marker for marker in inside if marker.kind == "end"]
    # A complete marker pair delimits the managed content directly
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0].comment_start >= ends[0].comment_start:
            raise ConfigurationFileError("init.c starter loadout markers are missing or ambiguous")
        return StarterRegion(starts[0].comment_end, ends[0].line_start, False)
    body = text[opening + 1:closing]
    code = _masked_code(body)
    # Unmarked legacy items cannot be adopted safely, so refuse them
    for match in ITEM.finditer(body):
        if code[match.start()] != " " and match.group(1) in LEGACY_ITEMS:
            raise ConfigurationFileError(
                "init.c contains unmarked legacy starter items; safe marker adoption is unavailable"
            )
    # Adoption needs exactly one itemEnt declaration as the insertion point
    declarations = list(re.finditer(r"\bEntityAI\s+itemEnt\s*;", code))
    if len(declarations) != 1:
        raise ConfigurationFileError("StartingEquipSetup needs one EntityAI itemEnt insertion point")
    insertion = opening + 1 + declarations[0].end()
    return StarterRegion(insertion, insertion, True)


def _function_bounds(text: str) -> tuple[int, int]:
    """Return the body bounds of the single StartingEquipSetup override."""
    code = _masked_code(text)
    # Exactly one recognizable signature must exist before bounds are computed
    matches = list(SIGNATURE.finditer(code))
    if len(matches) != 1:
        raise ConfigurationFileError("init.c needs one recognizable StartingEquipSetup insertion point")
    # Locate the opening brace and the brace that closes the body
    opening = code.find("{", matches[0].end())
    if opening < 0:
        raise ConfigurationFileError("StartingEquipSetup body is malformed")
    return opening, _matching_brace(text, opening)


def _marker_lines(text: str) -> list[MarkerLine]:
    """Return marker comment lines found outside strings and block comments."""
    markers: list[MarkerLine] = []
    index, state, line_start = 0, "code", 0
    # Track string and comment state so marker text inside them is ignored
    while index < len(text):
        pair, char = text[index:index + 2], text[index]
        if state == "code" and pair == "//":
            end = len(text)
            for separator in ("\r", "\n"):
                found = text.find(separator, index + 2)
                if found >= 0: end = min(end, found)
            content = text[index:end].rstrip()
            # Only a standalone trimmed marker comment counts as a marker
            if text[line_start:index].strip() == "" and content in (START, END):
                markers.append(MarkerLine("start" if content == START else "end", line_start, index, end))
            index = end; state = "code"; continue
        if state == "code" and pair == "/*": state = "block"; index += 2; continue
        if state == "block" and pair == "*/": state = "code"; index += 2; continue
        if state == "code" and char in ('"', "'"): state = char
        elif state in ('"', "'") and char == "\\": index += 2; continue
        elif state in ('"', "'") and char == state: state = "code"
        if char in "\r\n": line_start = index + 1
        index += 1
    return markers


def _generated(items: list[str], newline: str) -> str:
    """Render the DZ script lines for the managed starter block."""
    lines: list[str] = []
    for item in items:
        # Backpacks attach to the player; other items go into inventory
        method = "CreateAttachment" if item in BACKPACK_ITEMS else "CreateInInventory"
        lines.extend((f'{newline}\t\titemEnt = player.GetInventory().{method}( "{item}" );',
                      f"{newline}\t\tif ( itemEnt )",
                      f'{newline}\t\t\titemEnt.SetHealth01( "", "", 1.0 );'))
        # A created water bottle is filled and readied for use
        if item == "WaterBottle":
            lines.extend((f"{newline}\t\tItemBase waterBottle = ItemBase.Cast( itemEnt );",
                          f"{newline}\t\tif ( waterBottle )",
                          f"{newline}\t\t{{",
                          f"{newline}\t\t\twaterBottle.SetLiquidType( LIQUID_WATER );",
                          f"{newline}\t\t\twaterBottle.SetQuantityMax();",
                          f"{newline}\t\t}}"))
    # End the block with a newline so marker placement stays clean
    return "".join(lines) + newline


def _matching_brace(text: str, opening: int) -> int:
    """Return the index of the brace that closes the body opening."""
    depth, index, state = 0, opening, "code"
    # Depth counts only braces outside strings and comments
    while index < len(text):
        pair = text[index:index + 2]
        char = text[index]
        if state == "code" and pair in ("//", "/*"):
            state = "line" if pair == "//" else "block"; index += 2; continue
        if state == "line" and char in "\r\n": state = "code"
        elif state == "block" and pair == "*/": state = "code"; index += 2; continue
        elif state == "code" and char in ('"', "'"): state = char
        elif state in ('"', "'") and char == "\\": index += 2; continue
        elif state in ('"', "'") and char == state: state = "code"
        elif state == "code" and char == "{": depth += 1
        elif state == "code" and char == "}":
            depth -= 1
            if depth == 0: return index
        index += 1
    raise ConfigurationFileError("StartingEquipSetup body is malformed")


def _masked_code(text: str) -> str:
    """Return the text with comment and string contents blanked out."""
    result = list(text)
    index, state = 0, "code"
    # Blanking keeps every offset aligned with the original text
    while index < len(text):
        pair, char = text[index:index + 2], text[index]
        if state == "code" and pair in ("//", "/*"):
            result[index:index + 2] = [" ", " "]
            state = "line" if pair == "//" else "block"; index += 2; continue
        if state == "code" and char in ('"', "'"):
            result[index] = " "; state = char
        elif state == "line":
            if char in "\r\n": state = "code"
            else: result[index] = " "
        elif state == "block":
            result[index] = " "
            if pair == "*/": result[index + 1] = " "; state = "code"; index += 2; continue
        elif state in ('"', "'"):
            result[index] = " "
            if char == "\\" and index + 1 < len(text):
                result[index + 1] = " "; index += 2; continue
            if char == state: state = "code"
        index += 1
    return "".join(result)
