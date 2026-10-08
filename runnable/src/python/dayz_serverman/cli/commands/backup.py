"""`backup list [--all]` (10.1); the writes `backup create`, `restore` and `recover` are in `backup_write.py`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.activity_wording import plain_sentence
from ..output import CommandResult, Line, Table, sentence
from ..read_wording import BACKUP_RESTORE_REASONS, size_text
from .common import local_time, profile_id, profile_line

# Text of a backup that the restore of this version accepts, and of the backup folder kinds
RESTORABLE = "Can be restored"
FOLDER_TEXTS = {"default": "the portable default folder", "custom": "the custom backup folder"}


def backup_list(context: Any) -> CommandResult:
    """List the backups of the profile, or with `--all` every backup in the backup folder."""
    if context.options.all:
        catalog = context.call("list_backup_catalog")
        rows = tuple((str(entry.get("display_name") or entry.get("profile_id") or ""), *backup_cells(entry))
                     for entry in _entries(catalog))
        table = Table(("Profile", "Created", "Files", "Size", "Restore", "ID"), rows,
                      input_columns=frozenset({5}), value_columns=frozenset({0, 1, 2}))
        return CommandResult(catalog, _listing(catalog, table, None))
    history = context.call("list_backups", profile_id=profile_id(context.profile))
    table = Table(("Created", "Files", "Size", "Restore", "ID"),
                  tuple(backup_cells(entry) for entry in _entries(history)),
                  input_columns=frozenset({4}), value_columns=frozenset({0, 1}))
    return CommandResult(history, _listing(history, table, profile_line(context.profile)))


def _entries(listing: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the backup entries of a listing."""
    return [entry for entry in listing.get("backups", []) if isinstance(entry, Mapping)]


def backup_cells(entry: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    """Return created, files, size, restore and ID of one backup."""
    return (local_time(entry.get("created_at")) or "Unknown date", str(entry.get("entry_count", "")),
            size_text(entry.get("total_size")), _restore_text(entry), str(entry.get("backup_id")))


def _restore_text(entry: Mapping[str, Any]) -> str:
    """Word whether a backup can be restored, with the window's reason when it cannot."""
    compatibility = entry.get("restore_compatibility")
    if compatibility == "COMPATIBLE":
        return RESTORABLE
    return BACKUP_RESTORE_REASONS.get(str(compatibility)) or plain_sentence(
        entry.get("restore_compatibility_reason")) or "Cannot be restored"


def _listing(listing: Mapping[str, Any], table: Table, first: Line | None) -> list[Line | Table]:
    """Return the text: the profile, the folder, the table or the empty line, and each diagnostic."""
    blocks: list[Line | Table] = [] if first is None else [first]
    folder = FOLDER_TEXTS.get(str(listing.get("destination_kind")), "the backup folder")
    blocks.append(sentence(f"Backups in {folder}."))
    blocks.append(table if table.rows else sentence("No backups yet."))
    for diagnostic in listing.get("diagnostics", []):
        text = plain_sentence(diagnostic.get("message")) if isinstance(diagnostic, Mapping) else None
        blocks.append(sentence(text or "A backup could not be read."))
    return blocks
