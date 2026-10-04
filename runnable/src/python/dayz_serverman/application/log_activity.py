"""Operator-facing "Manager activity": one plain line per decision in the manager log.

The raw records stay available as "Manager diagnostics". A raw event name, kind,
state, code, method, or field value is never printed here.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from . import activity_wording as wording


# Events that are routine transport or lifecycle noise and never reach the activity view
HIDDEN_EVENTS = frozenset((
    "bridge.request", "bridge.success", "operation.progress", "shutdown.requested", "shutdown.closed",
    "operation_lane.draining", "schedule.started", "schedule.stopped", "update_check.request",
    "update_check.response", "update_check.scheduler_started", "update_check.scheduler_stopped",
    "mod_publication.prestart_check",
))
# Sentence shown for an event whose details stay in the diagnostics
DETAIL_HINT = "Details are in Manager diagnostics."
# Fixed sentence per event that needs no field
EVENT_TEXTS: dict[str, str] = {
    "schedule.skipped": "The scheduled action was skipped because its server was not running under the manager.",
    "schedule.queue_failed": f"The scheduled action could not be started. {DETAIL_HINT}",
    "schedule.storage_unavailable": f"The daily schedule could not be read. {DETAIL_HINT}",
    "update_check.ids_capped": "The update check left out some mods because the profiles hold too many.",
    "update_check.run_failed": f"The update check failed. {DETAIL_HINT}",
    "update_check.scheduler_failed": f"The automatic update check failed. {DETAIL_HINT}",
    "update_check.cache_write_failed": f"The result of the update check could not be saved. {DETAIL_HINT}",
    "content_proofs.write_failed": f"A record of verified mod files could not be saved. {DETAIL_HINT}",
}
# Wording of a scheduled action inside a sentence
SCHEDULE_ACTIONS: dict[str, str] = {"stop": "save and stop", "restart": "save and restart"}
# What each bridge request does, as the subject of "… failed: <reason>"
METHOD_TEXTS: dict[str, str] = {
    "apply_configuration": "Applying configuration changes", "preview_configuration": "Reviewing configuration changes",
    "load_configuration": "Loading the configuration", "apply_mission_configuration": "Applying tweaks",
    "preview_mission_configuration": "Reviewing tweaks", "load_mission_configuration": "Loading tweaks",
    "apply_medical_feature": "Applying the medical loot setting", "preview_medical_feature": "Reviewing the medical loot setting",
    "load_medical_features": "Loading the medical loot settings", "convert_starter_loadout": "Converting the starter loadout",
    "preview_starter_loadout_conversion": "Reviewing the starter loadout conversion",
    "apply_legacy_import": "Importing legacy data", "preview_legacy_import": "Previewing the legacy import",
    "select_legacy_root": "Choosing the legacy folder", "list_legacy_backup_references": "Listing legacy backups",
    "revalidate_legacy_backup_references": "Checking legacy backups",
    "create_backup": "Creating a backup", "list_backups": "Listing backups", "list_backup_catalog": "Listing backups",
    "preview_restore": "Reviewing a restore", "apply_restore": "Restoring a backup",
    "inspect_restore_recovery": "Checking an unfinished restore", "select_backup_archive": "Choosing a backup archive",
    "inspect_backup_archive": "Checking a backup archive", "preview_profile_restore": "Reviewing a profile restore",
    "restore_profile_from_backup": "Restoring a profile from a backup",
    "authenticate_steamcmd": "Signing in to Steam", "update_workshop_items": "Updating mods",
    "verify_mod_files": "Verifying mod files", "list_mod_inventory": "Listing mods",
    "preview_mod_publication": "Reviewing the mods to apply", "publish_mods_and_keys": "Applying mods to the server",
    "apply_mods_and_restart": "Applying mods and restarting the server",
    "get_update_status": "Reading the update status", "request_update_check": "Checking for updates",
    "save_automatic_update_checks": "Saving the automatic update check setting",
    "list_profiles": "Listing profiles", "read_profile": "Reading a profile", "save_profile": "Saving a profile",
    "delete_profile": "Deleting a profile", "provision_profile": "Creating a profile",
    "preview_profile_command": "Previewing the launch command", "list_profile_missions": "Listing missions",
    "save_selected_profile": "Saving the selected profile", "get_ui_preferences": "Reading the preferences",
    "save_backup_after_stop": "Saving the backup-after-stop setting",
    "get_lifecycle_schedule": "Reading the daily schedule", "save_lifecycle_schedule": "Saving the daily schedule",
    "start_server": "Starting the server", "stop_server": "Stopping the server", "restart_server": "Restarting the server",
    "get_server_status": "Reading the server state", "get_application_snapshot": "Loading the workspace",
    "get_operation": "Reading an operation", "read_operation_events": "Reading operation events",
    "request_operation_cancellation": "Cancelling an operation", "request_shutdown": "Closing DayZ-ServerMan",
    "save_settings": "Saving application locations", "save_steam_settings": "Saving Steam sign-in settings",
    "select_settings_path": "Choosing a location", "validate_settings_path_selection": "Checking a location",
    "read_log": "Reading a log",
}


def manager_activity(lines: list[str]) -> list[str]:
    """Turn structured diagnostics into concise operator-facing activity."""
    activity: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        rendered = format_manager_record(record)
        if rendered is not None:
            activity.append(rendered)
    return activity


def format_manager_record(record: Mapping[str, Any]) -> str | None:
    """Format one relevant manager record in operator wording, or hide routine diagnostics."""
    event = record.get("event")
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    if not isinstance(event, str) or event in HIDDEN_EVENTS:
        return None
    message = _event_sentence(event, fields, str(record.get("level", "INFO")))
    if message is None:
        return None
    timestamp = str(record.get("occurred_at", "")).replace("T", " ")[:19]
    return f"{timestamp}  {_level_label(str(record.get('level', 'INFO'))):<7}  {message}".strip()


def _level_label(level: str) -> str:
    """Return the level column of a line as a plain word."""
    return {"ERROR": "Error", "WARNING": "Warning", "CRITICAL": "Error"}.get(level, "Info")


def _event_sentence(event: str, fields: Mapping[str, Any], level: str) -> str | None:
    """Word one event; an event without wording is shown only when it reports a problem."""
    if event == "operation.state":
        return _operation_sentence(fields)
    if event == "bridge.failure":
        subject = METHOD_TEXTS.get(str(fields.get("method")), "A request")
        text = wording.error_text(fields.get("error_code"), fields.get("message"), fields.get("owner"))
        return f"{subject} failed: {text}"
    if event == "operation_lane.recovery_block":
        return f"Changes are now blocked. {wording.block_reason_text(fields.get('reason'), fields.get('owner'))}"
    if event in ("schedule.saved", "schedule.queued"):
        return _schedule_sentence(event, fields)
    if event == "update_check.completed":
        return ("Update check finished." if fields.get("outcome") == "OK"
                else f"The update check did not finish. {DETAIL_HINT}")
    if event in EVENT_TEXTS:
        return EVENT_TEXTS[event]
    # An event that this view does not know is named only when it is a warning or an error
    return f"A problem was recorded. {DETAIL_HINT}" if level in ("WARNING", "ERROR", "CRITICAL") else None


def _operation_sentence(fields: Mapping[str, Any]) -> str | None:
    """Word the end of an operation with the result sentences of the frontend catalogue."""
    state = fields.get("state")
    kind = str(fields.get("kind"))
    _name, success, failure, cancelled = wording.KIND_TEXTS.get(kind, wording.KIND_FALLBACK)
    if state == "SUCCEEDED":
        return wording.NEUTRAL_SUCCESS.get(kind, success)
    # "Apply mods and restart" names what the phase that worked last left behind
    code, message = fields.get("error_code"), fields.get("error_message")
    phase = fields.get("last_working_phase")
    specific = (wording.restart_apply_text(state, phase, code, message)
                if kind == "APPLY_MODS_AND_RESTART" else None)
    if state == "CANCELLED":
        return specific or cancelled
    if state not in ("FAILED", "RECOVERY_REQUIRED"):
        return None
    texts = wording.RESTART_APPLY_TEXTS
    # An apply with a start that failed in the check before the start, or in the start, did apply the mods
    if kind == "PUBLISH_MODS_AND_KEYS" and phase in ("VERIFY_BEFORE_START", "START_SERVER"):
        specific = texts["startCheck" if phase == "VERIFY_BEFORE_START" else "start"]
    # These sentences already say what the text of their error code or of the named state says
    said = ((specific == texts["preflight"] and code == "PUBLICATION_PREVIEW_STALE")
            or specific == texts["stopNotRunning"]
            or (specific in (texts["check"], texts["startCheck"]) and code == "PUBLICATION_FAILED"))
    failure = specific or failure
    if state == "RECOVERY_REQUIRED":
        failure = f"{failure} Changes are blocked."
    # A launch failure of a start or restart is already said by the failure sentence
    launch = code == "LAUNCH_FAILED" and kind in ("START_SERVER", "RESTART_SERVER")
    if launch or said or (not code and not message):
        return failure
    return f"{failure} {wording.error_text(code, message)}"


def _schedule_sentence(event: str, fields: Mapping[str, Any]) -> str:
    """Word a saved or a started daily schedule with its action and time."""
    action = SCHEDULE_ACTIONS.get(str(fields.get("action")).lower())
    if event == "schedule.queued":
        return f"The scheduled {action} started." if action else "A scheduled action started."
    if action is None:
        return "Daily schedule turned off."
    hour, minute = fields.get("hour"), fields.get("minute")
    if isinstance(hour, int) and isinstance(minute, int):
        return f"Daily schedule saved: {action} at {hour:02d}:{minute:02d}."
    return f"Daily schedule saved: {action}."
