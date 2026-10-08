"""Reviews of the editing commands of phase 6 (design 8.2): text blocks shown before the question.

Configuration and tweak edits show one row per changed field: the window's label, the key to
type (an input column, criterion 18), the value now and the new value. The starter conversion
and the profile deletion show the window's dialog. JSON carries the raw preview or the request.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..application import review_wording as texts
from ..application.field_wording import SPAWNABLE_DAMAGE_LABELS, entry_name, field_label, readable_key
from .commands.common import labelled, profile_name
from .commands.config import field_value, stored_text
from .commands.tweaks import MEDICAL_STATES, STARTER_LABEL
from .edit_wording import MARKER_TAKEN_OVER, SECRET_NEW, SECRET_NOW, changes_validated
from .output import Block, Table, Value, sentence

# The headings of a review table: the label, the key to type, the value now and the new value
HEADINGS = ("Setting", "Key", "Now", "New")


def config_review(target: str, loaded: Mapping[str, Any], preview: Mapping[str, Any]) -> list[Block]:
    """Return the review of `config set`: the file, the change count and one row per changed field."""
    before = _fields(loaded)
    after = _fields(preview)
    rows = []
    for key in preview.get("changed_fields") or []:
        now, new = before.get(key, {}), after.get(key, {})
        if now.get("secret") or new.get("secret"):
            # A secret is never printed; the review says that it changes
            cells = (SECRET_NOW, SECRET_NEW)
        else:
            cells = (field_value(now) if now else stored_text(None), field_value(new) if new else stored_text(None))
        rows.append((field_label(target, str(key)), str(key), *cells))
    return [labelled("File", str(preview.get("relative_path", ""))), changes_validated(len(rows)), _table(rows)]


def tweaks_review(target: str, loaded: Mapping[str, Any], preview: Mapping[str, Any],
                  updates: Mapping[str, Any]) -> list[Block]:
    """Return the review of `tweaks set`; an event change "<event>.<field>" is one row of the key `events`."""
    values = loaded.get("values") if isinstance(loaded.get("values"), Mapping) else {}
    rows = []
    for name in preview.get("changed_fields") or []:
        if target == "events":
            event, _dot, field = str(name).partition(".")
            now = ((values.get("events") or {}).get(event) or {}).get(field)
            new = ((updates.get("events") or {}).get(event) or {}).get(field)
            rows.append((f"{entry_name(event)}: {readable_key(field)}", "events", stored_text(now), stored_text(new)))
        else:
            rows.append((tweak_label(target, str(name)), str(name), stored_text(values.get(name)),
                         stored_text(updates.get(name))))
    blocks: list[Block] = [labelled("File", str(preview.get("relative_path", ""))), changes_validated(len(rows)),
                           _table(rows)]
    if preview.get("adopts_marker_region"):
        blocks.append(sentence(MARKER_TAKEN_OVER))
    return blocks


def tweak_label(target: str, key: str) -> str:
    """Return the label of a tweak value as `tweaks show` prints it."""
    if target == "starter_loadout" and key == "items":
        return STARTER_LABEL
    if target == "spawnable_damage" and key in SPAWNABLE_DAMAGE_LABELS:
        return SPAWNABLE_DAMAGE_LABELS[key]
    return field_label(target, key)


def medical_review(feature: str, enabled: bool, state: Mapping[str, Any]) -> list[Block]:
    """Return the review of `tweaks medical set`: the file and "<feature label>: On/Off" (8.2)."""
    return [labelled("File", str(state.get("relative_path", ""))),
            sentence(f"{field_label('medical', feature)}: {MEDICAL_STATES[enabled]}")]


def conversion_review(loaded: Mapping[str, Any]) -> list[Block]:
    """Return the window's "Convert legacy starter loadout?" dialog with the recognized block (criterion 30)."""
    conversion = loaded.get("conversion") if isinstance(loaded.get("conversion"), Mapping) else {}
    lead, dash, middle, tail = texts.STARTER_CONVERSION_RANGE
    items = conversion.get("items") if isinstance(conversion.get("items"), list) else []
    return [labelled("File", str(loaded.get("relative_path", ""))), sentence(texts.STARTER_CONVERSION_TITLE),
            sentence(lead, Value(str(conversion.get("start_line"))), dash, Value(str(conversion.get("end_line"))),
                     f"{middle}{len(items)}{tail}"),
            sentence(texts.STARTER_CONVERSION_BODY)]


def profile_delete_review(profile: Mapping[str, Any]) -> list[Block]:
    """Return the window's "Delete profile?" dialog with the profile's display name (8.2)."""
    return [sentence(texts.PROFILE_DELETE_TITLE),
            sentence(texts.PROFILE_DELETE_LEAD, Value(profile_name(profile)), texts.PROFILE_DELETE_BODY)]


def _fields(view: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return the field descriptors of a configuration view by key."""
    return {str(field.get("key")): field for field in view.get("fields") or [] if isinstance(field, Mapping)}


def _table(rows: list[tuple[str, ...]]) -> Table:
    """Return the review table; the key is the input name of `--set` and the values are stored data."""
    return Table(HEADINGS, tuple(rows), input_columns=frozenset({1}), value_columns=frozenset({2, 3}))
