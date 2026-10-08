"""The legacy import review of `migrate preview` and `migrate apply` (8.2, 10.4): the window's items and dialog.

The Settings page's legacy import (`frontend/migration.js`) lists each item of the preview with a
title, a summary, its warnings and its conflicts, then confirms with "Import selected legacy data?".
The CLI shows the same content as a table of the items with their IDs (an input column, criterion
18), the summaries, and the dialog. Host warnings are shown only as operator sentences.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..application.activity_wording import ROLE_LABELS, plain_sentence
from ..application.review_wording import (
    MIGRATION_BACKUP_SUMMARY, MIGRATION_BACKUP_TITLE, MIGRATION_BLOCKED_SUMMARY, MIGRATION_BLOCKED_TITLE,
    MIGRATION_DIALOG, MIGRATION_DIALOG_BODY, MIGRATION_NEEDS_REVIEW, MIGRATION_SETTINGS_TITLE,
    MIGRATION_SETTINGS_UNCHANGED, MIGRATION_SOURCE_NOTE,
)
from .output import Block, Echo, Line, Table, TypeText, Value, sentence

# A host conflict, and a host warning, that holds something other than operator words (for example a field name)
REVIEW_FALLBACK = "This item needs a closer look."
WARNING_FALLBACK = "A legacy value was adjusted for the import."
# Operator words of the legacy argument fields (`domain/legacy_arguments.KNOWN_OPTIONS`), after the profile
# form's labels; a host warning or conflict names the field as its last word (QF-72)
LEGACY_FIELD_LABELS = {
    "config_path": "the server config", "game_port": "the game port", "profiles_path": "the runtime directory",
    "mission_path": "the mission root", "mods": "the client mods", "server_mods": "the server mods",
}
_LEGACY_FIELD = re.compile(
    r"(?<= )(" + "|".join(sorted(LEGACY_FIELD_LABELS, key=len, reverse=True)) + r")(?=\.?$)")
# Row label of a profile item, before the profile's name
PROFILE_ITEM = "Legacy profile"
# The kinds of the import result, as the review names them
IMPORTED_KINDS = {"SETTINGS": MIGRATION_SETTINGS_TITLE, "PROFILE": PROFILE_ITEM,
                  "LEGACY_BACKUP_INDEX": MIGRATION_BACKUP_TITLE}


def items(preview: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the preview's items in the window's order: settings, each profile, the backup references."""
    found = [preview.get("settings"), *(preview.get("profiles") or []), preview.get("backup_inventory")]
    return [item for item in found if isinstance(item, Mapping)]


def selectable_ids(preview: Mapping[str, Any]) -> list[str]:
    """Return the IDs of the items that the window selects by default: every conflict-free one."""
    return [str(item.get("item_id")) for item in items(preview) if item.get("selectable") is True]


def review_blocks(preview: Mapping[str, Any], selected: Sequence[str] | None = None) -> list[Block]:
    """Return the items table, each item's summary with its warnings, and the window's source note.

    With `selected` (`migrate apply`) the last column says what the import takes; without it
    (`migrate preview`) it says what can be imported.
    """
    rows = []
    for item in items(preview):
        title, name = _title(preview, item)
        chosen = item.get("selectable") is True if selected is None else item.get("item_id") in selected
        rows.append((title, name, str(item.get("item_id", "")), "Yes" if chosen else "No"))
    heading = "Can import" if selected is None else "Import"
    blocks: list[Block] = [sentence("Legacy folder: ", Value(str(preview.get("source_label", "")))),
                           Table(("Item", "Name", "ID", heading), tuple(rows), input_columns=frozenset({2}),
                                 value_columns=frozenset({1}))]
    for item in items(preview):
        blocks.append(_summary(preview, item))
        warnings = [_plain(text, WARNING_FALLBACK) for text in item.get("warnings") or []]
        conflicts = [MIGRATION_NEEDS_REVIEW + _plain(conflict.get("message"), REVIEW_FALLBACK)
                     for conflict in item.get("conflicts") or [] if isinstance(conflict, Mapping)]
        # A sentence that several host messages share is shown once
        blocks.extend(sentence(text) for text in dict.fromkeys((*warnings, *conflicts)))
    blocks.append(sentence(MIGRATION_SOURCE_NOTE))
    return blocks


def dialog_blocks(count: int) -> list[Line]:
    """Return the window's "Import selected legacy data?" dialog for `count` selected items."""
    return [sentence(MIGRATION_DIALOG), sentence(MIGRATION_DIALOG_BODY.format(count=count))]


def imported_blocks(result: Mapping[str, Any]) -> list[Block]:
    """Return what the import published: one row per kind, a profile with its new ID (an input column)."""
    rows = tuple((IMPORTED_KINDS.get(str(entry.get("kind")), "Other data"), str(entry.get("profile_id") or ""))
                 for entry in result.get("published") or [] if isinstance(entry, Mapping)
                 and entry.get("kind") in IMPORTED_KINDS)
    return [Table(("Imported", "ID"), rows, input_columns=frozenset({1}))] if rows else []


def unknown_item(item_id: str) -> Line:
    """Word an `--item` that the preview of this folder does not list (exit 2)."""
    return sentence("Unknown item ", Echo(item_id), ". Run ", TypeText("migrate preview"), " for the items.")


def blocked(preview: Mapping[str, Any], item_ids: Sequence[str] | None = None) -> list[str]:
    """Return the IDs of the items that a conflict with the current data blocks ("Needs review"), in order.

    With `item_ids`, only those of the named items.
    """
    return [str(item.get("item_id")) for item in items(preview) if item.get("selectable") is not True
            and item.get("conflicts") and (item_ids is None or str(item.get("item_id")) in item_ids)]


def all_items_blocked() -> Line:
    """Word a legacy folder whose every importable item is blocked by the current data (exit 3, criterion 32)."""
    return sentence("Nothing was imported. The legacy data of this folder conflicts with the current "
                    "DayZ-ServerMan data. Run ", TypeText("migrate preview"), " for the reason of each item, "
                    "and resolve the conflicts in the current data first.")


def named_items_blocked(item_ids: Sequence[str]) -> Line:
    """Word named `--item`s that the current data blocks (exit 3, criterion 32); each ID is an echo."""
    names: list[Any] = []
    for index, item_id in enumerate(item_ids):
        names.extend(([", "] if index else []) + [Echo(item_id)])
    subject = ("The item ", *names, " conflicts") if len(item_ids) == 1 else ("The items ", *names, " conflict")
    return sentence("Nothing was imported. ", *subject, " with the current DayZ-ServerMan data. Run ",
                    TypeText("migrate preview"), " for the reason, and resolve the conflict in the current "
                    "data first.")


def item_without_data(item_id: str) -> Line:
    """Word a named `--item` that the preview lists with nothing to import now (exit 1, criterion 32)."""
    return sentence("Nothing was imported. The item ", Echo(item_id), " has nothing to import now. Run ",
                    TypeText("migrate preview"), " for the reason.")


def nothing_to_import() -> Line:
    """Word a legacy folder whose preview has no item that can be imported (exit 1, criterion 27)."""
    return sentence("This legacy folder has nothing that can be imported now. Run ", TypeText("migrate preview"),
                    " for the reasons.")


def _title(preview: Mapping[str, Any], item: Mapping[str, Any]) -> tuple[str, str]:
    """Return an item's row label and, for a profile, its display name as stored data."""
    if item is preview.get("settings"):
        return MIGRATION_SETTINGS_TITLE, ""
    if item is preview.get("backup_inventory"):
        return MIGRATION_BACKUP_TITLE, ""
    profile = item.get("profile")
    if isinstance(profile, Mapping):
        return PROFILE_ITEM, str(profile.get("display_name", ""))
    return MIGRATION_BLOCKED_TITLE, ""


def _summary(preview: Mapping[str, Any], item: Mapping[str, Any]) -> Line:
    """Return the window's summary line of one item."""
    if item is preview.get("settings"):
        roles = [ROLE_LABELS.get(str(field.get("role")), "location") for field in item.get("fields") or []
                 if isinstance(field, Mapping)]
        text = f"Set the {' and the '.join(roles)}." if roles else MIGRATION_SETTINGS_UNCHANGED
        return sentence(f"{MIGRATION_SETTINGS_TITLE}: {text}")
    if item is preview.get("backup_inventory"):
        summary = MIGRATION_BACKUP_SUMMARY.format(count=item.get("count", 0), size=item.get("size", 0))
        return sentence(f"{MIGRATION_BACKUP_TITLE}: {summary}")
    profile = item.get("profile")
    if not isinstance(profile, Mapping):
        return sentence(f"{MIGRATION_BLOCKED_TITLE}: {MIGRATION_BLOCKED_SUMMARY}")
    mods = profile.get("mods") if isinstance(profile.get("mods"), list) else []
    return sentence(Value(str(profile.get("display_name", ""))), f": {len(mods)} ordered mods; runtime profile ",
                    Value(str(profile.get("runtime_profile") or "")), ".")


def _plain(message: object, fallback: str) -> str:
    """Return a host warning or conflict as an operator sentence, else the general one.

    A legacy argument field at the end of the text becomes its operator words first, so every
    field reads the same way and none is shown by its field name (11.3).
    """
    text = _LEGACY_FIELD.sub(lambda match: LEGACY_FIELD_LABELS[match.group(1)], str(message or "").strip())
    return plain_sentence(text) or fallback
