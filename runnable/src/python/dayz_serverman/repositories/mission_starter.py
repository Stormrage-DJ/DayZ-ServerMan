"""Script primitives of the starter items in init.c: the routine, its item calls, and generated item lines.

`mission_starter_layer.py` builds the starter layer on them.
"""

from __future__ import annotations

import re

from .configuration_common import ConfigurationFileError

# Marker comments of the starter block that earlier versions placed among the mission's statements
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
