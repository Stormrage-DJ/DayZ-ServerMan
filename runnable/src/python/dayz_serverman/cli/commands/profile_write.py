"""`profile create`, `profile edit` and `profile delete` (10.4, 10.6; criteria 5, 6, 8 and 13).

Create and edit act at once, as the Profiles page's form submit does; their pre-steps type
`--set` and read `--from-file` before the instance lock (6.4.1). Delete confirms like the
window's "Delete profile?" dialog; the server-folder writer side (A13) is the deletion
service's, so a held observer read refuses it with nothing changed.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from typing import Any

from ..edit_review import profile_delete_review
from ..edit_wording import EXPECTED_ARRAY, create_values_missing, delete_needs_stop, profile_created, profile_id_fixed
from ..edits import (
    check_file_keys, checked_sets, integer, json_of, like_current, read_object, require_changes,
)
from ..exit_codes import REFUSED, USAGE
from ..flow import run_operation, stop_if_interrupted
from ..output import CliFailure, CommandResult, Table
from .common import profile_id, profile_line, profile_name
from .write_common import ask, check_pins, check_recovery_block, write_result

# The bridge `profile` object of `provision_profile`, exactly (`application/profile_provisioning.py`)
CREATE_FIELDS = ("profile_id", "display_name", "server_executable", "mission_root", "game_port", "mods",
                 "extra_arguments")
# Values that the window's creation form fills itself (`frontend/profile_create.js`)
CREATE_DEFAULTS: dict[str, Any] = {"server_executable": "DayZServer_x64.exe", "mods": [], "extra_arguments": []}
# Values that the operator must give: the form requires them, and provisioning refuses without them
CREATE_REQUIRED = ("profile_id", "display_name", "mission_root", "game_port")
# Text fields that may be empty: an empty value sends null (10.6)
NULLABLE_TEXT = frozenset(("mission_root", "runtime_profile"))
# Members of the read profile document that are not fields of the saved profile
RECORD_ONLY = frozenset(("revision", "semantic_digest"))
# The command that prints the keys of a profile
SHOW = "profile show"


def create_values(_call: Callable[..., Any], options: argparse.Namespace, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step of `profile create`: the defaults, then the file, then each typed `--set` (10.6)."""
    document = read_object(options.from_file)
    check_file_keys(document, CREATE_FIELDS, SHOW)
    sets = {key: create_value(key, text) for key, text in checked_sets(options.set, CREATE_FIELDS, SHOW)}
    profile = {**CREATE_DEFAULTS, **(document or {}), **sets}
    missing = [key for key in CREATE_REQUIRED if profile.get(key) in (None, "")]
    if missing:
        raise CliFailure("USAGE", create_values_missing(missing), USAGE)
    return {"profile": {key: profile.get(key) for key in CREATE_FIELDS}}


def create_value(key: str, text: str) -> Any:
    """Type one `profile create` value by the fixed table of 10.6."""
    if key == "game_port":
        return integer(key, text)
    if key in ("mods", "extra_arguments"):
        return json_of(list, key, text, EXPECTED_ARRAY)
    if key in NULLABLE_TEXT:
        return text or None
    return text


def edit_values(call: Callable[..., Any], options: argparse.Namespace, profile_id_: str | None) -> dict[str, Any]:
    """Pre-step of `profile edit`: the file and each `--set`, typed like the profile's current values."""
    document = read_object(options.from_file)
    fields = _fields(call("read_profile", profile_id=profile_id_))
    check_file_keys(document, fields, SHOW)
    if document is not None and "profile_id" in document and document["profile_id"] != profile_id_:
        raise CliFailure("USAGE", profile_id_fixed(), USAGE)
    pairs = checked_sets(options.set, fields, SHOW)
    if any(key == "profile_id" for key, _text in pairs):
        raise CliFailure("USAGE", profile_id_fixed(), USAGE)
    changes = {**(document or {}),
               **{key: (text or None) if key in NULLABLE_TEXT else like_current(fields[key], key, text)
                  for key, text in pairs}}
    require_changes(changes)
    return {"changes": changes}


def create(context: Any) -> CommandResult:
    """Create a profile with its generated server files (`provision_profile`), as the creation form does."""
    stop_if_interrupted(context.interrupts)
    snapshot = context.call("get_application_snapshot")
    revision = (snapshot.get("settings") or {}).get("revision")
    check_pins(context, None, revision)
    profile = context.resolved["profile"]
    record = run_operation(context, "provision_profile", "PROVISION_PROFILE", profile=profile,
                           expected_settings_revision=revision)
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    created = result.get("profile") if isinstance(result.get("profile"), Mapping) else profile
    table = Table(("Profile", "ID"), ((profile_name(created), str(created.get("profile_id"))),),
                  input_columns=frozenset({1}), value_columns=frozenset({0}))
    return write_result(record, None, [table, profile_created(result)])


def edit(context: Any) -> CommandResult:
    """Save the profile with the typed changes over the values just read (`save_profile`)."""
    stop_if_interrupted(context.interrupts)
    document = context.call("read_profile", profile_id=profile_id(context.profile))
    check_pins(context, document.get("revision"), None)
    profile = {**_fields(document), **context.resolved["changes"]}
    record = run_operation(context, "save_profile", "SAVE_PROFILE", profile=profile,
                           expected_revision=document.get("revision"))
    saved = record.get("result") if isinstance(record.get("result"), Mapping) else profile
    return write_result(record, None, [profile_line(saved)])


def delete(context: Any) -> CommandResult:
    """Delete the profile after the window's "Delete profile?" dialog; its backups stay."""
    stop_if_interrupted(context.interrupts)
    # State gate before the question (rule 5): a recovery block exits 6, a server that is not stopped 3
    check_recovery_block(context.call("get_application_snapshot"))
    document = context.call("read_profile", profile_id=profile_id(context.profile))
    check_pins(context, document.get("revision"), None)
    state = str(context.call("get_server_status").get("state"))
    if state != "STOPPED":
        raise CliFailure("DELETION_BLOCKED", delete_needs_stop(), REFUSED, True, {"state": state})
    request = {"profile_id": profile_id(context.profile), "expected_revision": document.get("revision")}
    ask(context, profile_delete_review(document), request)
    record = run_operation(context, "delete_profile", "DELETE_PROFILE", **request)
    return write_result(record, request, [profile_line(context.profile)])


def _fields(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the saved-profile fields of a read profile document."""
    return {key: value for key, value in document.items() if key not in RECORD_ONLY}
