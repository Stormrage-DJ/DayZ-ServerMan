"""`tweaks show --target …` and `tweaks medical show` (10.1); the tweak writes come with phase 6."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.field_wording import SPAWNABLE_DAMAGE_LABELS, entry_name, field_label, readable_key
from ..output import CommandResult, Line, Table, sentence
from ..wording import missing_mission
from .common import labelled, profile_id, profile_line, profile_name, read_data
from .config import stored_text

# Label of the starter item list, and of a medical setting's state
STARTER_LABEL = "Starter items"
MEDICAL_STATES = {True: "On", False: "Off"}


def tweaks_show(context: Any) -> CommandResult:
    """Show the values of one mission target with their labels and keys."""
    target = context.options.target.replace("-", "_")
    loaded = read_data(lambda: context.call("load_mission_configuration", profile_id=profile_id(context.profile),
                                            target=target),
                       lambda: missing_mission(profile_name(context.profile)))
    values = loaded.get("values") if isinstance(loaded.get("values"), Mapping) else {}
    blocks: list[Line | Table] = [profile_line(context.profile), labelled("File", str(loaded.get("relative_path", "")))]
    if target == "events" and isinstance(values.get("events"), Mapping):
        blocks.append(_event_table(values["events"]))
    elif target == "starter_loadout":
        blocks.append(_settings_table(((STARTER_LABEL, stored_text(values.get("items")), "items"),)))
    else:
        labels = SPAWNABLE_DAMAGE_LABELS if target == "spawnable_damage" else None
        blocks.append(_settings_table(tuple(
            ((labels or {}).get(key) or field_label(target, key), stored_text(value), key)
            for key, value in values.items())))
    if loaded.get("adoption_required"):
        blocks.append(sentence("This file is not in the form that DayZ-ServerMan edits yet."))
    return CommandResult(loaded, blocks)


def _settings_table(rows: tuple[tuple[str, str, str], ...]) -> Table:
    """Return a Setting, Value, Key table; the key is the input name of `tweaks set --set`."""
    return Table(("Setting", "Value", "Key"), rows, input_columns=frozenset({2}), value_columns=frozenset({1}))


def _event_table(events: Mapping[str, Any]) -> Table:
    """Return the event and population table with the window's column words."""
    fields: list[str] = []
    for values in events.values():
        fields.extend(key for key in (values if isinstance(values, Mapping) else {}) if key not in fields)
    rows = tuple((entry_name(name), *(stored_text((values or {}).get(key)) if key in (values or {}) else ""
                                       for key in fields))
                 for name, values in events.items())
    return Table(("Event", *(readable_key(key) for key in fields)), rows,
                 value_columns=frozenset(range(len(fields) + 1)))


def medical_show(context: Any) -> CommandResult:
    """Show each medical loot setting with its label, its state and the feature name to type."""
    loaded = read_data(lambda: context.call("load_medical_features", profile_id=profile_id(context.profile)),
                       lambda: missing_mission(profile_name(context.profile)))
    features = loaded.get("features") if isinstance(loaded.get("features"), Mapping) else {}
    rows = tuple((field_label("medical", name), MEDICAL_STATES[bool((state or {}).get("enabled"))], name)
                 for name, state in features.items())
    table = Table(("Setting", "State", "Feature"), rows, input_columns=frozenset({2}))
    return CommandResult(loaded, [profile_line(context.profile), table])
