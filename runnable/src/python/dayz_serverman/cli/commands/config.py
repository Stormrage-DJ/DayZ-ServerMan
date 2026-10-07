"""`config show --target server/gameplay` (10.1); `config set` comes with phase 6."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...application.field_wording import field_label
from ..output import CommandResult, Table
from ..wording import missing_configuration
from .common import labelled, profile_id, profile_line, profile_name, read_data

# Words of a stored value: a secret is never printed, a missing value reads as not set
SECRET_SET = "Set (hidden)"
NOT_SET = "Not set"
BOOLEAN_TEXTS = {True: "Enabled", False: "Disabled"}


def config_show(context: Any) -> CommandResult:
    """Show each field of the server or gameplay configuration with its label, value and key."""
    target = context.options.target
    loaded = read_data(lambda: context.call("load_configuration", profile_id=profile_id(context.profile), target=target),
                       lambda: missing_configuration(target, profile_name(context.profile)))
    rows = tuple((field_label(context.options.target, str(field.get("key"))), field_value(field), str(field.get("key")))
                 for field in loaded.get("fields", []) if isinstance(field, Mapping))
    table = Table(("Setting", "Value", "Key"), rows, input_columns=frozenset({2}), value_columns=frozenset({1}))
    return CommandResult(loaded, [profile_line(context.profile),
                                  labelled("File", str(loaded.get("relative_path", ""))), table])


def field_value(field: Mapping[str, Any]) -> str:
    """Word the value of one configuration field."""
    if not field.get("present", True) or field.get("value") is None:
        return NOT_SET
    if field.get("secret"):
        return SECRET_SET
    return stored_text(field["value"])


def stored_text(value: object) -> str:
    """Word a stored value: booleans as the window's Enabled and Disabled, the rest as stored."""
    if isinstance(value, bool):
        return BOOLEAN_TEXTS[value]
    if value is None:
        return NOT_SET
    if isinstance(value, list):
        return ", ".join(str(entry) for entry in value) or NOT_SET
    return str(value)
