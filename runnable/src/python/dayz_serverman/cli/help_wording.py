"""Help texts of the CLI: one operator sentence per noun, command and option (design 6.2, 11.3)."""

from __future__ import annotations

# One sentence per noun, shown in the command list
NOUN_TEXTS: dict[str, str] = {
    "status": "Show the server, the update state and any unfinished work at a glance.",
    "server": "Show, start, stop and restart the DayZ server.",
    "schedule": "Show and change the daily stop or restart time.",
    "backup": "List, create and restore backups.",
    "updates": "Show and run update checks.",
    "mods": "List, verify, update and apply mods.",
    "steam": "Show and change the Steam sign-in.",
    "profile": "List, show, create, change and delete server profiles.",
    "config": "Show and change the server configuration.",
    "tweaks": "Show and change the mission tweaks and the medical loot settings.",
    "logs": "Show the latest log lines.",
    "settings": "Show and change the folders of DayZ-ServerMan.",
    "migrate": "Preview and import data from an older DayZ-ServerMan.",
    "help": "Show the commands, or the help of one command.",
}

# One sentence per command, keyed by the command name
COMMAND_TEXTS: dict[str, str] = {
    "status": "Show the server state, the player count, the update state and any unfinished work.",
    "server status": "Show the state of the DayZ server.",
    "server players": "Show the players who are online.",
    "server start": "Start the DayZ server with a profile.",
    "server stop": "Save the world and stop the DayZ server.",
    "server restart": "Stop the DayZ server and start it again.",
    "schedule show": "Show the daily stop or restart time of a profile.",
    "schedule set": "Set the daily stop or restart time of a profile.",
    "schedule clear": "Turn off the daily stop or restart of a profile.",
    "backup list": "List the backups of a profile.",
    "backup create": "Create a verified backup of a profile.",
    "backup restore": "Restore a backup over the server files of a profile, after a review.",
    "backup recover": "Finish or undo a backup restore that was interrupted.",
    "updates status": "Show the result of the last update checks.",
    "updates check": "Check for mod and server updates now and wait for the result.",
    "updates auto": "Turn the automatic update checks on or off.",
    "mods list": "List the mods of a profile and their update state.",
    "mods verify": "Verify the downloaded mod files and the copies in the server folder.",
    "mods update": "Download changed mods, review the plan and apply them to the server.",
    "steam show": "Show the Steam sign-in mode and account.",
    "steam set": "Change the Steam sign-in mode and account.",
    "steam login": "Sign in to Steam in this terminal.",
    "profile list": "List the profiles.",
    "profile show": "Show the values of a profile.",
    "profile command": "Show the command line that starts the server with a profile.",
    "profile missions": "List the missions that a profile can use.",
    "profile create": "Create a profile and its server files.",
    "profile edit": "Change the values of a profile.",
    "profile delete": "Delete a profile and its server files; its backups stay.",
    "profile backup-after-stop": "Turn the backup after each stop of a profile on or off.",
    "profile restore": "Create or replace a profile from a backup archive, after a review.",
    "config show": "Show the server or gameplay configuration of a profile.",
    "config set": "Change the server or gameplay configuration of a profile, after a review.",
    "tweaks show": "Show the mission tweaks of a profile.",
    "tweaks set": "Change the mission tweaks of a profile, after a review.",
    "tweaks convert-loadout": "Convert the starter loadout of a profile to the editable form.",
    "tweaks medical show": "Show the medical loot settings of a profile.",
    "tweaks medical set": "Turn one medical loot setting on or off, after a review.",
    "logs": "Show the latest lines of a log.",
    "settings show": "Show the folders of DayZ-ServerMan and their checks.",
    "settings check-path": "Check whether a folder can be used for a setting.",
    "settings set": "Change the folders of DayZ-ServerMan.",
    "migrate preview": "Show what an import from an older DayZ-ServerMan folder would bring.",
    "migrate apply": "Import data from an older DayZ-ServerMan folder, after a review.",
}

# One sentence per option, keyed by its destination; the common options come first
OPTION_TEXTS: dict[str, str] = {
    "json": "Print one JSON document instead of text.",
    "yes": "Answer yes to the confirmation question; reviews and checks still run.",
    "help": "Show the help of this command.",
    "profile": "The profile to use, by its ID or its name.",
    "expect_profile_revision": "Refuse when the profile revision is not this number.",
    "expect_settings_revision": "Refuse when the settings revision is not this number.",
    "names": "Show the player names also when the output is not a terminal.",
    "wait_ready": "Wait until the server answers Steam queries.",
    "ready_timeout": "Seconds to wait for the server to answer, from 10 to 3600 (default 300).",
    "backup_after_stop": "Create a backup after the stop; the default is the saved choice of the profile.",
    "time": "The time of day, as hours and minutes (00:00 to 23:59).",
    "action": "What happens at that time: stop or restart.",
    "all": "List every backup in the backup folder, of every profile.",
    "backup_id": "The ID of the backup, as backup list shows it.",
    "archive": "The backup archive (ZIP file) to restore from.",
    "profile_id": "The ID of the new profile.",
    "name": "The name of the new profile.",
    "game_port": "The game port of the new profile.",
    "query_port": "The Steam query port of the new profile.",
    "storage": "Where the world goes: keep the original place, a new place, or replace an existing world.",
    "overwrite": "Confirm that the restore replaces an existing world.",
    "scope": "What to check: mods, the server build, or both (default).",
    "force": "Check again even when a check ran a short time ago.",
    "state": "Turn the setting on or off.",
    "mode": "Sign in with a Steam account or anonymously.",
    "account": "The Steam account name for account sign-in.",
    "start": "Apply the mods, then start the stopped server.",
    "restart": "Stop the running server, apply the mods, then start it again.",
    "target": "Which part to show or change.",
    "set": "One change as key=value; repeat it for several changes.",
    "from_file": "A UTF-8 JSON file with the changes.",
    "feature": "The medical loot setting, as tweaks medical show lists it.",
    "source": "Which log to show: manager (default), diagnostics or server.",
    "lines": "How many of the latest lines to show, from 1 to 1000 (default 100).",
    "role": "Which setting the folder is for.",
    "path": "The folder to check.",
    "dayz_root": "The DayZ server folder.",
    "steamcmd_root": "The SteamCMD folder.",
    "backup_root": "A backup folder of your choice.",
    "default_backup_root": "Use the backup folder inside the DayZ-ServerMan folder again.",
    "root": "The folder of the older DayZ-ServerMan.",
    "item": "One item to import, by its ID; repeat it for several items.",
    "all_items": "Import every item that can be imported (the default when no item is named).",
}

# The lines of the general help, before and after the command list
HELP_INTRO = "Run DayZ-ServerMan commands from a terminal or a script."
HELP_USAGE = "Usage: DayZ-ServerMan.py --cli <command> [options]"
HELP_OUTRO = "Run help <command> for the options of one command."
COMMAND_LIST_TITLE = "Commands:"
OPTION_LIST_TITLE = "Options:"
EXIT_CODES_TITLE = "Exit codes:"
# The exit codes of the R1 rule, in operator words
EXIT_CODE_TEXTS: tuple[tuple[str, str], ...] = (
    ("0", "Done."),
    ("1", "Failed."),
    ("2", "The command line is not valid; nothing was changed."),
    ("3", "Refused in the current state; nothing was changed."),
    ("4", "Not confirmed; nothing was changed."),
    ("5", "Cancelled at a safe point."),
    ("6", "Recovery is needed before changes are possible."),
)
