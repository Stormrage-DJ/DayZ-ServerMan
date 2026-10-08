"""`settings show` (10.1) and `settings check-path` (10.4); `settings set` is in `settings_write.py`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.activity_wording import ROLE_LABELS
from ..output import Block, CommandResult, Table, sentence
from ..read_wording import path_status
from ..settings_wording import BACKUP_CUSTOM, BACKUP_PORTABLE, folder_path
from .common import labelled

# The locations of the Settings page, in its order
ROLES = ("dayz_root", "dayz_executable", "steamcmd_root", "steamcmd_executable", "workshop_content_root",
         "backup_root")
NOT_SET = "Not set"
# `--role` choices and the roles of `validate_settings_path_selection` (10.4)
CHECK_ROLES = {"dayz-root": "dayz_root", "steamcmd-root": "steamcmd_root", "backup-root": "custom_backup_root"}


def settings_show(context: Any) -> CommandResult:
    """Show each location with its check, and which backup folder is in use."""
    snapshot = context.call("get_application_snapshot")
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), Mapping) else {}
    value = {"settings": settings, "diagnostics": snapshot.get("diagnostics", []),
             "portable_backup_root": snapshot.get("portable_backup_root")}
    return CommandResult(value, location_blocks(snapshot))


def location_blocks(snapshot: Mapping[str, Any]) -> list[Block]:
    """Return the Settings page's locations as a table with their checks, and the backup folder in use."""
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), Mapping) else {}
    diagnostics = {entry.get("role"): entry for entry in snapshot.get("diagnostics", []) if isinstance(entry, Mapping)}
    rows = []
    for role in ROLES:
        label = ROLE_LABELS[role]
        status, explanation = path_status(label, diagnostics.get(role))
        stored = _location(role, settings, snapshot)
        rows.append((_capital(label), stored or NOT_SET, f"{status}. {explanation}"))
    table = Table(("Setting", "Location", "Check"), tuple(rows), value_columns=frozenset({1}))
    return [table, sentence(BACKUP_CUSTOM if settings.get("custom_backup_root") else BACKUP_PORTABLE)]


def check_path(context: Any) -> CommandResult:
    """Check one folder for a setting, as the Settings page's folder choice does; nothing is saved."""
    role = CHECK_ROLES[context.options.role]
    checked = context.call("validate_settings_path_selection", role=role,
                           path=folder_path(context.options.path))
    label = ROLE_LABELS[role]
    status, explanation = path_status(label, checked)
    blocks: list[Block] = [sentence("Setting: ", _capital(label)), labelled("Location", str(checked.get("path", ""))),
                           sentence(f"Check: {status}. {explanation}")]
    resolved = checked.get("resolved_paths") if isinstance(checked.get("resolved_paths"), Mapping) else {}
    # The program and download paths that the Settings page derives from the folder
    blocks.extend(labelled(_capital(ROLE_LABELS.get(key, key)), str(path)) for key, path in resolved.items()
                  if key in ROLE_LABELS)
    return CommandResult(checked, blocks)


def _capital(label: str) -> str:
    """Start a role label with a capital letter, as a row label."""
    return label[0].upper() + label[1:]


def _location(role: str, settings: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    """Return the stored location of a role; the backup folder is the custom one or the portable default."""
    if role == "backup_root":
        return str(settings.get("custom_backup_root") or snapshot.get("portable_backup_root") or "")
    return str(settings.get(role) or "")
