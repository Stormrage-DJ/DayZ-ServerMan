"""Window texts of `map restore` and `map recover` (leaf 10.32).

Each text is an exact copy of `map_editor_preview_words.js` or `map_editor_publish_words.js`. They are the Product
Owner texts of 2026-10-15 15:57:10 (decisions 6a and 6b) and 16:03:40 in evidence 8.10. The CLI prints them word
for word (PO-6 and PO-7, Product Owner 2026-10-16 14:26:38 in evidence 10.1).
`tests/test_cli_map_restore_wording.py` compares them with the window files.

The 8.8 texts come with the answer or from `domain/mission_map_restore_texts.py`. They are never copied here.
"""

from __future__ import annotations

# window: headings of the restore sections, in the order of the dialog
REFUSALS_HEADING, NOTICES_HEADING = "Restore is not possible", "Check before you restore"
WRITES_HEADING = "What the restore writes"
OVERWRITTEN_HEADING = "Outside changes that the restore overwrites"
KEPT_HEADING = "Outside changes that the restore keeps"
# window: stop.restore, the footer line of the restore preview
STOP_SERVER = "Stop the DayZ server before you restore. DayZ-ServerMan does not restore while the server runs."
# window: others.previous, the intro line of the other profiles in Restore previous state
OTHER_PROFILES = ("Other profiles use this mission folder or have map editor records for it. The restore changes the "
                  "mission files for every profile that uses this folder.")
# window: restoreAffected and restoreCoordinated, the list labels
AFFECTED = "Files that the restore changes"
COORDINATED = ("Changes made by other DayZ-ServerMan settings, for example medical loot zones. The restore "
               "overwrites them.")
# window: previousWrites, originalWrites and notOverwritten; {time} is the local time of the record
PREVIOUS_WRITES = ("The restore writes the mission files as they were before the last recorded change: {change}, "
                   "{time}.")
ORIGINAL_WRITES = "The restore writes the original that DayZ-ServerMan saved on {time}."
NOT_OVERWRITTEN = "{path} changed outside the map editor. The restore does not change this file."
# window: changes, the {change} of the previous-state line by the record kind of `replaces`
CHANGES = {"apply": "Apply", "restore_original": "Restore original", "restore_previous": "Restore previous state",
           "medical": "medical loot zones change"}
# window (map_editor_publish_words.js): cleared (6a) and deferred (6b) of Check again
CLEARED = "No unfinished mission map change is left. Preview again before you apply or restore."
DEFERRED = ("DayZ-ServerMan cannot check the unfinished mission map change while another operation runs. Check "
            "again when that operation ends.")
# CLI line after `INVALID_MODE_TEXT` of 8.8, which names window choices (Product Owner 2026-10-17 02:14:51 in
# evidence 10.32, PO-6); the two commands are text to type
INVALID_MODE_COMMANDS = ("map restore original", "map restore previous")
INVALID_MODE_LINE = "Type {original} or {previous}."
# window: the start of the host text of a late file change under a restore kind (Product Owner 07:10:16, section 2)
LATE_CHANGE_START = "Restore stopped because "
