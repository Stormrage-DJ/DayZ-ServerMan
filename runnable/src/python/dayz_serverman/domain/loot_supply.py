"""Loot supply rules: item groups of the central economy and the percentage scaling of their counts.

A supply percentage scales `nominal` and `min` of every item type of a group from its original
values. The fill percentage scales `quantmin` and `quantmax` of loose ammunition and magazines. The
magazine percentage scales the chance that a weapon spawns with a magazine. The rules are the same
for every map: a group is found from the item category and the class name, not from a map list.
"""

from __future__ import annotations

import re

# Supply groups in the order that the window shows them
SUPPLY_GROUPS = (
    "weapons", "attachments", "magazines", "ammo", "ammo_boxes", "explosives",
    "food", "medical", "tools", "clothing", "containers",
)
# Fill percentage of loose ammunition and magazines
FILL_KEY = "ammo_fill"
# Groups whose quantity the fill percentage scales
FILL_GROUPS = frozenset({"ammo", "magazines"})
# Every value of the loot supply target: one key per group, and the fill percentage
SUPPLY_KEYS = frozenset((*(f"supply_{group}" for group in SUPPLY_GROUPS), FILL_KEY))
# The value of the magazine chance target
MAGAZINE_KEY = "magazine_chance"
# Limits of every loot percentage: from 0 through 300 in steps of 5
PERCENT_MIN, PERCENT_MAX, PERCENT_STEP = 0, 300, 5
# The percentage that keeps the original values
ORIGINAL_PERCENT = 100

# Medical supplies, found by class name because the game files keep them in the tools category
MEDICAL_ITEMS = frozenset(name.lower() for name in (
    "BandageDressing", "BloodBagEmpty", "BloodBagIV", "BloodTestKit", "CharcoalTablets",
    "ChelatingTablets", "DisinfectantAlcohol", "DisinfectantSpray", "Epinephrine", "IodineTincture",
    "Morphine", "PainkillerTablets", "PurificationTablets", "SalineBag", "SalineBagIV", "StartKitIV",
    "TetracyclineAntibiotics", "Thermometer", "VitaminBottle", "AntiChemInjector", "Splint",
    "Defibrillator",
))
# Weapon parts in the weapons category. The name ends with a part word, apart from a colour or size suffix.
# Part words: optic, stock, handguard, suppressor, compensator, bayonet, light and goggles.
ATTACHMENT_NAME = re.compile(
    r"(?:optic|bttstck|hndgrd|suppressor|compensator|bayonet|light|goggles)(?:_[a-z0-9]+)*$", re.IGNORECASE)
# Categories that map to one supply group as they are
CATEGORY_GROUPS = {"explosives": "explosives", "food": "food", "tools": "tools",
                   "clothes": "clothing", "containers": "containers"}


def supply_key(group: str) -> str:
    """Return the value key of a supply group; the prefix keeps the key apart from ordinary words."""
    return f"supply_{group}"


class LootSupplyError(ValueError):
    """Raised when a loot percentage is not valid."""
    pass


def supply_group(name: str, category: str | None) -> str | None:
    """Return the supply group of one item type, or None when no supply percentage changes it.

    Types without a category and event loot (`lootdispatch`) have no group.
    """
    if category is None:
        return None
    lowered = name.lower()
    if lowered in MEDICAL_ITEMS:
        return "medical"
    # Ammunition and magazines keep their class prefixes on every map and in most mods
    if lowered.startswith("ammobox_"):
        return "ammo_boxes"
    if lowered.startswith("ammo_"):
        return "ammo"
    if lowered.startswith("mag_"):
        return "magazines"
    if category == "weapons":
        return "attachments" if ATTACHMENT_NAME.search(name) else "weapons"
    return CATEGORY_GROUPS.get(category)


def validate_percent(value: object, name: str) -> int:
    """Return a loot percentage from 0 through 300 in steps of 5, or raise."""
    if (not isinstance(value, int) or isinstance(value, bool)
            or not PERCENT_MIN <= value <= PERCENT_MAX or value % PERCENT_STEP):
        raise LootSupplyError(f"{name} must be a percentage from {PERCENT_MIN} through {PERCENT_MAX} "
                              f"in steps of {PERCENT_STEP}")
    return value


def scale_count(value: int, percent: int) -> int:
    """Return a count scaled by a percentage, rounded half up; a count above 0 stays at least 1."""
    if value <= 0 or percent == 0:
        return 0 if percent == 0 else value
    return max(1, (value * percent + 50) // 100)


def scale_counts(nominal: int, minimum: int, percent: int) -> tuple[int, int]:
    """Return the scaled nominal and minimum; rounding keeps their order, also a minimum above the nominal."""
    return scale_count(nominal, percent), scale_count(minimum, percent)


def scale_fill(low: int, high: int, percent: int) -> tuple[int, int]:
    """Return the scaled quantity range in percent; -1 means no quantity and stays, and 100 is the top."""
    if low < 0 or high < 0:
        return low, high
    high_scaled = min(100, scale_count(high, percent))
    return min(min(100, scale_count(low, percent)), high_scaled), high_scaled


def unscale_count(value: int, percent: int) -> int:
    """Return the count that a percentage most likely scaled to this value, for a value edited by hand."""
    if percent in (0, ORIGINAL_PERCENT):
        return value
    return (value * 100 + percent // 2) // percent


def scale_chance(chance: float, percent: int) -> float:
    """Return a spawn chance scaled by a percentage, from 0.0 through 1.0, rounded to three decimals."""
    return min(1.0, round(chance * percent / 100, 3))
