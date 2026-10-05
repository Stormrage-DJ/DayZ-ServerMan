"""Operator wording for the "Manager activity" log view and for host sentences that name a setting.

The frontend catalogue holds the same texts for kinds, error codes, block reasons, and path roles.
Tests keep the two catalogues equal. A raw kind, state, code, or role is a key here and is never printed.
"""

from __future__ import annotations

import re

# Text for an internal fault and for every message that would show an identifier
INTERNAL_TEXT = "Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics."
# Text for a rejected request whose host message cannot be shown
REQUEST_TEXT = ("The request was not accepted. Check the values you entered, then try again. "
                "Details are in Logs, Manager diagnostics.")
# Text for a blocked state that an earlier operation left behind
RECOVERY_TEXT = ("An earlier operation did not finish cleanly. Changes are blocked until it is resolved. "
                 "Details are in Logs, Manager diagnostics.")
# The three causes of a refused submission, as the operator reads them
QUEUE_FULL_TEXT = "Too many operations are waiting. Wait for one to finish and try again."
CLOSING_TEXT = "DayZ-ServerMan is closing and starts no new operation."
BLOCKED_TEXT = "Changes are blocked until recovery is resolved."
# Reason shown when a block reason is neither known nor a plain sentence
BLOCK_FALLBACK = "An earlier operation did not finish cleanly."
# The way out of a block that has no catalogue sentence: a restart checks again (QF-045)
BLOCK_RESTART_ACTION = "Restart DayZ-ServerMan to check again."
# The way out of such a block when a restore owns it: the Backups page finishes the restore
BLOCK_RESTORE_ACTION = "Open Backups to finish the restore."
# Operation kind of a restore apply; the blocks it owns are lifted on the Backups page
BLOCK_RESTORE_OWNER = "RESTORE_BACKUP"

# Per operation kind: name while active, success, failure, and cancellation sentence
KIND_TEXTS: dict[str, tuple[str, str, str, str]] = {
    "START_SERVER": ("Starting server", "Server started.", "The server could not be started.", "Server start cancelled."),
    "STOP_SERVER": ("Stopping server", "Server stopped.", "The server could not be stopped.", "Backup cancelled. The server stays stopped."),
    "RESTART_SERVER": ("Restarting server", "Server restarted.", "The server could not be restarted.", "Restart cancelled during the backup. The server stays stopped."),
    "CREATE_BACKUP": ("Creating backup", "Backup created.", "The backup could not be created.", "Backup cancelled."),
    "RESTORE_BACKUP": ("Restoring backup", "Backup restored.", "The backup could not be restored.", "Restore cancelled. Server files were not changed."),
    "RESTORE_PROFILE_FROM_BACKUP": ("Restoring profile from backup", "Profile restored.", "The profile could not be restored.", "Profile restore cancelled."),
    "SAVE_PROFILE": ("Saving profile", "Profile saved.", "The profile could not be saved.", "Profile save cancelled."),
    "DELETE_PROFILE": ("Deleting profile", "Profile deleted.", "The profile could not be deleted.", "Profile deletion cancelled."),
    "PROVISION_PROFILE": ("Creating profile", "Profile created.", "The profile could not be created.", "Profile creation cancelled."),
    "APPLY_CONFIGURATION": ("Applying configuration changes", "Configuration changes applied.", "The configuration changes could not be applied.", "Configuration changes cancelled. Nothing was changed."),
    "APPLY_MISSION_CONFIGURATION": ("Applying tweaks", "Tweaks applied.", "The tweaks could not be applied.", "Tweaks cancelled. Nothing was changed."),
    "APPLY_MEDICAL_FEATURE": ("Applying medical loot setting", "Medical loot setting applied.", "The medical loot setting could not be applied.", "Medical loot change cancelled. Nothing was changed."),
    "CONVERT_STARTER_LOADOUT": ("Converting starter loadout", "Starter loadout converted.", "The starter loadout could not be converted.", "Starter loadout conversion cancelled. Nothing was changed."),
    "SAVE_SETTINGS": ("Saving application locations", "Application locations saved.", "The application locations could not be saved.", "Saving application locations cancelled."),
    "SAVE_STEAM_SETTINGS": ("Saving Steam sign-in settings", "Steam sign-in settings saved.", "The Steam sign-in settings could not be saved.", "Saving Steam sign-in settings cancelled."),
    "AUTHENTICATE_STEAMCMD": ("Signing in to Steam", "Steam sign-in completed.", "Steam sign-in did not complete.", "Steam sign-in cancelled."),
    "UPDATE_WORKSHOP_ITEMS": ("Updating mods", "Mods are downloaded and checked.", "The mods could not be updated.", "Mod update cancelled."),
    "PUBLISH_MODS_AND_KEYS": ("Applying mods to the server", "Mods and keys applied.", "The mods could not be applied.", "Applying mods cancelled."),
    "APPLY_MODS_AND_RESTART": ("Applying mods and restarting the server", "Mods applied and server restarted.", "Applying mods and restarting the server did not finish.", "Applying mods and restarting cancelled."),
    "VERIFY_WORKSHOP_FILES": ("Verifying mod files", "Mod files verified.", "The mod files could not be verified.", "Verification cancelled. Finished mods stay recorded."),
    "IMPORT_LEGACY": ("Importing legacy data", "Legacy data imported.", "The legacy data could not be imported.", "Legacy import cancelled."),
    "REVALIDATE_LEGACY_BACKUPS": ("Checking legacy backups", "Legacy backups checked.", "The legacy backups could not be checked.", "Legacy backup check cancelled."),
}
# Wording for a kind that this catalogue does not know yet
KIND_FALLBACK = ("Working", "Operation finished.", "The operation did not finish.", "Operation cancelled.")
# Kinds whose outcome depends on a result that the log record does not hold: a neutral end sentence
NEUTRAL_SUCCESS = {
    "UPDATE_WORKSHOP_ITEMS": "Mod update finished. The result is on the Mods page.",
    "VERIFY_WORKSHOP_FILES": "Mod file verification finished. The result is on the Mods page.",
    "APPLY_MODS_AND_RESTART": "Applying mods and restarting finished. The result is on the Mods page.",
}
# Sentences of "apply mods and restart" for a failed or cancelled end, chosen by the last working phase
RESTART_APPLY_TEXTS: dict[str, str] = {
    "cancelledRunning": "Cancelled. The server keeps running; nothing was applied.",
    "cancelledStopped": "Cancelled. The server stays stopped; the mods were not applied.",
    "preflight": "The reviewed plan is out of date, or the server state changed. The server keeps running; nothing was applied. Update again.",
    "stop": "The server could not be stopped. Nothing was applied.",
    "stopNotRunning": "The server was already stopped, so it was not restarted. Nothing was applied. To apply the mods and start the server, use Update & start.",
    "backup": "Server stopped, but the backup failed. The mods were not applied and the server was not started again.",
    "check": "Mods applied, but the server folder changed before the start. The server stays stopped.",
    "apply": "Server stopped, but the mods could not be applied. The server folder is as it was. The server was not started again.",
    "applyBlocked": "Server stopped, but the mods could not be applied.",
    "applyRefused": "The server was stopped, but its state changed before the mods could be applied. Nothing was applied; the server folder is as it was.",
    "startCheck": "Mods applied, but the server folder changed before the start. The server was not started.",
    "start": "Mods applied, but the server start did not succeed or could not be confirmed. Check the server state on Overview.",
}
# Error codes of a write guard that refused the apply because the server was not stopped
GUARD_CODES = frozenset(("CONTROL_CONFLICT", "EXTERNAL_PROCESS", "PROCESS_STATE_UNKNOWN"))

# Text that replaces the host message, per error code
ERROR_TEXTS: dict[str, str] = {
    "INTERNAL_FAILURE": INTERNAL_TEXT,
    "CONTRACT_VERSION_UNSUPPORTED": INTERNAL_TEXT,
    "EVENT_CURSOR_EXPIRED": INTERNAL_TEXT,
    "REVISION_CONFLICT": "The profile or the settings changed in the meantime. Open another page, return to this one, and try again.",
    "CONTROL_CONFLICT": "Another DayZ-ServerMan is using this DayZ installation. Close it and try again.",
    "PROCESS_STATE_UNKNOWN": "DayZ-ServerMan could not read the running programs, so the server state is not known. Try again.",
    "PROCESS_OWNERSHIP_UNPROVEN": "DayZ-ServerMan cannot confirm that it started this DayZ server, so it will not control it.",
    "STOP_TIMEOUT": "DayZ did not close in time. Check the server window, then try again.",
    "RECOVERY_REQUIRED": RECOVERY_TEXT,
    "UPDATE_RESULT_UNKNOWN": "SteamCMD did not close cleanly, so the update cannot be confirmed. Changes are blocked.",
    "AUTHENTICATION_REQUIRED": "Choose Steam account sign-in and enter an account name first.",
    "AUTHENTICATION_FAILED": "SteamCMD closed before the sign-in was confirmed.",
    "STEAMCMD_UNAVAILABLE": "The SteamCMD or Workshop folder is missing or changed. Check the folders in Settings and try again.",
    "STEAMCMD_PATH_CHANGED": "The SteamCMD or Workshop folder is missing or changed. Check the folders in Settings and try again.",
    "STEAMCMD_BUSY": "SteamCMD is still busy with the server build check. Try again in a minute.",
    "STEAMCMD_EXIT_UNPROVEN": "SteamCMD did not close after an earlier run. Close SteamCMD, then restart DayZ-ServerMan.",
    "ENTITLEMENT_DENIED": "This Steam account may not download the item.",
    "CONNECTION_FAILED": "Steam could not be reached.",
    "WORKSHOP_CONTENT_FAILED": "The downloaded mod files could not be verified. Run the update again.",
    "CACHE_VERIFICATION_FAILED": "The downloaded mod files could not be verified. Run the update again.",
    "WORKSHOP_MANIFEST_INVALID": "The downloaded mod files could not be verified. Run the update again.",
    "UPDATE_CANCELLED": "Mod update cancelled.",
    "PUBLICATION_REQUIRED": "Review and apply the mods before the server starts.",
    "PUBLICATION_PREVIEW_STALE": "The mods or the server folder changed after the review. Review and apply again.",
    "PUBLICATION_FAILED": "The mods could not be copied to the server folder.",
    "PUBLICATION_VERIFICATION_FAILED": "The applied mods could not be verified.",
    "BACKUP_UNAVAILABLE": "Backups are not available in this installation.",
    "RUNTIME_PROFILE_UNRESOLVED": "Set a runtime profile directory in Profiles first.",
    "PENDING_RUNTIME_PROFILE_SUPPORT": "This backup has content that this version cannot restore.",
    "UNSUPPORTED_SNAPSHOT_CONTENT": "This backup has content that this version cannot restore.",
    "PROFILE_CONTEXT_MISMATCH": "This backup belongs to a different profile.",
    "SOURCE_CHANGED": "The legacy folder changed after the preview. Preview it again.",
    "MIGRATION_CONFLICT": "The legacy folder changed after the preview. Preview it again.",
    "GAMEPLAY_NOT_ENABLED": "Turn on “Use gameplay configuration” in Configuration first.",
    "OPERATION_NOT_CANCELLABLE": "This operation can no longer be cancelled.",
}
# Known host sentences of a rejected request, by a fragment of the host message
REQUEST_TEXTS: tuple[tuple[str, str], ...] = (
    ("must contain only letters, digits, or underscore", "The Steam account name may contain only letters, digits and underscores."),
    ("is required in ACCOUNT mode", "Enter a Steam account name for account sign-in."),
    ("must be empty in ANONYMOUS mode", "Leave the account name empty for anonymous sign-in."),
    ("authentication mode", "Choose a sign-in mode for the Steam account."),
    ("must be ACCOUNT or ANONYMOUS", "Choose a sign-in mode for the Steam account."),
)
# Server state names that a host message may hold, with their wording inside a sentence
SERVER_STATES: dict[str, str] = {
    "RUNNING_EXTERNAL": "running outside DayZ-ServerMan", "RUNNING_MANAGED": "running",
    "STOPPED": "stopped", "STARTING": "starting", "STOPPING": "stopping",
    "AMBIGUOUS": "in an unknown state", "UNKNOWN": "in an unknown state",
}
# Reason sentence of an unfinished legacy import; the host words this block in two ways
_IMPORT_BLOCK = ("A legacy import was interrupted and could not be undone safely. "
                 "Restart DayZ-ServerMan; it checks the unfinished import again when it starts.")
# Known reasons of a recovery block, by a fragment of the host reason; the first match wins
BLOCK_REASONS: tuple[tuple[str, str], ...] = (
    ("Direct profile restore", "A profile restore from a backup archive did not finish. Stop the DayZ server, then restart DayZ-ServerMan; it checks the unfinished restore when it starts."),
    ("restore recovery", "A backup restore did not finish cleanly. Open Backups; DayZ-ServerMan checks the unfinished restore again there."),
    ("mod publication recovery", "Applying mods to the server folder was interrupted and could not be undone safely. Restart DayZ-ServerMan; it checks the server folder again when it starts."),
    ("unresolved mod publication", "Applying mods to the server folder was interrupted, and it cannot be checked because no DayZ server folder is set."),
    ("interrupted mod publication", "Applying mods to the server folder was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then restart DayZ-ServerMan."),
    ("interrupted backup restore", "A backup restore was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then open Backups again or restart DayZ-ServerMan."),
    ("interrupted direct profile restore", "A profile restore from a backup archive was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then restart DayZ-ServerMan."),
    ("interrupted profile creation", "Creating a profile was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then restart DayZ-ServerMan."),
    ("interrupted SteamCMD update", "A mod update was interrupted, so its result is not known. Restart DayZ-ServerMan, then update the mods again."),
    ("process-tree exit", "SteamCMD did not close cleanly, so the mod update cannot be confirmed. Close SteamCMD, restart DayZ-ServerMan, then update the mods again."),
    ("Profile provisioning recovery", "Creating a profile was interrupted and could not be undone safely. Restart DayZ-ServerMan; it checks the unfinished profile again when it starts."),
    ("migration recovery", _IMPORT_BLOCK),
    ("Migration publication", _IMPORT_BLOCK),
)
# Label of each path role and settings field inside a sentence
ROLE_LABELS: dict[str, str] = {
    "dayz_root": "DayZ server folder", "dayz_executable": "DayZ server program",
    "steamcmd_root": "SteamCMD folder", "steamcmd_executable": "SteamCMD program",
    "workshop_content_root": "Workshop download folder",
    "backup_root": "backup folder", "custom_backup_root": "backup folder",
}

# An upper-case identifier, a snake_case word, a camelCase word, or a bracketed list of names
_IDENTIFIER = re.compile(r"[A-Z]{2,}_[A-Z_]+|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|\b[a-z]+[A-Z]\w*|[\[\]{}]")
_STATE_NAME = re.compile(r"\b(" + "|".join(SERVER_STATES) + r")\b")
_ROLE_NAME = re.compile(r"\b(" + "|".join(sorted(ROLE_LABELS, key=len, reverse=True)) + r")\b")


def leaks_identifier(text: str) -> bool:
    """Report whether a text still holds something that is not operator wording."""
    return _IDENTIFIER.search(text) is not None


def plain_sentence(message: object) -> str | None:
    """Return a host message as one operator sentence, or None when it cannot be shown.

    Path roles become their labels. A bare code, an empty text, and a text that
    still holds an identifier are refused.
    """
    text = _ROLE_NAME.sub(lambda match: f"the {ROLE_LABELS[match.group(1)]}", str(message or "")).strip()
    text = text.replace("DayZ root", "DayZ server folder")
    if " " not in text or leaks_identifier(text):
        return None
    # Start with a capital letter and end with a full stop
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else f"{text}."


def restart_apply_text(
    state: object, phase: object, code: object = None, message: object = None,
) -> str | None:
    """Return the sentence of a failed or cancelled "apply mods and restart", or None for the generic one.

    `code` and `message` are the host error; a server state that the message names
    tells a refused stop of a stopped server and a refused apply from a real failure.
    """
    name = phase if isinstance(phase, str) else ""
    if state == "CANCELLED":
        # Before the stop the server still runs; every later safe point leaves it stopped
        return RESTART_APPLY_TEXTS["cancelledRunning" if name in ("", "preflight") else "cancelledStopped"]
    if not name:
        return None
    states = _STATE_NAME.findall(str(message or ""))
    named = states[-1] if states else None
    if name == "STOP_SERVER":
        # A stop that was refused because the server was already stopped did not fail to stop it
        key = "stopNotRunning" if named == "STOPPED" else "stop"
    elif name in ("preflight", "VERIFY_BEFORE_START", "START_SERVER"):
        # A failure of the start handoff proves neither a changed folder nor a stopped server
        key = {"preflight": "preflight", "VERIFY_BEFORE_START": "check", "START_SERVER": "start"}[name]
    elif name.startswith("BACKUP_"):
        key = "backup"
    elif state == "RECOVERY_REQUIRED":
        key = "applyBlocked"
    else:
        # A write guard that found the server no longer stopped applied nothing
        key = "applyRefused" if named not in (None, "STOPPED") and code in GUARD_CODES else "apply"
    return RESTART_APPLY_TEXTS[key]


def block_reason_text(reason: object, owner: object = None) -> str:
    """Word why changes are blocked: the known sentence, else the host sentence or the fallback with its way out."""
    text = str(reason or "")
    for fragment, sentence in BLOCK_REASONS:
        if fragment in text:
            return sentence
    action = BLOCK_RESTORE_ACTION if owner == BLOCK_RESTORE_OWNER else BLOCK_RESTART_ACTION
    return f"{plain_sentence(text) or BLOCK_FALLBACK} {action}"


def conflict_text(message: object, reason: object = None, owner: object = None) -> str:
    """Word a refused submission by its cause; without a cause the host message decides.

    The owner of a refusing block chooses the way out, as on the page (QF-047).
    """
    text = str(message or "")
    if reason == "QUEUE_FULL" or (reason is None and "queue is full" in text):
        return QUEUE_FULL_TEXT
    if reason == "SHUTTING_DOWN" or (reason is None and "lane is draining" in text):
        return CLOSING_TEXT
    return f"{BLOCKED_TEXT} {block_reason_text(text, owner)}"


def error_text(code: object, message: object, owner: object = None) -> str:
    """Turn a host error code and message into operator text; a code is never printed."""
    text = str(message or "")
    # A message that names a server state becomes one sentence about that state
    states = _STATE_NAME.findall(text)
    if states or code == "EXTERNAL_PROCESS":
        return f"This cannot be done while the server is {SERVER_STATES[states[-1] if states else 'RUNNING_EXTERNAL']}."
    if code == "MUTATION_CONFLICT":
        return conflict_text(text, owner=owner)
    if code in ERROR_TEXTS:
        return ERROR_TEXTS[str(code)]
    # A rejected request with a known host sentence has its own wording
    for fragment, sentence in REQUEST_TEXTS if code == "INVALID_REQUEST" else ():
        if fragment in text:
            return sentence
    # Any other host sentence is shown when it holds no identifier
    return plain_sentence(text) or (REQUEST_TEXT if code == "INVALID_REQUEST" else INTERNAL_TEXT)
