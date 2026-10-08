"""Operator wording of the reviews and confirmation dialogs that the CLI shows before a change (A9, design 11.1).

Python sibling of `activity_wording.py`. Each text is a copy of a literal in the frontend file
named beside it; `tests/test_cli_confirm.py` (lifecycle) and `tests/test_cli_restore_review.py` (restores)
check that every one appears there unchanged.
`tests/test_cli_edit_review.py` checks the profile delete and starter conversion dialogs (phase 6).
`tests/test_cli_migrate.py` checks the legacy import texts (phase 7).
"""

from __future__ import annotations

# `frontend/overview_lifecycle_dialog.js`: title and body of the Overview's start, stop and restart dialogs
LIFECYCLE_DIALOGS: dict[str, tuple[str, str]] = {
    "start": ("Start DayZ server?", "Start the selected profile now."),
    "stop": ("Save and stop DayZ?", "Request a graceful save and wait for DayZ to close."),
    "restart": ("Save and restart DayZ?", "Save and stop the managed process, then start the selected profile."),
}
# The same file: the sentence that a stop or restart with "Backup after stop" adds to the body
BACKUP_AFTER_STOP_SENTENCE = "A verified backup will be created after DayZ stops."

# `frontend/restore.js`: the start of the review title (the backup's date follows), and the confirmation dialog
RESTORE_REVIEW_TITLE = "Review backup from "
RESTORE_DIALOG: tuple[str, str] = (
    "Restore this backup?", "DayZ configuration files will be replaced. Verified recovery copies are created first.")
# The same file: the kind of each restore target, and the page's sentence after a restore that succeeded
RESTORE_TARGET_KINDS: dict[str, str] = {"RUNTIME_PROFILE": "Runtime profile"}
RESTORE_TARGET_FALLBACK = "DayZ configuration"
RESTORE_VERIFIED = "Restore completed and every published target verified."
# `frontend/diagnostic_labels.js` `restoreActionLabels`, with the lookup's fallback
RESTORE_ACTIONS: dict[str, str] = {"REPLACE": "replace", "CREATE": "create"}
RESTORE_ACTION_FALLBACK = "change"

# `frontend/profile_restore.js`: the restore dialog, its review list and its notices
PROFILE_RESTORE_TITLE = "Restore profile"
PROFILE_RESTORE_LABELS: dict[str, str] = {
    "profile": "Profile", "server_config": "Configuration", "runtime_profile": "Runtime", "mission_root": "Mission",
    "storage": "Storage", "ports": "Ports", "storage_policy": "Destination policy",
}
PROFILE_RESTORE_SENTENCE = "Restore creates the profile. Start it separately after checking readiness."
EXECUTABLE_MISSING = "Server executable is missing. Install it before starting."
REPLACE_WORLD_LEAD = "Replace the selected world used by:"
REPLACE_WORLD_TAIL = "A verified recovery copy will be retained."
# The same file: the host status after a profile restore that succeeded
PROFILE_RESTORED_CLEANUP = "Profile restored; recovery cleanup is pending."
PROFILE_RESTORED = "Profile restored."
PROFILE_RESTORED_CHECK = "Profile restored. Check readiness before starting."
WORLD_RECOVERY_COPY = "World recovery copy"
# `frontend/diagnostic_labels.js` `storagePolicyLabels`, with the lookup's fallback
STORAGE_POLICIES: dict[str, str] = {
    "allocate_new": "New isolated mission and storage ID",
    "preserve_original": "Original mission, only if absent",
    "replace_existing": "Replace selected existing world",
}
STORAGE_POLICY_FALLBACK = "Automatic"

# `frontend/backups.js`: title and body of the "Create backup" dialog; the profile's display name follows "Server: "
BACKUP_CREATE_TITLE = "Create backup?"
BACKUP_CREATE_BODY = ("Its configuration, runtime, complete mission and world persistence will be copied and verified. "
                      "Mod directories are not included.")

# `frontend/mod-publication.js` `modReviewTexts`: the apply review after a mod update (8.2). The window's
# advice sentences that name its buttons ("useRestart", the refused advice) have CLI forms in `cli/mods_wording.py`
MOD_REVIEW_TEXTS: dict[str, str] = {
    "noStart": "No server start was requested.",
    "start": "After the mods are verified in the server folder, DayZ-ServerMan will start this server.",
    "inUse": " A mod folder that is in use cannot be replaced; the apply then stops and puts the folders back.",
    "whenStopped": " To be safe, apply the mods when the server is stopped.",
    "refused": " Mods cannot be applied to the server folder now.",
    "restart": ("DayZ-ServerMan will save and stop the server, apply the mods, and start the server again. "
                "The server is offline during these steps."),
    "restartBackup": ("DayZ-ServerMan will save and stop the server, create a verified backup, apply the mods, "
                      "and start the server again. The server is offline during these steps."),
    "changed": "The server state changed. The mods are downloaded; nothing was applied.",
    "short": "No mod folder is copied. Missing key files are added.",
    "nothing": "Nothing is written to the server folder: every mod folder and key file is already in place.",
    "title": "Apply downloaded mods and keys?",
    "lead": "Review the server folders that will be updated. Changes use verified rollback protection.",
    "notAppliedTitle": "Mods were not applied",
    "notAppliedLead": "The reviewed plan was not applied:",
}
# The same file: the server state that the review names (`modReviewServerStates`), with its fallback
MOD_REVIEW_SERVER_STATES: dict[str, str] = {
    "RUNNING_MANAGED": "The server is running.",
    "RUNNING_EXTERNAL": "The server is running outside DayZ-ServerMan.",
    "STARTING": "The server is starting.",
    "STOPPING": "The server is stopping.",
}
MOD_REVIEW_STATE_UNCONFIRMED = "The server state could not be confirmed."
# The same file `publicationReviewLines`: the fixed parts of the key line (the counts lead them)
MOD_KEYS_ADDED = "verified key file(s) are added"
MOD_KEYS_IN_PLACE = "verified key file(s), all already in place"

# `frontend/profile_delete.js`: the "Delete profile?" dialog; the profile's display name follows the lead
PROFILE_DELETE_TITLE = "Delete profile?"
PROFILE_DELETE_LEAD = "Permanently delete "
PROFILE_DELETE_BODY = (", including its generated configuration, runtime files, exclusive world storage, schedule, "
                       "and saved preferences. Existing backup archives will remain in the configured backup "
                       "destination.")
# `frontend/tweaks_dialog.js`: the "Convert legacy starter loadout?" dialog; the line range and the item count
# of the recognized block go between the parts of its first sentence
STARTER_CONVERSION_TITLE = "Convert legacy starter loadout?"
STARTER_CONVERSION_RANGE = ("The recognized block spans lines ", "-", " and contains ", " supported item(s).")
STARTER_CONVERSION_BODY = ("Conversion adds DayZ-ServerMan ownership comments around that block. It does not "
                           "replace its statements or reorder its items.")
# `frontend/migration.js`: the legacy import's item titles and summaries, its source note and its dialog;
# `{count}` and `{size}` stand where the window inserts the numbers
MIGRATION_SETTINGS_TITLE = "DayZ installation settings"
MIGRATION_SETTINGS_UNCHANGED = "Current installation settings already have values."
MIGRATION_BLOCKED_TITLE = "Blocked legacy profile"
MIGRATION_BLOCKED_SUMMARY = "This legacy profile cannot be imported without review."
MIGRATION_BACKUP_TITLE = "Legacy backup external references"
MIGRATION_BACKUP_SUMMARY = ("{count} archives ({size} bytes) will be indexed by safe label, digest, and opaque ID. "
                            "The archives stay where they are and are only listed. External reference only — "
                            "source remains in legacy folder. Not restorable by DayZ-ServerMan. No archive bytes "
                            "will be copied.")
MIGRATION_NEEDS_REVIEW = "Needs review: "
MIGRATION_SOURCE_NOTE = "The source remains unchanged. Legacy UI state and authentication settings are ignored."
MIGRATION_DIALOG = "Import selected legacy data?"
MIGRATION_DIALOG_BODY = ("{count} selected item(s) will be converted or indexed. Legacy backup archives remain "
                         "external and read-only; they are not copied or restorable by DayZ-ServerMan.")
