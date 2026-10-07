"""Read-only argument checks of writing commands, run in an observer session before the instance lock (6.4.1).

Each pre-step returns the arguments it resolved; an unknown name that the operator typed exits 2,
and data that is missing behind names that all resolve exits 1 (criterion 27). Nothing changes.
The typing of `--set` values (10.6) and the pre-steps of phases 6 and 7 come with those phases.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import read_text_shared
from .bridge_client import CliBridgeError
from .exit_codes import USAGE
from .output import CliFailure
from .commands.common import read_data
from .wording import (
    duplicate_key, invalid_file, invalid_set, missing_configuration, missing_mission, overwrite_needs_replace,
    unknown_backup, unknown_feature, unknown_key,
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
    """`config set`: the file is a JSON object and every `--set` key is a field of the loaded target."""
    file_updates = _from_file(options.from_file)
    loaded = read_data(lambda: call("load_configuration", profile_id=profile_id, target=options.target),
                       lambda: missing_configuration(options.target, str(profile_id)))
    known = {field.get("key") for field in loaded.get("fields", []) if isinstance(field, Mapping)}
    sets = _checked_sets(options.set, known, f"config show --target {options.target}")
    return {"file_updates": file_updates, "sets": sets}


def tweak_keys(call: Call, options: argparse.Namespace, profile_id: str | None) -> dict[str, Any]:
    """`tweaks set`: the file is a JSON object and every `--set` key is a value of the loaded target."""
    file_updates = _from_file(options.from_file)
    loaded = read_data(lambda: call("load_mission_configuration", profile_id=profile_id,
                                    target=options.target.replace("-", "_")),
                       lambda: missing_mission(str(profile_id)))
    values = loaded.get("values")
    known = set(values) if isinstance(values, Mapping) else set()
    sets = _checked_sets(options.set, known, f"tweaks show --target {options.target}")
    return {"file_updates": file_updates, "sets": sets}


def medical_feature(call: Call, options: argparse.Namespace, profile_id: str | None) -> dict[str, Any]:
    """`tweaks medical set FEATURE`: the feature name must be in the profile's medical loot settings."""
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
        # The archive cannot be used: an argument error whatever the host's reason
        failure = error.failure()
        raise CliFailure(failure.code, failure.message, USAGE, failure.retryable, failure.details) from error
    return {"archive": path, "inspected": inspected}


def _from_file(path: str | None) -> dict[str, Any] | None:
    """Read a `--from-file` file: UTF-8 JSON whose top level is an object."""
    if path is None:
        return None
    try:
        # A12: every file read goes through the shared-read opener
        document = json.loads(read_text_shared(Path(path), encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        document = None
    if not isinstance(document, dict):
        raise CliFailure("USAGE", invalid_file(path), USAGE)
    return document


def _checked_sets(values: list[str] | None, known: set[Any], show_command: str) -> list[tuple[str, str]]:
    """Split each `--set` at its first `=`; refuse an unknown key, a key given twice and a missing `=`."""
    pairs: list[tuple[str, str]] = []
    for value in values or []:
        key, separator, text = value.partition("=")
        if not separator or not key:
            raise CliFailure("USAGE", invalid_set(value), USAGE)
        if key not in known:
            raise CliFailure("USAGE", unknown_key(key, show_command), USAGE)
        if any(key == seen for seen, _text in pairs):
            raise CliFailure("USAGE", duplicate_key(key), USAGE)
        pairs.append((key, text))
    return pairs
