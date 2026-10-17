"""The `editor_notes` of a same-profile backup restore preview (leaf 10.10).

The texts are of the Product Owner entries of 2026-10-16 in evidence 10.10. The entry 20:58:44 gives rows 1 to 9.
The entry 21:01:16 gives the skip reasons and row 7 parts A and B. The entry 21:55:02 gives the runtime profile
texts of rows 2, 4, 5 and 6. The entry 21:56:40 gives `original_corrupt` and the damaged-record sentences.

Review order: row 1 when something is restored, the skipped parts, `original_corrupt` (mission, then runtime).
Then the D5 rows follow, each with the mission sentence first, then rows 8 and 9. Last come the damaged working
baseline, the damaged last Apply record and the damaged content files.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

EDITOR_NOTES_HEADING = "Map editor records"
# An interrupted deletion can leave editor records for the ID of a restored profile. The preview shows the first
# text before the other notes. The result line follows the success line (Product Owner 2026-10-17 05:42:37).
REMNANT_NOTE = ("Map editor records of an earlier profile with this ID are still in the DayZ-ServerMan folder. An "
                "interrupted deletion left them. The restore moves them to the folder for deleted records before it "
                "starts, and it does not use them.")
REMNANT_MOVED = "Map editor records of an earlier profile with this ID were moved to the folder for deleted records."
CARRIED = ("This backup holds the map editor records of this profile. The restore replaces the map plan, the working "
           "baseline and the last Apply record of this profile with the ones of the backup.")
NO_ORIGINAL = ("DayZ-ServerMan has no saved original for this mission. The restore installs the saved original of "
               "the backup.")
SUPERSEDED = ("The saved original of this mission differs from the saved original of the backup. The restore makes "
              "the original of the backup the active original. It keeps the current original under a dated name. "
              "Nothing is deleted.")
RESTORED_COPY = ("The saved original of this mission differs from the saved original of the backup, and other "
                 "profiles use this mission: {profiles}. The current original stays active for all profiles. The "
                 "restore keeps the original of the backup as an inactive copy for this profile.")
SHARED = ("Other profiles use this mission or have map editor records for it: {profiles}. The restore does not "
          "change their records.")
# The runtime profile texts of rows 2, 4, 5 and 6 (Product Owner 21:55:02)
RUNTIME_NO_ORIGINAL = ("DayZ-ServerMan has no saved original for the runtime profile folder of this profile. The "
                       "restore installs the saved original of the backup.")
RUNTIME_SUPERSEDED = ("The saved original of the runtime profile folder differs from the saved original of the "
                      "backup. The restore makes the original of the backup the active original. It keeps the "
                      "current original under a dated name. Nothing is deleted.")
RUNTIME_RESTORED_COPY = ("The saved original of the runtime profile folder differs from the saved original of the "
                         "backup, and other profiles use this runtime profile folder: {profiles}. The current "
                         "original stays active for all profiles. The restore keeps the original of the backup as "
                         "an inactive copy for this profile.")
RUNTIME_SHARED = ("Other profiles use this runtime profile folder or have map editor records for it: {profiles}. "
                  "The restore does not change their records.")
NO_RECORDS = "This backup has no map editor records."
KEEPS_ORIGINAL = ("The restore keeps the current saved original. If the restored files differ from what the last "
                  "Apply wrote, the restore invalidates the last Apply record, and Apply then needs adoption of the "
                  "mission.")
APPLIED_MISMATCH = ("The last Apply record of the backup does not match the restored mission files. The restore "
                    "invalidates it. Apply then needs adoption of the mission.")
PLAN_SET_ASIDE = "The current map plan is not valid. The restore keeps it under a dated name."
BASELINE_CORRUPT = ("The current working baseline of this profile is damaged. The restore keeps it under a dated "
                    "name and installs or builds a new one.")
APPLIED_CORRUPT = ("The current last Apply record of this profile is damaged. The restore keeps it under a dated "
                   "name and does not use it.")
# Once for the restore, however many content files are kept (Product Owner 22:32:02)
CONTENT_CORRUPT = ("A damaged file of the map editor records of this profile is kept under a dated name. The restore "
                   "does not use it.")
SKIPPED = {
    "path_change": ("The mission path of this profile changed. The restore does not restore the map editor records "
                    "of this backup, and it does not change the current ones. Adopt the mission to end the path "
                    "change."),
    "mission_key": ("The mission of this backup is not the mission that this profile uses now. The restore does not "
                    "restore the map editor records of this backup."),
    "mission_unresolved": ("DayZ-ServerMan cannot find the mission folder of this profile. The restore does not "
                           "restore the map editor records of this backup."),
    "runtime_key": ("The runtime profile folder of this backup is not the one that this profile uses now. The "
                    "restore restores the map editor records of the mission, but not the saved original of the "
                    "runtime profile folder."),
}
# `original_corrupt` for each target class (Product Owner 21:56:40)
ORIGINAL_CORRUPT = {
    "mission": ("The saved original of the mission is not valid. The restore leaves it as it is and does not restore "
                "the map editor records for the mission. The server files of the backup are restored."),
    "runtime": ("The saved original of the runtime profile folder is not valid. The restore leaves it as it is and "
                "does not restore the map editor records for the runtime profile folder. The server files of the "
                "backup are restored."),
}
# The D5 row texts of each class, by row: install (2), supersede (4), restored copy (5), shared (6)
ROW_TEXTS = {
    "install": {"mission": NO_ORIGINAL, "runtime": RUNTIME_NO_ORIGINAL},
    "supersede": {"mission": SUPERSEDED, "runtime": RUNTIME_SUPERSEDED},
    "restored_copy": {"mission": RESTORED_COPY, "runtime": RUNTIME_RESTORED_COPY},
    "shared": {"mission": SHARED, "runtime": RUNTIME_SHARED},
}
CLASSES = ("mission", "runtime")
# Skip reasons after which nothing of the archive is restored
WHOLE_SKIPS = ("path_change", "mission_key", "mission_unresolved")


def profile_names(profile_ids: list[str], names: Mapping[str, str]) -> str:
    """Return the {profiles} form: display names joined with ", "; a deleted profile names its ID."""
    return ", ".join(names[pid] if pid in names else f"{pid} (deleted profile)" for pid in profile_ids)


def editor_notes(carried: bool, skipped: list[str], rows: Mapping[str, Any], names: Mapping[str, str]) -> list[str]:
    """Return the sentences of the editor part in their review order."""
    restored = carried and not any(reason in skipped for reason in WHOLE_SKIPS)
    notes = [CARRIED] if restored else []
    notes += [SKIPPED[reason] for reason in SKIPPED if reason in skipped]
    notes += [ORIGINAL_CORRUPT[name] for name in CLASSES if rows.get(f"original_{name}") == "corrupt"]
    # Nothing is restored and no D5 row applies
    if "path_change" in skipped or "mission_unresolved" in skipped:
        return notes
    if restored:
        notes += _row_notes(rows, names)
    # Row 7: a class that takes "Backup without an original"; part A only for a backup without editor records.
    # Without carried records it applies when a last Apply record exists.
    if not carried:
        notes += [NO_RECORDS, KEEPS_ORIGINAL] if rows.get("applied_current") or rows.get("applied_corrupt") else []
    elif "mission_key" in skipped or "runtime_key" in skipped or rows.get("original_mission") is None:
        notes.append(KEEPS_ORIGINAL)
    if restored and rows.get("applied_carried") == "invalidate":
        notes.append(APPLIED_MISMATCH)
    notes += [text for key, text in (("plan_set_aside", PLAN_SET_ASIDE), ("baseline_corrupt", BASELINE_CORRUPT),
                                     ("applied_corrupt", APPLIED_CORRUPT), ("content_corrupt", CONTENT_CORRUPT))
              if rows.get(key)]
    return notes


def _row_notes(rows: Mapping[str, Any], names: Mapping[str, str]) -> list[str]:
    """Return the D5 row texts of the restored classes: row by row, the mission sentence first (21:55:02)."""
    notes = []
    for row in ROW_TEXTS:
        for name in CLASSES:
            found, shared = rows.get(f"original_{name}"), list(rows.get(f"shared_{name}") or [])
            # Row 6 shows for any restored row except 5, which already names the profiles
            show = (found == row) if row != "shared" else bool(shared) and found in ("install", "keep", "supersede")
            if show:
                notes.append(ROW_TEXTS[row][name].format(profiles=profile_names(shared, names)))
    return notes
