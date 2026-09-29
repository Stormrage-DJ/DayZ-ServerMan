"""Unknown-preserving parser and transformer for cfgGameplay.json files."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from ..domain.configuration import ConfigurationValidationError, field_specs, validate_value
from .configuration_common import (
    ConfigurationFileError,
    ConfigurationSnapshot,
    digest_bytes,
    encode_utf8,
    newline_for,
    read_utf8,
)


def load_gameplay_configuration(path: Path) -> ConfigurationSnapshot:
    """Load a gameplay configuration file while preserving its formatting state."""
    # Read the raw bytes so later rewrites can preserve them exactly
    content, text, bom = read_utf8(path)
    # Parse the document once so values and formatting state share one tree
    data = _parse(text)
    # Snapshot the validated values with the formatting state used for rewrites
    return ConfigurationSnapshot(
        "gameplay", path, digest_bytes(content), content, _values(data),
        {
            "data": data,
            "bom": bom,
            "newline": newline_for(text),
            "indent": _indent(text),
            "trailing_newline": text.endswith(("\n", "\r")),
        },
    )


def transform_gameplay_configuration(
    snapshot: ConfigurationSnapshot,
    updates: dict[str, str | bool | int | float],
) -> bytes:
    """Rebuild the gameplay file with the requested field updates applied."""
    # Restore the parse state captured when the snapshot was created
    state = snapshot.state
    assert isinstance(state, dict) and isinstance(state["data"], dict)
    data = copy.deepcopy(state["data"])
    # Apply each requested update along its dotted path
    for path, value in updates.items():
        _deep_set(data, path, value)
    # Serialize with the original indentation, newlines, and BOM
    text = json.dumps(data, ensure_ascii=False, indent=int(state["indent"]), allow_nan=False)
    newline = str(state["newline"])
    if newline != "\n":
        text = text.replace("\n", newline)
    if state["trailing_newline"]:
        text += newline
    return encode_utf8(text, bool(state["bom"]))


def _parse(text: str) -> dict[str, Any]:
    """Parse gameplay JSON, rejecting duplicate keys and non-finite numbers."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        """Build the object mapping while rejecting duplicate keys."""
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ConfigurationFileError(f"gameplay JSON has duplicate key: {key}")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        """Reject non-finite numeric constants such as NaN."""
        raise ConfigurationFileError("gameplay JSON contains a non-finite number")

    # Parse the text with duplicate-key and finite-number protection
    try:
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        raise ConfigurationFileError("gameplay JSON is malformed") from error
    # Require the document root to be an object
    if not isinstance(data, dict):
        raise ConfigurationFileError("gameplay JSON root must be an object")
    return data


def _values(data: dict[str, Any]) -> dict[str, Any]:
    """Extract the managed field values that the document defines."""
    values: dict[str, Any] = {}
    # Collect every managed field present in the document
    for path, spec in field_specs("gameplay").items():
        present, value = _deep_get(data, path)
        if present:
            try:
                values[path] = validate_value(path, value, spec)
            except ConfigurationValidationError as error:
                raise ConfigurationFileError(f"{path} has an invalid value") from error
    return values


def _deep_get(data: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Return whether the dotted path exists and its value when present."""
    # Walk the object tree along the dotted path
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _deep_set(data: dict[str, Any], path: str, value: Any) -> None:
    """Set a value at a dotted path, creating intermediate objects as needed."""
    current = data
    parts = path.split(".")
    # Walk or create intermediate objects along the dotted path
    for part in parts[:-1]:
        existing = current.get(part)
        if existing is None:
            existing = {}
            current[part] = existing
        if not isinstance(existing, dict):
            raise ConfigurationFileError(f"{path} crosses a non-object value")
        current = existing
    # Set the final segment to the new value
    current[parts[-1]] = value


def _indent(text: str) -> int:
    """Detect the indentation width of the file, defaulting to two spaces."""
    # Detect the width of the first indented member
    match = re.search(r"(?:\r\n|\n|\r)([ \t]+)\"", text)
    if match and "\t" not in match.group(1):
        # Clamp the detected width so rewrites stay within a sane range
        return max(1, min(8, len(match.group(1))))
    # Fall back to two spaces when no indented member exists
    return 2
