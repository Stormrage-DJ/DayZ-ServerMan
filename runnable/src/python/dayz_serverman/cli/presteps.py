"""Read-only argument checks of writing commands, run in an observer session before the instance lock (6.4.1).

Each pre-step returns the arguments it resolved; an unknown name that the operator typed exits 2,
and data that is missing behind names that all resolve exits 1 (criterion 27). Nothing changes.
`config set` and `tweaks set` type their `--set` values here (10.6, `cli/edits.py`); the pre-steps
of the profile edits are in `commands/profile_write.py`, those of phase 7 come with that phase.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..application.medical_features import FEATURE_PATHS
from .bridge_client import CliBridgeError
from .edits import check_file_keys, checked_sets, kind_value, like_current, read_object, require_changes
from .exit_codes import USAGE
from .output import CliFailure
from .commands.common import read_data
from .wording import (
    missing_configuration, missing_mission, no_archive_file, overwrite_needs_replace, unknown_backup,
    unknown_feature,
)

# A bridge call of the observer session
Call = Callable[..., Any]


def backup_id(call: Call, options: argparse.Namespace, profile_id: str | None) -> dict[str, Any]:
    """`backup restore ID`: the ID must be in the profile's backup list."""
    listed = call("list_backups", profile_id=profile_id)
    identifiers = {entry.get("backup_id") for entry in listed.get("backups", []) if isinstance(entry, Mapping)}
    if options.backup_id not in identifiers:
        raise CliFailure("USAGE", unknown_backup(options.backup_id), USAGE)
    return {"backup_id": options.backup_id}


def configuration_keys(call: Call, options: argparse.Namespace, profile_id: str | None) -> dict[str, Any]:
    """`config set`: the file is a JSON object of known keys; each `--set` value is typed by its field kind.

    Returns the bridge `updates`: the file first, then each `--set` replaces its key (10.6).
    """
    file_updates = read_object(options.from_file)
    loaded = read_data(lambda: call("load_configuration", profile_id=profile_id, target=options.target),
                       lambda: missing_configuration(options.target, str(profile_id)))
    kinds = {field.get("key"): field.get("kind") for field in loaded.get("fields", []) if isinstance(field, Mapping)}
    show = f"config show --target {options.target}"
    check_file_keys(file_updates, kinds, show)
    sets = {key: kind_value(kinds[key], key, text) for key, text in checked_sets(options.set, kinds, show)}
    updates = {**(file_updates or {}), **sets}
    require_changes(updates)
    return {"updates": updates}


def tweak_keys(call: Call, options: argparse.Namespace, profile_id: str | None) -> dict[str, Any]:
    """`tweaks set`: the file is a JSON object of known keys; each `--set` value is typed like the current one."""
    file_updates = read_object(options.from_file)
    loaded = read_data(lambda: call("load_mission_configuration", profile_id=profile_id,
                                    target=options.target.replace("-", "_")),
                       lambda: missing_mission(str(profile_id)))
    values = loaded.get("values") if isinstance(loaded.get("values"), Mapping) else {}
    show = f"tweaks show --target {options.target}"
    check_file_keys(file_updates, values, show)
    sets = {key: like_current(values.get(key), key, text) for key, text in checked_sets(options.set, values, show)}
    updates = {**(file_updates or {}), **sets}
    require_changes(updates)
    return {"updates": updates}


def medical_feature(call: Call, options: argparse.Namespace, profile_id: str | None) -> dict[str, Any]:
    """`tweaks medical set FEATURE`: the feature name must be one of the application's medical loot settings.

    The typed name is checked against the application's own list before any load (criterion 27 of
    2026-10-08, QF-60 b), so an unknown name exits 2 also when the profile's medical files cannot be
    read or classified. Then the load of a known name reports missing data (exit 1).
    """
    if options.feature not in FEATURE_PATHS:
        raise CliFailure("USAGE", unknown_feature(options.feature), USAGE)
    loaded = read_data(lambda: call("load_medical_features", profile_id=profile_id),
                       lambda: missing_mission(str(profile_id)))
    features = loaded.get("features") if isinstance(loaded.get("features"), Mapping) else {}
    if options.feature not in features:
        raise CliFailure("USAGE", unknown_feature(options.feature), USAGE)
    return {"feature": options.feature}


def archive(call: Call, options: argparse.Namespace, _profile_id: str | None) -> dict[str, Any]:
    """`profile restore --archive ZIP`: the archive inspects as a backup; `--overwrite` needs `--storage replace`.

    The selection token of this observer composition ends with it; the owner session inspects again.
    """
    if options.overwrite and options.storage != "replace":
        raise CliFailure("USAGE", overwrite_needs_replace(), USAGE)
    path = str(Path(options.archive).absolute())
    try:
        inspected = call("inspect_backup_archive", path=path)
    except CliBridgeError as error:
        # The archive cannot be used: an argument error whatever the host's reason; a path with no
        # file says so, and the host's text stays for a file that exists but cannot be used (QF-43)
        failure = error.failure()
        message = failure.message if Path(path).exists() else no_archive_file(options.archive)
        raise CliFailure(failure.code, message, USAGE, failure.retryable, failure.details) from error
    return {"archive": path, "inspected": inspected}


# The names of 2.5 that the parser tests call; the rules live in `cli/edits.py` since task 6.1
_from_file = read_object
_checked_sets = checked_sets
