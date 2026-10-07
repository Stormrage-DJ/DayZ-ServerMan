"""`profile list`, `show`, `command`, `missions` and `backup-after-stop` (10.1); other writes come with phases 4 and 6."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from typing import Any

from ..flow import stop_if_interrupted
from ..output import CommandResult, Line, Table, sentence
from ..wording import no_profile_exists
from .common import labelled, profile_id, profile_line, with_profile

# Label and key of each profile value, in the order of the window's profile form (profiles.js)
PROFILE_FIELDS = (
    ("Display name", "display_name"), ("Game port", "game_port"), ("Server executable", "server_executable"),
    ("Server config", "server_config"), ("Mission root", "mission_root"), ("Runtime profile", "runtime_profile"),
    ("Mods", "mods"), ("Extra arguments", "extra_arguments"),
)
NOT_SET = "Not set"


def profile_list(context: Any) -> CommandResult:
    """List the profiles with the selected one and the backup-after-stop choice; the ID is in its own column."""
    profiles = [profile for profile in context.call("list_profiles") if isinstance(profile, Mapping)]
    preferences = context.call("get_ui_preferences")
    value = {"profiles": profiles, "preferences": preferences}
    if not profiles:
        return CommandResult(value, [no_profile_exists()])
    selected = preferences.get("selected_profile_id")
    backups = set(preferences.get("backup_after_stop_profiles") or [])
    rows = tuple((str(profile.get("display_name") or ""), "Yes" if profile.get("profile_id") == selected else "",
                  "Yes" if profile.get("profile_id") in backups else "No", str(profile.get("profile_id")))
                 for profile in profiles)
    table = Table(("Profile", "Selected", "Backup after stop", "ID"), rows,
                  input_columns=frozenset({3}), value_columns=frozenset({0}))
    return CommandResult(value, [table])


def profile_show(context: Any) -> CommandResult:
    """Show the values of the profile, each with its label and the key that `profile edit` takes."""
    document = context.call("read_profile", profile_id=profile_id(context.profile))
    rows = tuple((label, _value_text(document.get(key)), key) for label, key in PROFILE_FIELDS)
    table = Table(("Setting", "Value", "Key"), rows, input_columns=frozenset({2}), value_columns=frozenset({1}))
    return CommandResult(document, [profile_line(context.profile), table])


def _value_text(value: object) -> str:
    """Word one stored profile value: mods by folder and scope, lists joined, an empty value as not set."""
    if value is None or value == "" or value == []:
        return NOT_SET
    if isinstance(value, list):
        if all(isinstance(entry, Mapping) for entry in value):
            return ", ".join(f"{entry.get('directory')} ({'server' if entry.get('launch_scope') == 'server' else 'client'})"
                             for entry in value)
        return " ".join(str(entry) for entry in value)
    return str(value)


def profile_command(context: Any) -> CommandResult:
    """Show the program, the working folder and the command line that starts the server with the profile."""
    preview = context.call("preview_profile_command", profile_id=profile_id(context.profile))
    lines: list[Line] = [profile_line(context.profile),
                         labelled("Program", str(preview.get("executable", ""))),
                         labelled("Working folder", str(preview.get("working_directory", ""))),
                         labelled("Command line", subprocess.list2cmdline([str(part) for part in preview.get("argv", [])]))]
    return CommandResult(with_profile(preview, context.profile), lines)


def profile_missions(context: Any) -> CommandResult:
    """List the missions in the DayZ server folder that a profile can use."""
    answer = context.call("list_profile_missions")
    missions = [mission for mission in answer.get("missions", []) if isinstance(mission, Mapping)]
    if not missions:
        return CommandResult(answer, [sentence("No missions were found in the DayZ server folder.")])
    table = Table(("Mission", "Folder"), tuple((str(mission.get("display_name", "")), str(mission.get("relative_path", "")))
                                               for mission in missions), value_columns=frozenset({0, 1}))
    return CommandResult(answer, [table])


def backup_after_stop(context: Any) -> CommandResult:
    """Save whether a stop or restart of the profile creates a verified backup after DayZ stops."""
    stop_if_interrupted(context.interrupts)
    enabled = context.options.state == "on"
    saved = context.call("save_backup_after_stop", profile_id=profile_id(context.profile), enabled=enabled)
    value = {"operations": [], "review": None, "result": saved}
    return CommandResult(value, [profile_line(context.profile),
                                 sentence(f"Backup after stop: {'on' if enabled else 'off'}")])
