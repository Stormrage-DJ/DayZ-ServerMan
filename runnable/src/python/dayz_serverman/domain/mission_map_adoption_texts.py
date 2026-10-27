"""Product Owner texts of the adoption review (leaf 10.13), word for word.

The sources are the Product Owner entries of 2026-10-16 in evidence 10.1 (14:26:38, 14:35:24) and in
evidence 10.2 (14:54:33, 15:30:03). A text of evidence 10.2 replaces the text of the same slot in evidence 10.1.

ADOPT-26 is removed. The window and `map adopt` show these texts unchanged. A copy text has the variant of the
route that offered adoption.
"""

from __future__ import annotations

from typing import Any

from .mission_map_adoption import CAPTURE, COPY, KEPT_DIFFERENT, KEPT_SAME, MOVE, RESTORE, RETIRED

# ADOPT-7, the map notice of a copy: one text for each route (15:30:03)
NOTICE_PATH_CHANGE = ("The files of this mission match map editor output for {old_path}, which still exists or which "
                      "another profile uses. Apply is not possible until you adopt the mission.")
NOTICE_CAPTURE = ("The files of this mission match map editor output for {old_path}. Apply is not possible until you "
                  "adopt the mission.")
# ADOPT-8, the text after success, by case (copy: by route)
SUCCESS = {
    MOVE: "Adopted. The map plan and the records now belong to {new_path}.",
    COPY: "Adopted. {new_path} has its own records. The map plan of this profile is copied to it.",
    (COPY, CAPTURE): "Adopted. {new_path} has its own records. Its map plan stays as it was.",
    RETIRED: "Adopted. The map plan and the records are active for {new_path} again.",
    RESTORE: "Adopted. Apply no longer needs adoption for this mission.",
}
# ADOPT-12, what happened, by case (copy: by route)
HAPPENED = {
    MOVE: ("This mission moved from {old_path} to {new_path}. Adoption gives {new_path} the map plan and the map "
           "editor records of this profile. The old folder of records is kept as history."),
    COPY: ("{old_path} still exists or another profile uses it, so DayZ-ServerMan treats {new_path} as a copy. "
           "Adoption gives {new_path} its own map editor records."),
    (COPY, CAPTURE): ("The files of {new_path} match map editor output for {old_path}, so DayZ-ServerMan treats "
                      "{new_path} as a copy. Adoption gives {new_path} its own map editor records."),
    RETIRED: ("This mission is back at {new_path}. An earlier adoption retired the records of that path. Adoption "
              "keeps them as history and makes the records of {other_path} active for {new_path}."),
    RESTORE: ("A restore removed the last Apply record of this profile for this mission. Adoption ends this state. "
              "It keeps the map plan and the saved original."),
}
# ADOPT-13, before the list of the other profiles of the source key
OTHER_PROFILES = ("These profiles use {old_path} or have map editor records for it. Adoption does not change their "
                  "records or their mission files.")
# ADOPT-15, the intro of the records that adoption writes, by case (copy: by route)
INTRO = {
    MOVE: ("Adoption copies these records of this profile to {new_path}. It retires the old folder of records. That "
           "folder stays as history and is never applied."),
    COPY: ("Adoption writes these records for {new_path}. It retires the old folder of records of this profile and "
           "keeps it as history. The records of other profiles stay unchanged."),
    (COPY, CAPTURE): "Adoption writes these records for {new_path}. The records for {old_path} stay unchanged.",
    RETIRED: "Adoption writes these records. The old folder of records stays under a dated name and is never applied.",
    RESTORE: "Adoption closes the invalidated record. It writes no original, no map plan and no Apply record.",
}
# ADOPT-16, one row for each record kind
ROW_ORIGINAL = ("Saved original for {new_path}: a copy of the saved original of {old_path}. The copy keeps a link to "
                "its source. The original of {old_path} stays.")
ROW_RECORDS = {
    MOVE: "Map plan, working baseline and last Apply record: copied to the new path.",
    COPY: ("Map plan of this profile: copied to {new_path} as the next revision. Working baseline: built from the "
           "saved original. There is no last Apply record."),
    (COPY, CAPTURE): ("Working baseline for {new_path}: built from the saved original. There is no last Apply "
                      "record. The map plan stays as it is."),
}
ROW_LEDGER = ("Records of earlier Apply output: {count} repeated for the new path, so that DayZ-ServerMan recognizes "
              "its own output there.")
ROW_RETIRED = ("Old folder of records of this profile for {old_path}: marked as retired. It keeps its files and is "
               "never applied.")
ROW_EXISTING = "Existing folder of records for {new_path}: renamed to {history_name}. It is kept as history."
ROW_HISTORY = "Folder of the retired records: renamed to {history_name}. It is kept as history."
ROW_INVALIDATED = "Invalidated last Apply record: closed and kept under a dated name."
# ADOPT-18, what stays unchanged
UNCHANGED_FILES = "No mission file changes. Adoption does not write to the DayZ server folder."
UNCHANGED_SOURCE = ("The saved original and the output records of {old_path}, and the records of other profiles, stay "
                    "unchanged.")
UNCHANGED_NOTHING_DELETED = "Nothing is deleted. Old records stay in their folders."
UNCHANGED_PLAN = "The map plan of this profile stays as it is."
# Refusals: ADOPT-24 (no source), ADOPT-25 (a file differs; copy and move variants) and `nothing_to_adopt`
NO_SOURCE_TEXT = ("Adoption is not possible. DayZ-ServerMan finds no earlier mission path with map editor records and "
                  "a saved original that matches this mission.")
FILES_DIFFER_TEXT = {
    COPY: ("Adoption is not possible. {path} differs from the saved original and from the Apply output of {old_path}, "
           "so DayZ-ServerMan cannot show that this mission is a copy. It does not declare the current files as the "
           "original."),
    MOVE: ("Adoption is not possible. {path} differs from the saved original and from the Apply output of {old_path}, "
           "so DayZ-ServerMan cannot show that the files moved with the mission. It does not declare the current files "
           "as the original."),
}
# Without a file name, these words replace "{path}" in both ADOPT-25 texts
NO_FILE_NAME = "A mission file"
NOTHING_TO_ADOPT_TEXT = ("This mission needs no adoption. Its map plan and records already belong to the current "
                         "mission path.")
# ADOPT-28: the adoption is stale (also the REVISION_CONFLICT row of the kind in 10.35)
ADOPTION_STALE_TEXT = ("The profile, the settings, the mission files or the map editor records changed after the "
                       "preview. Nothing was adopted. Preview the adoption again and check it before you adopt.")
# PUBLICATION_FAILED under the lane kind (Product Owner 2026-10-17 11:41:56 in evidence 10.35)
ADOPTION_COMMITTED_TEXT = ("Adoption reached its commit point, but a later step failed. No mission file changed. The "
                           "map editor records can have changed. Preview the adoption again to see what is left to "
                           "adopt.")


# Records-only adoption (Architect 2026-10-17 09:30:36, Product Owner 09:31:28 in evidence 10.13): the old folder
# of this profile has no last-applied record and its key has no original. The key of these table variants.
RECORDS_ONLY = "records_only"
_RECORDS_ONLY_INTRO = ("Adoption writes this record for {new_path}. It retires the old folder of records of this "
                       "profile and keeps it as history.")
# Every records-only path change is a copy (Architect 2026-10-17 10:11:41 in evidence 10.13)
HAPPENED.update({
    (COPY, RECORDS_ONLY): ("{old_path} still exists or another profile uses it, so DayZ-ServerMan treats {new_path} as "
                           "a copy. No Apply wrote files for this profile at the old path, so there is no saved "
                           "original to copy. Adoption gives {new_path} the map plan of this profile."),
})
INTRO[(COPY, RECORDS_ONLY)] = _RECORDS_ONLY_INTRO
ROW_RECORDS[(COPY, RECORDS_ONLY)] = "Map plan of this profile: copied to {new_path} as the next revision."
SUCCESS[(COPY, RECORDS_ONLY)] = "Adopted. The map plan of this profile is copied to {new_path}."
UNCHANGED_NO_ORIGINAL = ("No saved original exists yet. The first Apply saves one, after the usual check of the "
                         "mission files.")
UNCHANGED_OTHER_PROFILES = "The records of other profiles stay unchanged."
# The retired case, after the history-folder and retired-marker rows (Product Owner 09:29:14 in evidence 10.13):
# step 2, the fresh active folder, and step 4, the D5 step on the original of the active key
ROW_FRESH = ("Fresh folder of records for {new_path}: the map plan, the working baseline and the last Apply record of "
             "{other_path} are copied to it.")
ROW_RETIRED_ORIGINAL = {
    "installed": "Saved original for {new_path}: installed from the records of {other_path}.",
    "active": ("Saved original for {new_path}: the original of {other_path} becomes active. The current original is "
               "kept under a dated name."),
    "shared": ("Saved original for {new_path}: the current original stays active, because other profiles use this "
               "mission. The original of {other_path} is kept as an inactive copy for this profile."),
}


def variant(table: dict[object, str], case: str, route: str | None) -> str | None:
    """Return the text of the route variant of a case, else the text of the case, else None."""
    return table.get((case, route), table.get(case))


# ADOPT-16 for an original that the new key already has; it is kept (Product Owner 2026-10-17 11:43:27, 10.35)
ROW_KEPT = {
    KEPT_SAME: "Saved original for {new_path}: kept. It already holds the same files as the saved original of "
               "{old_path}.",
    KEPT_DIFFERENT: ("Saved original for {new_path}: kept. It differs from the saved original of {old_path}, so the "
                     "working baseline is built again from it. The last Apply record is moved only if the mission "
                     "files still match what that Apply wrote."),
}


def review_texts(case: str | None, route: str | None, values: dict[str, Any], retired: list[str], *,
                 existing: bool = False, others: bool = False, records_only: bool = False,
                 original_step: str | None = None, kept_original: str | None = None) -> dict[str, Any]:
    """Return the texts of the adoption answer for a case and route; a row that does not exist for it is left out.

    `values` fills the placeholders. `retired` holds the display path of each old folder that adoption retires.
    A records-only adoption has no original, baseline or ledger row. `original_step` is the D5 outcome of the
    retired case: `installed`, `active`, `shared`, or None for no row.
    """
    def fill(text: str | None) -> str | None:
        return None if text is None else text.format(**values)
    if case is None:
        return {"notice": None, "happened": None, "other_profiles_text": None, "intro": None, "records": [],
                "unchanged": [], "success": None}
    key = RECORDS_ONLY if records_only else route
    rows: list[str | None] = [ROW_HISTORY] if case == RETIRED else []
    if case in (MOVE, COPY):
        original = ROW_KEPT[kept_original] if kept_original else ROW_ORIGINAL
        rows = [ROW_EXISTING if existing else None, None if records_only else original,
                variant(ROW_RECORDS, case, key), ROW_LEDGER if values["count"] else None]
    rows = [fill(row) for row in rows if row is not None]
    rows += [ROW_RETIRED.format(**dict(values, old_path=path)) for path in retired]
    if case == RETIRED:
        rows += [fill(ROW_FRESH)] + ([fill(ROW_RETIRED_ORIGINAL[original_step])] if original_step else [])
    rows += [ROW_INVALIDATED] if case == RESTORE else []
    if records_only:
        kept = [UNCHANGED_NO_ORIGINAL, UNCHANGED_OTHER_PROFILES]
    else:
        kept = [UNCHANGED_SOURCE] if case in (MOVE, COPY) else []
        kept += [UNCHANGED_PLAN] if route == CAPTURE else []
    # A records-only copy matched no editor output, so no ADOPT-7 notice applies to it
    notice = NOTICE_CAPTURE if route == CAPTURE else NOTICE_PATH_CHANGE if case == COPY else None
    notice = None if records_only else notice
    return {"notice": fill(notice), "happened": fill(variant(HAPPENED, case, key)),
            "other_profiles_text": fill(OTHER_PROFILES) if others else None,
            "intro": fill(variant(INTRO, case, key)), "records": rows,
            "unchanged": [UNCHANGED_FILES, *(fill(text) for text in kept), UNCHANGED_NOTHING_DELETED],
            "success": fill(variant(SUCCESS, case, key))}
