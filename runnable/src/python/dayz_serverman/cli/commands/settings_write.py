"""`settings set [--dayz-root P] [--steamcmd-root P] [--backup-root P / --default-backup-root]` (10.4, 6.4.1).

The Settings page saves its three folders together; a field that the request leaves out would
be saved as empty (`application/coordinator.py` `save_settings`). So the command sends all three:
the stored values, with the ones the operator named replaced. The window's Save asks no question.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.activity_wording import ROLE_LABELS
from ..exit_codes import USAGE
from ..flow import run_operation, stop_if_interrupted
from ..output import CliFailure, CommandResult
from ..read_wording import path_status
from ..settings_wording import folder_path, folder_unusable, no_location_named, restart_needed
from .settings import location_blocks
from .write_common import check_pins

# The folders of the settings save, with the option that names each one
LOCATION_OPTIONS = (("dayz_root", "dayz_root"), ("steamcmd_root", "steamcmd_root"),
                    ("custom_backup_root", "backup_root"))
# Checks that the folder dialog can never give: no folder at the path, or a file
NOT_A_FOLDER = frozenset(("MISSING", "NOT_DIRECTORY"))


def location_values(call: Any, options: Any, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step: each named folder passes the Settings page's folder check (exit 2 before the lock).

    A path that the check refuses exits 2 (PATH_INVALID at dispatch); a path where no folder
    exists, or that is a file, exits 2 as well, because the window's folder dialog cannot give it.
    """
    named = {field: getattr(options, option) for field, option in LOCATION_OPTIONS
             if getattr(options, option) is not None}
    if not named and not options.default_backup_root:
        raise CliFailure("USAGE", no_location_named(), USAGE)
    locations: dict[str, str | None] = {}
    for field, text in named.items():
        checked = call("validate_settings_path_selection", role=field, path=folder_path(text))
        if checked.get("status") in NOT_A_FOLDER:
            _status, explanation = path_status(ROLE_LABELS[field], checked)
            raise CliFailure("USAGE", folder_unusable(text, explanation), USAGE)
        # The path as the check normalized it, as the window keeps the dialog's answer
        locations[field] = str(checked.get("path") or folder_path(text))
    if options.default_backup_root:
        # The portable default: the window saves no custom backup folder
        locations["custom_backup_root"] = None
    return {"locations": locations}


def settings_set(context: Any) -> CommandResult:
    """Save the three folders under the settings revision just read; report the checks and a restart."""
    stop_if_interrupted(context.interrupts)
    snapshot = context.call("get_application_snapshot")
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), Mapping) else {}
    check_pins(context, None, settings.get("revision"))
    # Every folder that the operator did not name keeps its stored value
    values = {field: settings.get(field) for field, _option in LOCATION_OPTIONS}
    values.update(context.resolved["locations"])
    record = run_operation(context, "save_settings", "SAVE_SETTINGS", expected_revision=settings.get("revision"),
                           **values)
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    # The Settings page reloads after the save and shows the checks of the saved folders
    blocks = location_blocks(context.call("get_application_snapshot"))
    if result.get("restart_required") is True:
        blocks.extend(restart_needed())
    return CommandResult({"operations": [dict(record)], "review": None, "result": dict(result)}, blocks)
