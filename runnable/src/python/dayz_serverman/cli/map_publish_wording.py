"""Product Owner and window texts of `map preview` and `map apply` (leaf 10.5).

Texts marked "window" are exact copies of `map_editor_preview_words.js` and `map_editor_encounter_wording.js`.
They are the Product Owner texts of 2026-10-15 15:57:10, 16:03:40 and 18:04:08 in evidence 8.10, and the 7.3
texts. `tests/test_cli_map_publish_wording.py` compares them.

The CLI lines name options, so they replace the window choices. They are exempt from that comparison (Product
Owner 2026-10-16 14:26:38 in evidence 10.1, PO-2 and PO-6). Backend texts of the answer are never copied here.
"""

from __future__ import annotations

import re
from typing import Any

from .output import Line, Part, TypeText, Value, sentence

# window: headings of the Apply sections, in the order of the dialog
REFUSALS_HEADING, NOTICES_HEADING = "Apply is not possible", "Check before you apply"
EXTERNAL_HEADING, ENCOUNTER_CHOICES_HEADING = "Files changed outside the map editor", "Unavailable encounters"
CHANGES_HEADING, ENCOUNTERS_HEADING = "Map plan changes", "Encounters"
LOOT_HEADING, FILES_HEADING = "Loot report", "Mission files"
# window: stop.apply, the footer line of the Apply preview
STOP_SERVER = "Stop the DayZ server before you apply. DayZ-ServerMan does not apply while the server runs."
# window: bounds, others.apply and deletedProfile
BOUNDS_NOT_CONFIRMED = ("The terrain bounds of this plan are not confirmed. Apply cannot write loot tiers until you "
                        "confirm the bounds under Terrain bounds.")
OTHER_PROFILES = ("Other profiles use this mission folder or have map editor records for it. Apply changes the "
                  "mission files for every profile that uses this folder.")
DELETED_PROFILE = "{profile_id} (deleted profile)"
# window: replaceNote, linkedLoot and noPrevious
REPLACE_NOTE = "DayZ-ServerMan saves a copy of the current file before it replaces the file."
LINKED_LOOT = "Its linked loot is still applied."
NO_PREVIOUS = "No zone or encounter list of a last Apply is recorded, so every zone and encounter shows as added."
# window: the list labels
ADDED, CHANGED, REMOVED = "Added", "Changed", "Removed"
WRITTEN = "Infected encounters that Apply writes"
# window: excluded; the plan-excluded label of `map preview` is the same text (PO-A1)
EXCLUDED = "Excluded in the plan. Apply does not write these encounters."
LOOT_UNAVAILABLE = "Zones and encounters with a loot tier that Apply cannot write"
LOOT_CAPACITY = "Building groups with a capacity that Apply changes"
LOOT_KEPT = "Buildings that do not get the capacity of this plan"
AFFECTED = "Files that Apply changes"
COORDINATED = "Changes made by other DayZ-ServerMan settings, for example medical loot zones. Apply keeps them."
# window: global, "Global container capacity: {current}" and, when the last Apply value differs, the suffix
GLOBAL = "Global container capacity: {current}"
GLOBAL_APPLIED = " (last Apply: {applied})"
# window (7.3): banditReason, wolfBearReason and notOffered, the reasons of an unavailable encounter
BANDIT_REASON = "Bandit placement is not available on this terrain: no verified ground height source."
WOLF_BEAR_REASON = ("Wolf and bear territories are not supported in this version; the pack territory mapping is not "
                    "verified.")
NOT_OFFERED = "This mission does not offer the infected event {event}."

# CLI lines of `map apply` (Product Owner 2026-10-16 14:26:38 in evidence 10.1). The review prints the choice line
# of each file and encounter with its path or ID
FILE_CHOICE = ("Choose for each file: add --keep-change {path} to keep the change, or --replace-file {path} to "
               "replace the file.")
ENCOUNTER_CHOICE = ("Apply cannot write these encounters on this mission. Add --exclude-encounter {id} for each one "
                    "to leave it out of this Apply.")
# A missing choice (exit 4): this line follows the 8.7 `choice_required` text
MISSING_CHOICE = ("Add --keep-change PATH or --replace-file PATH for each file, and --exclude-encounter ID for each "
                  "encounter.")
# A path or ID that the review does not list, and a path in both options (exit 2)
NOT_IN_REVIEW = "{value} is not in the review. Use a path or ID that the review lists."
IN_BOTH = "{path} is in both --keep-change and --replace-file. Choose one."
# The infected values (Product Owner 2026-10-16 17:17:52 in evidence 10.5): CLI texts, no window copy
INFECTED_HEADING = "Values that limit infected"
INFECTED_EVENT = "{event}: nominal {nominal}, max {max}"
ZOMBIE_MAX_COUNT = "ZombieMaxCount in db/globals.xml: {value}"
ZONE_SPAWN_DIST = "ZoneSpawnDist in db/globals.xml: {value}"
# A value that could not be read from the file (Architect 2026-10-14 18:32:23 in evidence 7.6); JSON has null
NOT_READ = "not read"
# The start of the host message of a late file change under Apply (7c, `TARGET_CHANGED_TEXT` of the backend)
LATE_CHANGE_START = "Apply stopped because "

# An option with the value that follows it ("--keep-change {path}", "--keep-change PATH"), or a placeholder
TOKEN = re.compile(r"--[a-z]+(?:-[a-z]+)*(?: (?:\{\w+\}|[A-Z]+))?|\{\w+\}")


def filled(text: str, **values: Any) -> Line:
    """Return a text with its placeholders filled.

    An option with its value is text to type. A placeholder alone is the given part (an echo or a value), or a
    stored value.
    """
    pieces: list[str | Part] = []
    last = 0
    for found in TOKEN.finditer(text):
        pieces.append(text[last:found.start()])
        token = found.group(0)
        if token.startswith("--"):
            pieces.append(TypeText(re.sub(r"\{(\w+)\}", lambda name: _text(values[name.group(1)]), token)))
        else:
            value = values[token[1:-1]]
            pieces.append(Value(value) if isinstance(value, str) else value)
        last = found.end()
    pieces.append(text[last:])
    return sentence(*pieces)


def _text(value: Any) -> str:
    """Return the text of a value or of a typed part."""
    return str(getattr(value, "text", value))
