"""Operator words of the read commands, copied from the window's catalogues (A9, design 11.3).

Each table names its frontend source; `tests/test_cli_read_wording.py` checks that every copied text is
the same string literal there. Texts that name a GUI page pass through the way-out table of `wording.py`.
A raw state, reason or code is a key here and is never printed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# diagnostic_labels.js pathStatusLabels: short label and sentence; "{l}" stands for the role label
PATH_STATUS_TEXTS: dict[str, tuple[str, str]] = {
    "READY": ("Ready", "The {l} is ready."),
    "UNCONFIGURED": ("Not set", "Choose the {l}."),
    "MISSING": ("Not found", "The {l} does not exist. Choose the correct location."),
    "MOVED": ("Moved or missing", "The {l} is no longer where it was. Reconnect the drive or choose the new location."),
    "NOT_FILE": ("Not a file", "This location is not a file. Choose the {l}."),
    "NOT_DIRECTORY": ("Not a folder", "This location is not a folder. Choose a folder."),
    "NOT_WRITABLE": ("Read-only", "DayZ-ServerMan cannot write to the {l}. Choose another folder or change its permissions."),
    "UNSUPPORTED_NETWORK": ("Network location", "Network locations are not supported. Choose a folder on a local drive."),
    "UNSUPPORTED_REPARSE": ("Linked folder", "Links and junctions are not supported. Choose the real folder."),
    "INACCESSIBLE": ("Cannot be opened", "Windows did not allow access to the {l}. Check its permissions."),
}
PATH_STATUS_FALLBACK = ("Needs attention", "Check the {l}.")
# diagnostic_labels.js processDiagnosticLabels: sentence per process diagnostic of the server status
PROCESS_DIAGNOSTIC_TEXTS: dict[str, str] = {
    "RECOVERY_REQUIRED": "Recovery is required before the server can be controlled.",
    "INVENTORY_UNAVAILABLE": "The running programs could not be read.",
    "INVENTORY_INCOMPLETE": "The running programs could not be read completely.",
    "PROCESS_AMBIGUOUS": "More than one matching DayZ process is running.",
}
# diagnostic_labels.js backupRestoreReasons: why a backup cannot be restored
BACKUP_RESTORE_REASONS: dict[str, str] = {
    "LEGACY_PROFILE_SCHEMA": "This backup was made by an older version, before backups held the full profile data. "
                             "Create a new backup to have one that can be restored.",
    "PENDING_RUNTIME_PROFILE_SUPPORT": "This backup has content that this version cannot restore.",
}
# mods_display.js inventoryStatus: label of a mod row state; the fallback is "Unavailable"
MOD_STATE_TEXTS: dict[str, str] = {
    "CURRENT": "Current", "INSTALLED": "Installed", "NOT_DOWNLOADED": "Not downloaded", "LOCAL": "Local",
    "UPDATE_AVAILABLE": "Update available", "PENDING_APPLY": "Downloaded - not applied",
}
MOD_STATE_FALLBACK = "Unavailable"
MOD_NOT_CHECKED = "Could not check"
# mods_display.js: the launch scope column and the empty version
SCOPE_TEXTS: dict[str, str] = {"server": "Server", "client": "Client"}
VERSION_FALLBACK = "Not declared"
NO_MODS = "This server profile has no configured mods."
# update_status.js updateFailureReasons and updateCheckReason
UPDATE_FAILURE_REASONS: dict[str, str] = {
    "NETWORK_UNREACHABLE": "Steam could not be reached",
    "TIMEOUT": "Steam did not answer in time",
    "TLS_FAILURE": "the secure connection to Steam failed",
    "HTTP_STATUS": "Steam refused the request",
    "RESPONSE_TOO_LARGE": "the answer from Steam was too large",
    "RESPONSE_MALFORMED": "the answer from Steam could not be read",
}
# server_build_status.js: failure, unknown-installation and pending texts
BUILD_FAILURE_REASONS: dict[str, str] = {
    "STEAMCMD_UNAVAILABLE": "SteamCMD could not be used. Check its folder in Settings",
    "TIMEOUT": "SteamCMD did not answer in time",
    "STEAMCMD_FAILED": "SteamCMD reported an error",
    "NOT_CONNECTED": "SteamCMD could not connect to Steam",
    "OUTPUT_UNREADABLE": "the answer from Steam could not be read",
    "STEAMCMD_EXIT_UNPROVEN": "SteamCMD did not close after the check. Close SteamCMD, then restart DayZ-ServerMan",
}
BUILD_UNKNOWN_REASONS: dict[str, str] = {
    "NO_DAYZ_FOLDER": "no DayZ server folder is set in Settings",
    "NO_MANIFEST": "Steam has no installation record for this folder",
    "MANIFEST_AMBIGUOUS": "two Steam installation records were found for this folder",
    "MANIFEST_UNREADABLE": "the Steam installation record could not be read",
    "NOT_INSTALLED": "Steam does not report the server as fully installed",
}
BUILD_PENDING_TEXTS: dict[str, str] = {
    "UPDATE_REQUIRED": "Steam has a DayZ server update queued.",
    "UPDATE_RUNNING": "Steam is updating the DayZ server.",
    "FILES_DAMAGED": "Steam reports missing or damaged DayZ server files.",
    "BRANCH_CHANGE": "Steam has a change of the DayZ server branch queued.",
}
# overview_schedule.js scheduleSummaryText and scheduleStatusText
SCHEDULE_ACTIONS: dict[str, str] = {"stop": "Save & Stop", "restart": "Save & Restart"}
NO_SCHEDULE = "No scheduled action"
NO_TIMED_ACTION = "No timed action is enabled."
SCHEDULE_LAST_TEXTS: dict[str, str] = {
    "QUEUED": " Last run was queued.",
    "SKIPPED_NOT_RUNNING": " Last run was skipped because this server was not running under the manager.",
    "QUEUE_FAILED": " Last run could not be queued.",
    "CLAIMED": " Last run is being prepared.",
}
# host_sentences.js hostEnumWords: the Steam sign-in modes
STEAM_MODE_TEXTS: dict[str, str] = {"ACCOUNT": "Steam account", "ANONYMOUS": "Anonymous"}
# overview_players.js: a count that the server does not report
PLAYERS_NOT_KNOWN = "Players not known"


def path_status(role_label: str, diagnostic: Mapping[str, Any] | None) -> tuple[str, str]:
    """Word one path diagnostic as `pathDiagnosticText` does: status label and sentence."""
    if not diagnostic:
        return "Not checked", "Choose a location to check it."
    label, text = PATH_STATUS_TEXTS.get(str(diagnostic.get("status")), PATH_STATUS_FALLBACK)
    return label, text.replace("{l}", role_label)


def mod_state(row: Mapping[str, Any]) -> str:
    """Word the state of one mod row, as the Mods table does."""
    if row.get("state") == "INSTALLED" and row.get("remote_check") not in (None, "OK", "NOT_APPLICABLE"):
        return MOD_NOT_CHECKED
    return MOD_STATE_TEXTS.get(str(row.get("state")), MOD_STATE_FALLBACK)


def update_check_reason(state: object, error_code: object, automatic: bool) -> str:
    """Explain why the mod check has no verified answer (`updateCheckReason`)."""
    if state == "FAILED":
        return UPDATE_FAILURE_REASONS.get(str(error_code), "the check failed")
    if not automatic:
        return "automatic checks are off"
    return "the last check is too old" if state == "STALE" else "not checked yet"


def _count(count: int, singular: str, plural: str) -> str:
    """Phrase a count with its singular or plural noun."""
    return f"{count} {singular if count == 1 else plural}"


def mods_summary(status: Mapping[str, Any] | None, automatic: bool) -> str:
    """Summarize the mod update state in one line, as `updateSummary(null, true)` does."""
    mods = status.get("mods") if isinstance(status, Mapping) else None
    if not isinstance(mods, Mapping):
        return "Update status is unavailable"
    if status.get("checking") and mods.get("check_state") == "NEVER":
        return "Checking for mod updates…"
    parts = []
    if mods.get("update_count", 0) > 0:
        parts.append(f"{_count(mods['update_count'], 'mod update', 'mod updates')} available")
    if mods.get("pending_apply_count", 0) > 0:
        parts.append(f"{mods['pending_apply_count']} downloaded - not applied")
    if mods.get("not_downloaded_count", 0) > 0:
        parts.append(f"{mods['not_downloaded_count']} not downloaded")
    if mods.get("check_state") != "OK":
        parts.append(f"Could not check: {update_check_reason(mods.get('check_state'), mods.get('error_code'), automatic)}")
    return " · ".join(parts) if parts else "No mod updates available"


def build_summary(build: Mapping[str, Any] | None, automatic: bool) -> str:
    """Return the leading sentence of the server build check (`serverBuildSummary`)."""
    if not isinstance(build, Mapping):
        return "Update status is unavailable"
    state, reason = build.get("state"), build.get("reason")
    if state == "CURRENT":
        return "DayZ server is up to date."
    if state == "UPDATE_AVAILABLE":
        newer = _number(build.get("available_build")) > _number(build.get("installed_build"))
        return (f"DayZ server update available: build {build.get('available_build')}." if newer
                else f"Steam lists a different DayZ server build: {build.get('available_build')}.")
    if state == "UPDATE_PENDING":
        if reason == "TARGET_BUILD" and build.get("target_build") is not None:
            return f"Steam has a DayZ server update queued: build {build['target_build']}."
        return BUILD_PENDING_TEXTS.get(str(reason), "Steam has a DayZ server update queued.")
    if state == "UNKNOWN_INSTALLATION":
        why = BUILD_UNKNOWN_REASONS.get(str(reason), "the Steam installation record could not be read")
        return f"The installed DayZ server build is not known: {why}."
    return f"Could not check the DayZ server build: {_build_check_reason(build, automatic)}."


def _build_check_reason(build: Mapping[str, Any], automatic: bool) -> str:
    """Explain why the build check has no current answer (`serverBuildCheckReason`)."""
    reason = build.get("reason")
    if reason in ("NEVER", "STALE"):
        if not automatic:
            return "automatic checks are off"
        return "not checked yet" if reason == "NEVER" else "the last check is too old"
    if reason == "BRANCH_NOT_LISTED":
        return f"Steam shows the branch “{build.get('installed_branch')}” only after a sign-in"
    if reason == "STEAMCMD_NOT_CONFIGURED":
        return "SteamCMD is not set up. Set its folder in Settings"
    return BUILD_FAILURE_REASONS.get(str(build.get("error_code")), "the check failed")


def _number(value: object) -> float:
    """Return a build number for a comparison; anything else counts as 0."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def schedule_texts(schedule: Mapping[str, Any]) -> tuple[str, str]:
    """Return the schedule summary and status line, as the Overview shows them."""
    action = SCHEDULE_ACTIONS.get(str(schedule.get("action")))
    summary = NO_SCHEDULE if action is None else \
        f"{action} daily at {int(schedule.get('hour', 0)):02d}:{int(schedule.get('minute', 0)):02d}"
    next_run = schedule.get("next_run_local")
    status = f"Next: {str(next_run).replace('T', ' ')} local time." if next_run else NO_TIMED_ACTION
    return summary, status + SCHEDULE_LAST_TEXTS.get(str(schedule.get("last_status")), "")


def size_text(size: object) -> str:
    """Render a byte count with binary units (`formatBackupSize`)."""
    try:
        value = float(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "Unknown size"
    if value < 0:
        return "Unknown size"
    units, unit = ("bytes", "KB", "MB", "GB", "TB"), 0
    while value >= 1024 and unit < len(units) - 1:
        value, unit = value / 1024, unit + 1
    return f"{value:.0f} {units[unit]}" if unit == 0 or value >= 10 else f"{value:.1f} {units[unit]}"
