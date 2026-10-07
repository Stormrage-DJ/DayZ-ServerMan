"""Operator wording for operation phases, operation states and the server status (A9, design 11.1).

Python sibling of `activity_wording.py`. The frontend catalogues `operation_labels.js`
and `overview_readiness.js` hold the same texts; `tests/test_cli_wording_parity.py`
keeps both sides equal. A raw kind, phase or state is a key here and is never printed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Per "kind/phase": text and whether the percent moves during the phase (True: determinate).
# "*" rows hold for every kind; "LIFECYCLE" rows hold for the backup inside a stop or restart.
PHASE_TEXTS: dict[str, tuple[str, bool]] = {
    "*/accepted": ("Waiting to start", False),
    "*/queued": ("Waiting to start", False),
    "*/running": ("Starting", False),
    "START_SERVER/preflight": ("Checking and starting the server", False),
    "STOP_SERVER/preflight": ("Checking the server state", False),
    "STOP_SERVER/STOP_SERVER": ("Saving the world and stopping the server", False),
    "RESTART_SERVER/STOP_SERVER": ("Saving the world and stopping the server", False),
    "RESTART_SERVER/preflight": ("Stopping and starting the server", False),
    "RESTART_SERVER/START_SERVER": ("Starting the server", False),
    "LIFECYCLE/BACKUP_DISCOVER": ("Backup: copying files", True),
    "LIFECYCLE/BACKUP_STAGE": ("Backup: checking copied files", True),
    "LIFECYCLE/BACKUP_HASH": ("Backup: writing the file list", True),
    "LIFECYCLE/BACKUP_WRITE_MANIFEST": ("Backup: building and verifying the archive", True),
    "LIFECYCLE/BACKUP_VERIFY": ("Backup: saving the archive", True),
    "LIFECYCLE/BACKUP_PUBLISH": ("Backup: saving the archive", True),
    "CREATE_BACKUP/DISCOVER": ("Copying files", True),
    "CREATE_BACKUP/STAGE": ("Checking copied files", True),
    "CREATE_BACKUP/HASH": ("Writing the file list", True),
    "CREATE_BACKUP/WRITE_MANIFEST": ("Building and verifying the archive", True),
    "CREATE_BACKUP/VERIFY": ("Saving the backup", True),
    "CREATE_BACKUP/PUBLISH": ("Saving the backup", True),
    "RESTORE_BACKUP/VERIFY_SOURCE": ("Saving a recovery copy and preparing files to restore", True),
    "RESTORE_BACKUP/STAGE_TARGETS": ("Saving a recovery copy and preparing files to restore", True),
    "RESTORE_BACKUP/PREPARE_RECOVERY": ("Recording the restore plan", True),
    "RESTORE_BACKUP/WRITE_JOURNAL": ("Replacing server files", False),
    "RESTORE_PROFILE_FROM_BACKUP/VERIFYING_BACKUP": ("Verifying the backup", True),
    "RESTORE_PROFILE_FROM_BACKUP/PREPARING": ("Preparing files", True),
    "RESTORE_PROFILE_FROM_BACKUP/PREPARED": ("Restoring server files", True),
    "RESTORE_PROFILE_FROM_BACKUP/PUBLISHING": ("Restoring server files", True),
    "RESTORE_PROFILE_FROM_BACKUP/PROFILE_PUBLISHING": ("Saving the profile", True),
    "PROVISION_PROFILE/stage_profile": ("Preparing profile files", True),
    "PROVISION_PROFILE/publish_profile_files": ("Creating server files", True),
    "PROVISION_PROFILE/reuse_profile_files": ("Reusing existing server files", True),
    "PROVISION_PROFILE/save_profile": ("Saving the profile", True),
    "PROVISION_PROFILE/verify_launch": ("Checking that the profile can start", True),
    "APPLY_CONFIGURATION/loaded": ("Checking the changes", True),
    "APPLY_CONFIGURATION/validated": ("Writing the file", True),
    "APPLY_CONFIGURATION/verified": ("Finishing", True),
    "APPLY_MISSION_CONFIGURATION/loaded": ("Checking the changes", True),
    "APPLY_MISSION_CONFIGURATION/validated": ("Writing the file", True),
    "APPLY_MISSION_CONFIGURATION/published": ("Verifying the written file", True),
    "APPLY_MISSION_CONFIGURATION/verified": ("Finishing", True),
    "APPLY_MEDICAL_FEATURE/loaded": ("Checking the changes", True),
    "APPLY_MEDICAL_FEATURE/validated": ("Writing the file", True),
    "APPLY_MEDICAL_FEATURE/verified": ("Finishing", True),
    "CONVERT_STARTER_LOADOUT/loaded": ("Checking the changes", True),
    "CONVERT_STARTER_LOADOUT/validated": ("Writing the file", True),
    "CONVERT_STARTER_LOADOUT/published": ("Verifying the written file", True),
    "CONVERT_STARTER_LOADOUT/verified": ("Finishing", True),
    "AUTHENTICATE_STEAMCMD/preflight": ("Checking settings and folders", True),
    "AUTHENTICATE_STEAMCMD/wait_steamcmd": ("Waiting for the server build check to finish", False),
    "AUTHENTICATE_STEAMCMD/interactive_authentication": ("Waiting for sign-in in the SteamCMD window", False),
    "UPDATE_WORKSHOP_ITEMS/preflight": ("Checking settings and folders", True),
    "UPDATE_WORKSHOP_ITEMS/wait_steamcmd": ("Waiting for the server build check to finish", False),
    "UPDATE_WORKSHOP_ITEMS/resolve_items": ("Reading downloaded mods", True),
    "UPDATE_WORKSHOP_ITEMS/check_remote": ("Checking Steam for changes", False),
    "UPDATE_WORKSHOP_ITEMS/download": ("Downloading changed mods", False),
    "UPDATE_WORKSHOP_ITEMS/verify_items": ("Verifying downloaded mods", True),
    "UPDATE_WORKSHOP_ITEMS/verify_set": ("Finishing", True),
    "VERIFY_WORKSHOP_FILES/verify_source": ("Verifying downloaded files", True),
    "VERIFY_WORKSHOP_FILES/verify_target": ("Verifying server folder copies", True),
    "PUBLISH_MODS_AND_KEYS/PUBLICATION_PREFLIGHT": ("Checking the reviewed plan", True),
    "PUBLISH_MODS_AND_KEYS/DISCOVER_ITEM": ("Reading downloaded mods", True),
    "PUBLISH_MODS_AND_KEYS/CACHE_PROOF_RECHECK": ("Checking downloaded mods", True),
    "PUBLISH_MODS_AND_KEYS/STAGE_TARGET": ("Preparing mod folders", True),
    "PUBLISH_MODS_AND_KEYS/CHECK_TARGET": ("Checking the server folder", True),
    "PUBLISH_MODS_AND_KEYS/COPY_FILE": ("Copying mod files", True),
    "PUBLISH_MODS_AND_KEYS/COPY_KEY": ("Copying key files", True),
    "PUBLISH_MODS_AND_KEYS/BEFORE_PUBLICATION": ("Replacing mod folders on the server", False),
    "PUBLISH_MODS_AND_KEYS/AFTER_LIVE_TARGET": ("Replacing mod folders on the server", False),
    "PUBLISH_MODS_AND_KEYS/COMPENSATE_TARGET": ("Undoing changes after a problem", False),
    "PUBLISH_MODS_AND_KEYS/VERIFY_BEFORE_START": ("Checking the server folder before the start", False),
    "PUBLISH_MODS_AND_KEYS/START_SERVER": ("Starting the server", False),
    "APPLY_MODS_AND_RESTART/preflight": ("Checking the reviewed plan", False),
    "IMPORT_LEGACY/DISCOVER": ("Copying legacy files", True),
    "IMPORT_LEGACY/COPY_SOURCE": ("Converting legacy data", True),
    "IMPORT_LEGACY/CONVERT": ("Verifying converted data", True),
    "IMPORT_LEGACY/VERIFY": ("Saving imported data", True),
    "IMPORT_LEGACY/PUBLISH": ("Finishing", True),
    "IMPORT_LEGACY/REPORT": ("Finishing", True),
}
# Wording for a phase that this catalogue does not know yet; such a phase never shows a percent
PHASE_FALLBACK: tuple[str, bool] = ("Working", False)
# Phases that the lane sets when an operation ends; the result sentence stands in their place
TERMINAL_PHASES: tuple[str, ...] = ("complete", "cancelled", "failed", "shutdown")
# Kinds whose backup phases carry the "BACKUP_" prefix
LIFECYCLE_KINDS: tuple[str, ...] = ("STOP_SERVER", "RESTART_SERVER", "APPLY_MODS_AND_RESTART")
# Kinds whose steps are steps of other kinds: their phases are also looked up under those kinds, in this order
BORROWED_PHASES: dict[str, tuple[str, ...]] = {
    "APPLY_MODS_AND_RESTART": ("PUBLISH_MODS_AND_KEYS", "RESTART_SERVER"),
}

# Label per operation state, for lists and for progress lines
STATE_TEXTS: dict[str, str] = {
    "ACCEPTED": "Waiting", "QUEUED": "Waiting", "RUNNING": "In progress", "CANCELLING": "Cancelling",
    "SUCCEEDED": "Done", "FAILED": "Failed", "CANCELLED": "Cancelled", "RECOVERY_REQUIRED": "Needs recovery",
}
# Label of a state that this catalogue does not know
STATE_FALLBACK = "Status unknown"

# Line shown while an operation is cancelling, per kind
CANCELLING_TEXTS: dict[str, str] = {
    "AUTHENTICATE_STEAMCMD": "Cancelling. The sign-in stops at the next safe moment.",
    "UPDATE_WORKSHOP_ITEMS": "Cancelling. The update stops at the next safe moment.",
    "VERIFY_WORKSHOP_FILES": "Cancelling. The verification stops at the next safe moment.",
    "PUBLISH_MODS_AND_KEYS": "Cancelling. Applying the mods stops at the next safe moment.",
    "APPLY_MODS_AND_RESTART": "Cancelling. The restart stops at the next safe moment.",
}
# Cancelling line of a kind without its own
CANCELLING_FALLBACK = "Cancelling. The operation stops at the next safe moment."

# Server status of a manager-owned running server, by readiness: label and explanation
READINESS_TEXTS: dict[str, tuple[str, str]] = {
    "STARTING": ("Starting", "DayZ is running and preparing its Steam endpoint."),
    "READY": ("Ready", "DayZ is running and answering Steam server queries."),
    "UNRESPONSIVE": ("Not responding", "The DayZ process is running, but its Steam endpoint is not responding."),
}
# A manager-owned running server whose readiness is not known
READINESS_FALLBACK: tuple[str, str] = ("Running", "DayZ is running; application readiness is not available.")
# Server status by process state: label and explanation
SERVER_STATE_TEXTS: dict[str, tuple[str, str]] = {
    "STOPPED": ("Stopped", "The configured server process is not running."),
    "RUNNING_EXTERNAL": ("Running outside the manager", "DayZ is running, but this manager does not own it."),
    "STARTING": ("Starting", "The manager is starting DayZ."),
    "STOPPING": ("Stopping", "DayZ is saving and closing."),
    "AMBIGUOUS": ("Several servers found", "More than one matching DayZ process was found."),
    "UNKNOWN": ("State unknown", "The DayZ process state could not be proven."),
}
# A state that the host returns and this catalogue does not know
SERVER_STATE_FALLBACK: tuple[str, str] = ("State unknown", "No authoritative state is available.")


def phase_text(kind: object, phase: object) -> tuple[str, bool]:
    """Return the text of a phase and whether its percent moves, with the lookup order of the frontend.

    The kind's own row comes first, then the rows of the kinds it borrows from, then the
    shared backup rows of a stop or restart, then the generic rows; else the fallback.
    """
    keys = [f"{kind}/{phase}"]
    keys.extend(f"{other}/{phase}" for other in BORROWED_PHASES.get(str(kind), ()))
    if kind in LIFECYCLE_KINDS and str(phase).startswith("BACKUP_"):
        keys.append(f"LIFECYCLE/{phase}")
    keys.append(f"*/{phase}")
    for key in keys:
        if key in PHASE_TEXTS:
            return PHASE_TEXTS[key]
    return PHASE_FALLBACK


def is_working_phase(phase: object) -> bool:
    """Report whether a phase is a working phase: set by a checkpoint, not by the lane itself."""
    return (isinstance(phase, str) and phase != "" and phase not in TERMINAL_PHASES
            and f"*/{phase}" not in PHASE_TEXTS)


def state_text(state: object) -> str:
    """Return the label of an operation state, or the label for an unknown value."""
    return STATE_TEXTS.get(str(state), STATE_FALLBACK)


def cancelling_text(kind: object) -> str:
    """Return the cancelling line of an operation kind, or the line for a kind without its own."""
    return CANCELLING_TEXTS.get(str(kind), CANCELLING_FALLBACK)


def server_status_text(status: Mapping[str, Any] | None) -> tuple[str, str]:
    """Return the label and explanation of a server status, as the Overview shows them."""
    values: Mapping[str, Any] = status if isinstance(status, Mapping) else {}
    if values.get("state") == "RUNNING_MANAGED":
        # Readiness refines only a manager-owned running process
        return READINESS_TEXTS.get(str(values.get("readiness")), READINESS_FALLBACK)
    return SERVER_STATE_TEXTS.get(str(values.get("state")), SERVER_STATE_FALLBACK)
