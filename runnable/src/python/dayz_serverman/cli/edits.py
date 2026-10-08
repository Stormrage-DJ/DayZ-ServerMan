"""`--set key=value` typing and `--from-file` shapes of the editing commands (design 10.6).

The CLI only turns text into JSON values: by the field kind of a configuration, by the JSON type
of a tweak's or a profile's current value, or by the fixed table of `profile create`. The bridge
validates the values and refuses (exit 2 for INVALID_REQUEST). Every refusal here is an argument
error of the read-only pre-step, before the instance lock (6.4.1): exit 2, nothing changed.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import read_text_shared
from .edit_wording import (
    EXPECTED_ARRAY, EXPECTED_BOOLEAN, EXPECTED_INTEGER, EXPECTED_NUMBER, EXPECTED_OBJECT, invalid_value, no_changes,
)
from .exit_codes import USAGE
from .output import CliFailure
from .wording import duplicate_key, invalid_file, invalid_set, unknown_key

# The words of a boolean value, without regard to case (10.6)
BOOLEAN_WORDS = {"true": True, "on": True, "yes": True, "1": True, "false": False, "off": False, "no": False,
                 "0": False}
# A whole number in base 10
INTEGER = re.compile(r"[+-]?[0-9]+")


def read_object(path: str | None) -> dict[str, Any] | None:
    """Read a `--from-file` file: UTF-8 JSON whose top level is an object; None without the option."""
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


def checked_sets(values: list[str] | None, known: Iterable[Any], show_command: str) -> list[tuple[str, str]]:
    """Split each `--set` at its first `=`; refuse an unknown key, a key given twice and a missing `=`."""
    names = set(known)
    pairs: list[tuple[str, str]] = []
    for value in values or []:
        key, separator, text = value.partition("=")
        if not separator or not key:
            raise CliFailure("USAGE", invalid_set(value), USAGE)
        if key not in names:
            raise CliFailure("USAGE", unknown_key(key, show_command), USAGE)
        if any(key == seen for seen, _text in pairs):
            raise CliFailure("USAGE", duplicate_key(key), USAGE)
        pairs.append((key, text))
    return pairs


def check_file_keys(document: Mapping[str, Any] | None, known: Iterable[Any], show_command: str) -> None:
    """Refuse a `--from-file` object with a key that the loaded definitions do not hold."""
    names = set(known)
    for key in document or {}:
        if key not in names:
            raise CliFailure("USAGE", unknown_key(str(key), show_command), USAGE)


def require_changes(updates: Mapping[str, Any]) -> None:
    """Refuse an edit that names no change at all."""
    if not updates:
        raise CliFailure("USAGE", no_changes(), USAGE)


def kind_value(kind: object, key: str, text: str) -> Any:
    """Type a configuration value by its field kind: boolean, integer, number; other kinds as text."""
    if kind == "boolean":
        return boolean(key, text)
    if kind == "integer":
        return integer(key, text)
    if kind == "number":
        return number(key, text)
    return text


def like_current(current: Any, key: str, text: str) -> Any:
    """Type a value by the JSON type of the current one; without one, a JSON literal, else text."""
    if isinstance(current, bool):
        return boolean(key, text)
    if isinstance(current, int):
        return integer(key, text)
    if isinstance(current, float):
        return number(key, text)
    if isinstance(current, list):
        return json_of(list, key, text, EXPECTED_ARRAY)
    if isinstance(current, dict):
        return json_of(dict, key, text, EXPECTED_OBJECT)
    if isinstance(current, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def boolean(key: str, text: str) -> bool:
    """Return true/false, on/off, yes/no or 1/0 as a boolean."""
    value = BOOLEAN_WORDS.get(text.strip().casefold())
    if value is None:
        raise CliFailure("USAGE", invalid_value(key, text, EXPECTED_BOOLEAN), USAGE)
    return value


def integer(key: str, text: str) -> int:
    """Return a whole number written in base 10."""
    if INTEGER.fullmatch(text.strip()) is None:
        raise CliFailure("USAGE", invalid_value(key, text, EXPECTED_INTEGER), USAGE)
    return int(text.strip(), 10)


def number(key: str, text: str) -> int | float:
    """Return a finite number as JSON writes it: a whole number stays whole, as the window sends it."""
    try:
        value = json.loads(text.strip())
    except ValueError:
        value = None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CliFailure("USAGE", invalid_value(key, text, EXPECTED_NUMBER), USAGE)
    return value


def json_of(kind: type, key: str, text: str, expected: str) -> Any:
    """Return the JSON value of the text when it has the expected type (an array or an object)."""
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not isinstance(value, kind):
        raise CliFailure("USAGE", invalid_value(key, text, expected), USAGE)
    return value
