"""Product Owner texts of the map commands (10.4: `map show`; 10.15: `map save`, `map set-aside`; 10.6:
`map export`; 10.33: `map import`; 10.7: `map copy`); each map leaf adds its own texts here.

Texts marked "window" are exact copies of the editor window: `tests/test_cli_map_wording.py` compares
them with `map_editor_wording.js` and `map_editor_encounter_wording.js`. The CLI lines name a command and
are exempt from that comparison (Product Owner 2026-10-16 14:26:38, PO-6, in evidence 10.1).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..domain.mission_map_loot_report_texts import UNAVAILABLE_TEXTS
from ..domain.mission_map_terrain import (
    OPERATION_DETECT_BOUNDS, OPERATION_TERRAIN_IDENTITY, OPERATION_TIER_LAYER, OPERATION_TIER_WRITES,
    TIER_OPERATION_PREFIX,
)
from .output import Line, TypeText, sentence

# Labels of the lines of `map show`
MISSION_LABEL, STATE_LABEL, REVISION_LABEL = "Mission", "Plan state", "Revision"
TERRAIN_LABEL, BOUNDS_LABEL, WRITES_LABEL = "Terrain", "Terrain bounds", "Tier writes"
CAPACITY_LABEL = "Global container capacity"
# window: unknownTerrain, boundsConfirmed, boundsUnconfirmed
UNKNOWN_TERRAIN = "Unknown terrain"
BOUNDS_TEXTS = {True: "Bounds confirmed", False: "Bounds not confirmed"}
# The value of the "Terrain bounds" line (Product Owner 2026-10-16 15:28:30); the window texts above keep their words
BOUNDS_LINE = {True: "confirmed", False: "not confirmed"}
WRITES_TEXTS = {True: "Available", False: "Not available"}
# The draft-changes summary: the load answer compares the saved plan with the last Apply
APPLIED_CHANGES = "The saved plan has changes that no Apply has written."
APPLIED_MATCHES = "The saved plan matches the last Apply."
# A valid plan with no objects, global capacity 0 and no changes (Product Owner 2026-10-17 13:41:00 in evidence 10.31)
APPLIED_EMPTY = "The saved plan is empty."
# window: objectList, noObjects, objectTypes, capacityPresets and the tier "Inherit"
OBJECTS_HEADING = "Objects in priority order"
OBJECT_HEADINGS = ("ID", "Name", "Kind", "Tier", "Zone capacity", "Availability")
OBJECT_KINDS = {"loot_circle": "Loot circle", "loot_polygon": "Loot polygon", "encounter": "Encounter"}
ZONE_PRESETS = {"sparse": "Sparse -25%", "normal": "Normal 0%", "rich": "Rich +50%", "very_rich": "Very Rich +100%"}
INHERIT = "Inherit"
NO_OBJECTS = "No objects in this plan."
AVAILABLE, NOT_AVAILABLE = "Available", "Not available"
# The capabilities that the mission cannot support, with the reason of each
UNSUPPORTED_HEADING = "Not available on this mission"
OPERATION_NAMES = {
    OPERATION_TERRAIN_IDENTITY: "Terrain identity", OPERATION_DETECT_BOUNDS: "Detecting bounds",
    OPERATION_TIER_LAYER: "Original tiers", OPERATION_TIER_WRITES: "Tier writes",
}
TIER_OPERATION = "Tier {name}"
NO_REASON_TEXT = "This mission cannot support this operation."
# window: mapTierReasons, the reasons of Original tiers and Detecting bounds
TIER_REASONS = {
    "areaflags_missing": "This mission has no areaflags.map, so its original tiers cannot be shown.",
    "mission_file_unreadable": "A mission file could not be read.",
    "tier_header_invalid": "The tier map of this mission is not valid.",
    "limits_missing": "This mission has no cfglimitsdefinition.xml, so its tier names are not known.",
    "limits_invalid": "The value flags in cfglimitsdefinition.xml are not valid.",
    "tier_unknown": "This tier is not in the tier map of this mission.",
    "over_budget": "The original tiers of this mission are too large to show on the map. Inspect still shows the "
                   "tier of each point.",
}
# window: mapEncounterWordsNames and mapEncounterWordsInfected, for the encounter providers that are unavailable
PROVIDER_NAMES = {"native_infected": "Infected", "ai_bandits": "AI_Bandits", "native_wolf": "Wolves",
                  "native_bear": "Bears"}
INFECTED_FILES = "cfgenvironment.xml, env/zombie_territories.xml or db/events.xml"
INFECTED_REASONS = {
    "territory_not_registered": "cfgenvironment.xml does not register env/zombie_territories.xml.",
    "no_matching_events": "no zone name in env/zombie_territories.xml is also an event name in db/events.xml.",
    "provider_file_missing": f"{INFECTED_FILES} is missing.",
    "provider_file_unreadable": f"{INFECTED_FILES} cannot be read.",
    "provider_path_invalid": f"{INFECTED_FILES} is a link or is outside the mission folder.",
    "provider_configuration_invalid": f"{INFECTED_FILES} is not valid XML of the expected form.",
}
INFECTED_PREFIX = "Infected areas are not available on this mission: "
INFECTED_UNKNOWN = "the check gave the unknown result {code}."
# The row name of the infected provider under the heading "Not available on this mission" (no window text)
INFECTED_ROW_NAME = "Infected areas"
PATH_CHANGE_HEADING = "Earlier mission paths with a map plan"
INTERRUPTED_EVIDENCE = "Interrupted save files in the plan folder: {names}. DayZ-ServerMan keeps them as evidence."

# Plan state values; window: mapPlanDetails for OTHER_ASSOCIATION
PLAN_STATES = {
    "VALID": "Valid", "MISSING": "No saved plan", "CORRUPT": "Not valid", "INVALID_PLAN": "Not valid",
    "FUTURE_SCHEMA": "Saved by a newer DayZ-ServerMan", "NEWER_PLAN": "Saved by a newer DayZ-ServerMan",
    "OTHER_ASSOCIATION": "The saved plan belongs to another profile or mission.",
}
# window: mapReadOnlyReasons for invalid_saved_data and association_mismatch, and the fallback of readOnly
READ_ONLY_FALLBACK = "Read only: this plan cannot be edited."
READ_ONLY_TEXTS = {
    "invalid_saved_data": "Read only: the saved map plan is not valid. Set aside invalid plan keeps it under a dated "
                          "name and starts a new, empty plan.",
    "association_mismatch": "Read only: the saved map plan does not match the mission folder or runtime profile "
                            "folder of this profile. Correct the profile settings, then load the plan again.",
    # New window texts ADOPT-4 to ADOPT-6 (Product Owner 2026-10-16 14:26:38 and 14:35:24); leaf 10.16 puts them
    # into the window, so the wording test pins these three literally
    "mission_path_changed": "Read only: the mission path changed. The map plan shown belongs to an earlier path. "
                            "Adopt the mission to use its map editor records for the new path.",
    "returned_to_earlier_path": "Read only: this mission returned to an earlier path. Adopt the mission to use its "
                                "map editor records again.",
    "adoption_required": "Apply is not possible: a restore removed the last Apply record of this profile for this "
                         "mission. Adopt the mission to continue.",
}
# The reasons whose text names adoption: no adoption line follows them (Product Owner 20:58:44 in evidence 10.10)
ADOPTION_REASONS = ("mission_path_changed", "returned_to_earlier_path", "adoption_required")
# CLI lines of leaf 10.13 (Product Owner 2026-10-16 14:35:24 in evidence 10.1): `map show` for each case of the
# load field `adoption`, and `map preview` and `map apply` for `capture.adoption`
ADOPTION_LINES = {
    "move": "Adoption is needed: this mission moved. Run map adopt to review it.",
    "copy": "Adoption is needed: this mission is a copy of another mission path. Run map adopt to review it.",
    "retired": "Adoption is needed: this mission returned to an earlier path. Run map adopt to review it.",
    "restore": "Adoption is needed: a restore removed the last Apply record of this profile for this mission. Run "
               "map adopt to review it.",
}
CAPTURE_ADOPTION_LINE = ("Adoption is needed: the files of this mission match map editor output for another mission "
                         "path. Run map adopt to review it.")

# `map save` and `map set-aside` (leaf 10.15): the subject of the `--expect-revision` text, and the success line
PLAN_SUBJECT = "map plan"
SAVED = "Map plan saved as revision {n}."
# window: setAsideMessage, the review intro after the state detail and the problems of the load
SET_ASIDE_MESSAGE = ("The plan file stays in its folder under a dated name. Nothing is deleted, and mission files do "
                     "not change. The editor then starts a new, empty plan. The new plan does not contain the objects "
                     "of the invalid plan.")
# window: setAsideDone and setAsideOpen; the success line is the two texts in this order
SET_ASIDE_DONE = "The invalid plan was kept as {name} in its folder."
SET_ASIDE_OPEN = "A new, empty plan is open."
# window: setAsideNotInvalid; the CLI line "Run map show." follows it
SET_ASIDE_NOT_INVALID = ("Not set aside: The saved map plan is valid now, or it is no longer there. Reload plan "
                         "shows its current state.")
# Refusals of `map save` and `map set-aside` (Product Owner 2026-10-16 16:25:27 in evidence 10.15); "map show"
# is text to type
PIN_WITHOUT_PLAN = ("There is no saved map plan, so revision {pinned} does not exist. Nothing was changed. Run map "
                    "show for the state of the plan.")
STALE_PLAN = ("The saved map plan changed after it was read. Nothing was changed. Run map show for its current "
              "state, then run the command again.")
# The `--from-file` cases (exit 2); a JSON value that is not an object keeps `invalid_file` of `wording.py`
FILE_UNREADABLE = "The file {path} cannot be read. Check the path and the file."
FILE_TOO_LARGE = "The file {path} is larger than 8 MiB."
FILE_NOT_UTF8 = "The file {path} is not UTF-8 text."
FILE_NOT_JSON = "The file {path} is not valid JSON. A repeated key, NaN and infinity are not allowed."

# `map export` (leaf 10.6): Product Owner 2026-10-16 14:26:38 in evidence 10.1, with the protected-folder text of
# 14:35:24 (DC-3). The exists refusal is also the text of a file that appears after the check (exit 4)
EXPORT_WRITTEN = "Map plan written to {path}."
EXPORT_EXISTS = "{path} exists already. Nothing was written. Add --overwrite to replace it."
EXPORT_NO_FOLDER = "The folder of {path} does not exist. Create the folder first."
EXPORT_FOLDER_OR_LINK = "{path} is a folder or a link. Name a file."
EXPORT_PROTECTED = ("{path} is in a folder that DayZ-ServerMan uses: the DayZ server folder, the DayZ-ServerMan "
                    "folder or the backup folder. Name a file in another folder.")
EXPORT_NOT_WRITTEN = "The file {path} could not be written correctly. DayZ-ServerMan removed it."

# `map import` (leaf 10.33), later also `map copy` (10.7): the compatibility review and the success lines (Product
# Owner 2026-10-16 14:26:38 in evidence 10.1). The refusals are the bridge texts of the transfer coordinator
TRANSFER_HEADING = "Compatibility"
TERRAIN_SAME = "Terrain: same as this mission"
TERRAIN_DIFFERS = "Terrain: {imported} in the file, {own} in this mission"
BOUNDS_SAME = "Bounds: same"
BOUNDS_DIFFER = "Bounds: the file differs by X {x}, Z {z}, width {w} and height {h} metres"
FILE_OBJECTS = "Objects in the file: {count}"
NEW_IDS = "Objects that get a new ID: {count}"
INSERTIONS = {"replace": "Replace: the objects of the file replace the objects of this plan.",
              "top": "Merge: the objects of the file go above all objects of this plan, in their order."}
CAPACITY_MERGE = "Global container capacity: {destination} stays. The file has {imported}."
CAPACITY_REPLACE = "Global container capacity: {imported}."
UNAVAILABLE_HEADING = "Objects that are not available on this mission"
EXCLUDED_HEADING = "Objects left out because they do not fit the bounds"
WITH_LINKED_LOOT = "{name} (with its linked loot)"
UNMAPPED_HEADING = "Items that are not imported"
DISCARDED = "Fields of the file that are not kept: {count}"
# The result line with its singular forms (Product Owner 2026-10-17 03:37:11 in evidence 10.33), keyed by
# (one object, one vertex)
RESULT_FORMS = {
    (False, False): "The plan will have {objects} objects and {vertices} polygon vertices.",
    (True, False): "The plan will have 1 object and {vertices} polygon vertices.",
    (False, True): "The plan will have {objects} objects and 1 polygon vertex.",
    (True, True): "The plan will have 1 object and 1 polygon vertex.",
}
PLACEMENT_REVIEW = "The terrain or the bounds differ. Check the placement of every object before you continue."
IMPORTED = "Map plan imported as revision {n}."
COPIED = "Map plan copied as revision {n}."
LEFT_OUT = "{count} objects were left out."
LEFT_OUT_ONE = "1 object was left out."
# The reason `provider_unavailable` of an unavailable object: the provider read did not return its provider
# (Product Owner 2026-10-17 04:07:15 in evidence 10.33)
PROVIDER_UNREAD = "DayZ-ServerMan could not read whether this mission supports this kind of encounter."
# The labels that name a file, by slot: the import forms above and the copy forms. The copy forms are from the
# Product Owner entry 2026-10-17 04:37:12 in evidence 10.7. Every other label of the review is shared
IMPORT_LABELS = {"terrain": TERRAIN_DIFFERS, "bounds": BOUNDS_DIFFER, "objects": FILE_OBJECTS,
                 "replace": INSERTIONS["replace"], "top": INSERTIONS["top"], "capacity": CAPACITY_MERGE,
                 "discarded": DISCARDED}
COPY_LABELS = {
    "terrain": "Terrain: {imported} in the source profile, {own} in this mission",
    "bounds": "Bounds: the source plan differs by X {x}, Z {z}, width {w} and height {h} metres",
    "objects": "Objects in the source plan: {count}",
    "replace": "Replace: the objects of the source plan replace the objects of this plan.",
    "top": "Merge: the objects of the source plan go above all objects of this plan, in their order.",
    "capacity": "Global container capacity: {destination} stays. The source plan has {imported}.",
    "discarded": "Fields of the source plan that are not kept: {count}",
}
# The command words in the transfer texts, as text to type
TRANSFER_TYPED = ("map show", "--confirm-listed", "--mode replace", "--mode merge", "--from-profile")


def typed_show(text: str) -> Line:
    """Return a text as one line, with each "map show" in it as text to type."""
    pieces = text.split("map show")
    parts: list[Any] = [pieces[0]]
    for piece in pieces[1:]:
        parts += [TypeText("map show"), piece]
    return sentence(*parts)


def typed(text: str, fragments: tuple[str, ...] = TRANSFER_TYPED) -> Line:
    """Return a text as one line, with each command word of `fragments` in it as text to type."""
    pattern = "(" + "|".join(re.escape(fragment) for fragment in sorted(fragments, key=len, reverse=True)) + ")"
    # re.split keeps the matched words at the odd indexes
    pieces = re.split(pattern, text)
    return sentence(*(TypeText(piece) if index % 2 else piece for index, piece in enumerate(pieces)))


def _run(command: str) -> Line:
    """Return the CLI line "Run <command>." with the command as text to type."""
    return sentence("Run ", TypeText(command), ".")


def read_only_way_out(reason: object) -> Line | None:
    """Return the CLI line that follows the window text of a read-only reason, or None when there is none."""
    if reason == "invalid_saved_data":
        return _run("map set-aside")
    if reason in ADOPTION_REASONS:
        return _run("map adopt")
    if reason == "association_mismatch":
        return sentence("Run ", TypeText("profile edit"), " to correct the profile, then run ", TypeText("map show"),
                        ".")
    return None


def adoption_line(field: object, *, capture: bool = False) -> Line | None:
    """Return the adoption line of an offer field, with `map adopt` as text to type, or None without an offer.

    `capture` selects the line of `capture.adoption`, which is offered only for the case `copy`.
    """
    case = field.get("case") if isinstance(field, Mapping) else None
    text = (CAPTURE_ADOPTION_LINE if case == "copy" else None) if capture else ADOPTION_LINES.get(str(case))
    return None if text is None else typed(text, ("map adopt",))


def unsupported_reason(operation: str, reason: object) -> str:
    """Return the window or backend text of the reason that an operation is not available (PO-6)."""
    if operation in (OPERATION_TIER_LAYER, OPERATION_DETECT_BOUNDS):
        table: Mapping[str, str] = TIER_REASONS
    else:
        table = UNAVAILABLE_TEXTS
    return table.get(str(reason), NO_REASON_TEXT)


def operation_name(operation: str) -> str:
    """Return the Product Owner name of an operation; a tier that the value layer cannot hold is "Tier <name>"."""
    if operation.startswith(TIER_OPERATION_PREFIX):
        return TIER_OPERATION.format(name=operation[len(TIER_OPERATION_PREFIX):])
    return OPERATION_NAMES.get(operation, operation)


def provider_reason(provider: str, entry: Mapping[str, Any]) -> str:
    """Return the window text of an unavailable provider; the other providers carry their own text."""
    if provider == "native_infected":
        code = str(entry.get("reason"))
        return INFECTED_PREFIX + INFECTED_REASONS.get(code, INFECTED_UNKNOWN.format(code=code))
    return str(entry.get("reason"))


def provider_row(provider: str, entry: Mapping[str, Any]) -> str:
    """Return the row of an unavailable provider under the heading; the infected row has no repeated prefix."""
    if provider == "native_infected":
        code = str(entry.get("reason"))
        return f"{INFECTED_ROW_NAME}: {INFECTED_REASONS.get(code, INFECTED_UNKNOWN.format(code=code))}"
    return f"{PROVIDER_NAMES[provider]}: {provider_reason(provider, entry)}"
