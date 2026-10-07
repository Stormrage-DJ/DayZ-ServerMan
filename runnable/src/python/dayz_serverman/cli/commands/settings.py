"""`settings show` (10.1); `settings check-path` and `settings set` come with phase 7."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.activity_wording import ROLE_LABELS
from ..output import CommandResult, Line, Table, sentence
from ..read_wording import path_status

# The locations of the Settings page, in its order
ROLES = ("dayz_root", "dayz_executable", "steamcmd_root", "steamcmd_executable", "workshop_content_root",
         "backup_root")
NOT_SET = "Not set"


def settings_show(context: Any) -> CommandResult:
    """Show each location with its check, and which backup folder is in use."""
    snapshot = context.call("get_application_snapshot")
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), Mapping) else {}
    diagnostics = {entry.get("role"): entry for entry in snapshot.get("diagnostics", []) if isinstance(entry, Mapping)}
    rows = []
    for role in ROLES:
        label = ROLE_LABELS[role]
        status, explanation = path_status(label, diagnostics.get(role))
        stored = _location(role, settings, snapshot)
        rows.append((label[0].upper() + label[1:], stored or NOT_SET, f"{status}. {explanation}"))
    table = Table(("Setting", "Location", "Check"), tuple(rows), value_columns=frozenset({1}))
    custom = settings.get("custom_backup_root")
    blocks: list[Line | Table] = [table, sentence(
        "Backups go to the custom backup folder." if custom
        else "Backups go to the portable default folder, which moves with the DayZ-ServerMan folder.")]
    value = {"settings": settings, "diagnostics": snapshot.get("diagnostics", []),
             "portable_backup_root": snapshot.get("portable_backup_root")}
    return CommandResult(value, blocks)


def _location(role: str, settings: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    """Return the stored location of a role; the backup folder is the custom one or the portable default."""
    if role == "backup_root":
        return str(settings.get("custom_backup_root") or snapshot.get("portable_backup_root") or "")
    return str(settings.get(role) or "")
