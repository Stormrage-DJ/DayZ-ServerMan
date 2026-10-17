"""Modes, refusal reasons and texts of Restore original and Restore previous state (leaf 8.8).

The texts are the Product Owner texts of 2026-10-15 07:10:16 in evidence 8.8,
word for word. The window (8.10) and the CLI (10.5) show them unchanged.
"""

from __future__ import annotations

# The two restore modes, their D4 journal and record kinds and their operation-lane kinds
ORIGINAL, PREVIOUS = "original", "previous"
RECORD_KINDS = {ORIGINAL: "restore_original", PREVIOUS: "restore_previous"}
LANE_KINDS = {ORIGINAL: "RESTORE_MISSION_MAP_ORIGINAL", PREVIOUS: "RESTORE_MISSION_MAP_PREVIOUS"}

# Closed refusal reasons of a restore review; the last one is Architect A88-F2 (2026-10-15 07:07:28)
NO_ORIGINAL, NO_PREVIOUS_STATE, INVALID_MODE = "no_original", "no_previous_state", "invalid_mode"
TARGET_UNPROJECTABLE = "target_unprojectable"

# Texts of the refusals
NO_ORIGINAL_TEXT = ("Restore original is not available for this mission. DayZ-ServerMan has no saved original of this "
                    "mission folder. It saves the original before the first map editor Apply on the folder.")
NO_PREVIOUS_STATE_TEXT = ("Restore previous state is not available for this mission. DayZ-ServerMan has no saved "
                          "previous state of this mission for this profile. It saves one when this profile applies a "
                          "map plan to this mission or uses Restore original on it.")
INVALID_MODE_TEXT = "This restore choice is not valid. Choose Restore original or Restore previous state."
TARGET_UNPROJECTABLE_TEXT = ("This restore is not available for this mission. In the version of {path} that it would "
                             "write, DayZ-ServerMan cannot read the values that the map editor sets, so it does not "
                             "write that version back.")
# Text of a stale restore review
RESTORE_STALE_TEXT = ("The profile, the settings, the mission files or the map editor records changed after the "
                      "preview. Nothing was restored. Preview the restore again and check it before you restore.")
# Text of a file that changed between the review and its write; the commit step names Apply
TARGET_CHANGED_TEXT = ("Restore stopped because {path} changed after the preview. Nothing was restored, and that file "
                       "keeps its current content. Preview the restore again and check it before you restore.")
# Preview text on a shared target: the restore writes the one original of the folder (A4, D5)
SHARED_ORIGINAL_TEXT = ("Other profiles use this mission folder or have map editor records for it. The folder has one "
                        "original for all profiles, and Restore original writes that original. The restore also "
                        "changes the mission files for every profile that uses this folder.")
# Restore original preview sentence, once after SHARED_ORIGINAL_TEXT. It shows when the profile keeps an inactive
# restored copy of the original of this folder (Product Owner 2026-10-17 07:28:16 in evidence 10.37).
RESTORED_COPY_TEXT = ("This profile also has a different original from a restored backup. Restore original does not "
                      "use that original.")
# Preview lines of a file that changed outside the map editor and that the restore overwrites (R17)
BACKUP_TEXT = ("{path} changed outside the map editor. DayZ-ServerMan saves a copy of the current file before the "
               "restore changes it.")
DELETED_TEXT = "{path} was deleted outside the map editor. The restore writes this file again."
# Restore original preview sentence when the working-baseline files differ from the active original (ruling 1a)
KEPT_CHANGES_TEXT = ("This profile kept changes made outside the map editor with Keep the change. Restore original "
                     "does not remove them from the map editor records. The next Apply writes them again with the map "
                     "plan, and its preview shows them.")
