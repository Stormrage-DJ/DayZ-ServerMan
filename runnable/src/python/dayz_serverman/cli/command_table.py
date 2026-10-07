"""The command table of 01.04 with the options of design section 10; later phases fill in the handlers."""

from __future__ import annotations

from .registry import CommandSpec, Confirm, Form, Kind, OptionSpec, ProfileRule

# Options that several commands share
PROFILE = OptionSpec("profile", Form.VALUE, "--profile", metavar="ID-OR-NAME")
EXPECT_PROFILE = OptionSpec("expect_profile_revision", Form.INTEGER, "--expect-profile-revision", minimum=0,
                            metavar="N")
EXPECT_SETTINGS = OptionSpec("expect_settings_revision", Form.INTEGER, "--expect-settings-revision", minimum=0,
                             metavar="N")
BACKUP_AFTER_STOP = OptionSpec("backup_after_stop", Form.BOOL_PAIR, "--backup-after-stop")
SET = OptionSpec("set", Form.APPEND, "--set", metavar="KEY=VALUE")
FROM_FILE = OptionSpec("from_file", Form.VALUE, "--from-file", metavar="FILE")
ON_OFF = OptionSpec("state", Form.POSITIONAL, choices=("on", "off"))
# Revisions that a write sends, and the profile that it needs
REVISIONS = (PROFILE, EXPECT_PROFILE, EXPECT_SETTINGS)
CONFIG_TARGETS = ("server", "gameplay")
TWEAK_TARGETS = ("economy", "weather", "spawnable-damage", "starter-loadout", "events")


def read(path: str, phase: int, methods: tuple[str, ...], *options: OptionSpec,
         profile: ProfileRule = ProfileRule.NONE, handler: str | None = None) -> CommandSpec:
    """Return a read-only command; with READ_DEFAULT it takes `--profile`; `handler` names its function."""
    if profile is not ProfileRule.NONE:
        options = (PROFILE, *options)
    return CommandSpec(tuple(path.split()), Kind.READ, phase, frozenset(methods), options,
                       profile=profile, handler=handler)


def write(path: str, phase: int, methods: tuple[str, ...], *options: OptionSpec,
          confirm: Confirm = Confirm.NONE, profile: ProfileRule = ProfileRule.NONE,
          prestep: str | None = None, handler: str | None = None) -> CommandSpec:
    """Return a writing command; with WRITE_REQUIRED it takes `--profile` (part of `options`)."""
    return CommandSpec(tuple(path.split()), Kind.WRITE, phase, frozenset(methods), options,
                       confirm=confirm, profile=profile, prestep=prestep, handler=handler)


NEEDS = ProfileRule.WRITE_REQUIRED
DEFAULT = ProfileRule.READ_DEFAULT
# Reads of a write that needs the profile and settings revisions (10.1 "rev")
REV_READS = ("read_profile", "get_application_snapshot")

COMMANDS: tuple[CommandSpec, ...] = (
    # Phase 2: reads (10.1)
    read("status", 2, ("get_application_snapshot", "get_server_status", "get_update_status", "get_ui_preferences"),
         profile=DEFAULT, handler="commands.status:run"),
    read("server status", 2, ("get_server_status",), handler="commands.server:status"),
    read("server players", 2, ("get_online_players",), OptionSpec("names", Form.FLAG, "--names"),
         handler="commands.server:players"),
    # Phase 3: lifecycle (10.1)
    write("server start", 3, (*REV_READS, "get_server_status", "start_server"), *REVISIONS,
          OptionSpec("wait_ready", Form.FLAG, "--wait-ready"),
          OptionSpec("ready_timeout", Form.INTEGER, "--ready-timeout", minimum=10, maximum=3600, metavar="SECONDS"),
          confirm=Confirm.CONFIRM, profile=NEEDS, handler="commands.lifecycle:start"),
    # The D11 refusal names the running profile: get_server_status and list_profiles after a refusal
    write("server stop", 3, (*REV_READS, "get_ui_preferences", "stop_server", "get_server_status", "list_profiles"),
          *REVISIONS, BACKUP_AFTER_STOP, confirm=Confirm.CONFIRM, profile=NEEDS, handler="commands.lifecycle:stop"),
    write("server restart", 3, (*REV_READS, "get_ui_preferences", "restart_server", "get_server_status",
                                "list_profiles"),
          *REVISIONS, BACKUP_AFTER_STOP, confirm=Confirm.CONFIRM, profile=NEEDS,
          handler="commands.lifecycle:restart"),
    read("schedule show", 2, ("get_lifecycle_schedule",), profile=DEFAULT, handler="commands.schedule:schedule_show"),
    write("schedule set", 3, ("save_lifecycle_schedule",), PROFILE,
          OptionSpec("time", Form.TIME, metavar="HH:MM"),
          OptionSpec("action", Form.POSITIONAL, choices=("stop", "restart")), profile=NEEDS,
          handler="commands.schedule:schedule_set"),
    write("schedule clear", 3, ("get_lifecycle_schedule", "save_lifecycle_schedule"), PROFILE, profile=NEEDS,
          handler="commands.schedule:schedule_clear"),
    write("profile backup-after-stop", 3, ("save_backup_after_stop",), PROFILE, ON_OFF, profile=NEEDS,
          handler="commands.profile:backup_after_stop"),
    # Phase 2 and 4: backups (10.1, 10.2)
    read("backup list", 2, ("list_backups", "list_backup_catalog"), OptionSpec("all", Form.FLAG, "--all"),
         profile=DEFAULT, handler="commands.backup:backup_list"),
    write("backup create", 4, (*REV_READS, "create_backup"), *REVISIONS, profile=NEEDS),
    write("backup restore", 4, ("list_backups", "preview_restore", "apply_restore"), PROFILE,
          OptionSpec("backup_id", Form.POSITIONAL, metavar="BACKUP-ID"),
          confirm=Confirm.REVIEW, profile=NEEDS, prestep="presteps:backup_id"),
    write("backup recover", 4, ("inspect_restore_recovery",)),
    write("profile restore", 4, ("inspect_backup_archive", "preview_profile_restore", "restore_profile_from_backup"),
          OptionSpec("archive", Form.VALUE, "--archive", required=True, metavar="ZIP"),
          OptionSpec("profile_id", Form.VALUE, "--profile-id", metavar="ID"),
          OptionSpec("name", Form.VALUE, "--name", metavar="NAME"),
          OptionSpec("game_port", Form.INTEGER, "--game-port", minimum=1, maximum=65535, metavar="PORT"),
          OptionSpec("query_port", Form.INTEGER, "--query-port", minimum=1, maximum=65535, metavar="PORT"),
          OptionSpec("storage", Form.CHOICE, "--storage", choices=("preserve", "new", "replace")),
          OptionSpec("overwrite", Form.FLAG, "--overwrite"),
          confirm=Confirm.REVIEW, prestep="presteps:archive"),
    # Phase 2 and 5: updates, mods and Steam (10.1, 10.3)
    read("updates status", 2, ("get_update_status", "get_ui_preferences"), profile=DEFAULT,
         handler="commands.updates:updates_status"),
    write("updates check", 5, ("request_update_check", "get_update_status", "get_ui_preferences"),
          OptionSpec("scope", Form.CHOICE, "--scope", choices=("mods", "server-build", "all")),
          OptionSpec("force", Form.FLAG, "--force")),
    write("updates auto", 5, ("save_automatic_update_checks",), ON_OFF),
    read("mods list", 2, ("list_mod_inventory",), profile=DEFAULT, handler="commands.mods:mods_list"),
    write("mods verify", 5, (*REV_READS, "verify_mod_files"), *REVISIONS, profile=NEEDS),
    write("mods update", 5, (*REV_READS, "get_server_status", "get_ui_preferences", "update_workshop_items",
                             "preview_mod_publication", "publish_mods_and_keys", "apply_mods_and_restart"),
          *REVISIONS, OptionSpec("start", Form.FLAG, "--start", group="start"),
          OptionSpec("restart", Form.FLAG, "--restart", group="start"), BACKUP_AFTER_STOP,
          confirm=Confirm.REVIEW, profile=NEEDS),
    read("steam show", 2, ("get_application_snapshot",), handler="commands.steam:steam_show"),
    write("steam set", 5, ("get_application_snapshot", "save_steam_settings"), EXPECT_SETTINGS,
          OptionSpec("mode", Form.CHOICE, "--mode", choices=("account", "anonymous"), required=True),
          OptionSpec("account", Form.VALUE, "--account", metavar="NAME")),
    write("steam login", 5, ("get_application_snapshot", "authenticate_steamcmd"), EXPECT_SETTINGS),
    # Phase 2 and 6: profiles (10.1, 10.4)
    read("profile list", 2, ("list_profiles", "get_ui_preferences"), handler="commands.profile:profile_list"),
    read("profile show", 2, ("read_profile",), profile=DEFAULT, handler="commands.profile:profile_show"),
    read("profile command", 2, ("preview_profile_command",), profile=DEFAULT,
         handler="commands.profile:profile_command"),
    read("profile missions", 2, ("list_profile_missions",), handler="commands.profile:profile_missions"),
    write("profile create", 6, ("get_application_snapshot", "provision_profile"), SET, FROM_FILE, EXPECT_SETTINGS),
    write("profile edit", 6, ("read_profile", "save_profile"), PROFILE, EXPECT_PROFILE, SET, FROM_FILE,
          profile=NEEDS),
    write("profile delete", 6, ("read_profile", "delete_profile"), PROFILE, EXPECT_PROFILE,
          confirm=Confirm.CONFIRM, profile=NEEDS),
    # Phase 2 and 6: configuration and tweaks (10.1, 10.4)
    read("config show", 2, ("load_configuration",),
         OptionSpec("target", Form.CHOICE, "--target", choices=CONFIG_TARGETS, required=True), profile=DEFAULT,
         handler="commands.config:config_show"),
    write("config set", 6, ("load_configuration", "preview_configuration", "apply_configuration"), PROFILE,
          OptionSpec("target", Form.CHOICE, "--target", choices=CONFIG_TARGETS, required=True), SET, FROM_FILE,
          confirm=Confirm.REVIEW, profile=NEEDS, prestep="presteps:configuration_keys"),
    read("tweaks show", 2, ("load_mission_configuration",),
         OptionSpec("target", Form.CHOICE, "--target", choices=TWEAK_TARGETS, required=True), profile=DEFAULT,
         handler="commands.tweaks:tweaks_show"),
    write("tweaks set", 6, ("load_mission_configuration", "preview_mission_configuration",
                            "apply_mission_configuration"), PROFILE,
          OptionSpec("target", Form.CHOICE, "--target", choices=TWEAK_TARGETS, required=True), SET, FROM_FILE,
          confirm=Confirm.REVIEW, profile=NEEDS, prestep="presteps:tweak_keys"),
    write("tweaks convert-loadout", 6, ("load_mission_configuration", "convert_starter_loadout"), PROFILE,
          profile=NEEDS),
    read("tweaks medical show", 2, ("load_medical_features",), profile=DEFAULT,
         handler="commands.tweaks:medical_show"),
    write("tweaks medical set", 6, ("load_medical_features", "preview_medical_feature", "apply_medical_feature"),
          PROFILE, OptionSpec("feature", Form.POSITIONAL, metavar="FEATURE"), ON_OFF,
          confirm=Confirm.REVIEW, profile=NEEDS, prestep="presteps:medical_feature"),
    # Phase 2: logs (10.1)
    read("logs", 2, ("read_log",),
         OptionSpec("source", Form.CHOICE, "--source", choices=("manager", "diagnostics", "server")),
         OptionSpec("lines", Form.INTEGER, "--lines", minimum=1, maximum=1000, metavar="N"), handler="commands.logs:logs"),
    # Phase 2 and 7: settings and migration (10.1, 10.4)
    read("settings show", 2, ("get_application_snapshot",), handler="commands.settings:settings_show"),
    read("settings check-path", 7, ("validate_settings_path_selection",),
         OptionSpec("role", Form.CHOICE, "--role", choices=("dayz-root", "steamcmd-root", "backup-root"),
                    required=True),
         OptionSpec("path", Form.POSITIONAL, metavar="PATH")),
    write("settings set", 7, ("get_application_snapshot", "validate_settings_path_selection", "save_settings"),
          EXPECT_SETTINGS, OptionSpec("dayz_root", Form.VALUE, "--dayz-root", metavar="PATH"),
          OptionSpec("steamcmd_root", Form.VALUE, "--steamcmd-root", metavar="PATH"),
          OptionSpec("backup_root", Form.VALUE, "--backup-root", group="backup", metavar="PATH"),
          OptionSpec("default_backup_root", Form.FLAG, "--default-backup-root", group="backup")),
    read("migrate preview", 7, ("select_legacy_root", "preview_legacy_import"),
         OptionSpec("root", Form.POSITIONAL, metavar="ROOT")),
    write("migrate apply", 7, ("select_legacy_root", "preview_legacy_import", "apply_legacy_import"),
          OptionSpec("root", Form.POSITIONAL, metavar="ROOT"),
          OptionSpec("item", Form.APPEND, "--item", group="items", metavar="ID"),
          OptionSpec("all_items", Form.FLAG, "--all-items", group="items"),
          confirm=Confirm.REVIEW),
)
