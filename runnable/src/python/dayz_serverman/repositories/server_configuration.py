"""Loss-minimizing parser and transformer for serverDZ.cfg-style files."""

from __future__ import annotations

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


def load_server_configuration(path: Path) -> ConfigurationSnapshot:
    """Read a serverDZ.cfg file and return its loss-minimizing snapshot."""
    # Keep raw bytes and text form so a rewrite can preserve both
    content, text, bom = read_utf8(path)
    return ConfigurationSnapshot(
        "server", path, digest_bytes(content), content, _parse(text),
        {"text": text, "bom": bom, "newline": newline_for(text)},
    )


def transform_server_configuration(
    snapshot: ConfigurationSnapshot,
    updates: dict[str, str | bool | int | float],
) -> bytes:
    """Apply key updates to a snapshot and return the re-encoded bytes."""
    state = snapshot.state
    assert isinstance(state, dict)
    text = str(state["text"])
    newline = str(state["newline"])
    # Rewrite each existing assignment in place to preserve surrounding text
    for key, value in updates.items():
        encoded = _encode(value)
        pattern = _pattern(key)
        if pattern.search(text):
            # Keep the matched prefix and suffix around the new value
            text = pattern.sub(
                lambda match: f"{match.group('prefix')}{encoded}{match.group('suffix')}",
                text,
                count=1,
            )
        else:
            # Terminate the last line before appending a missing assignment
            if text and not text.endswith(("\n", "\r")):
                text += newline
            text += f"{key} = {encoded};{newline}"
    # Re-encode with the original byte-order-mark choice
    return encode_utf8(text, bool(state["bom"]))


def _parse(text: str) -> dict[str, Any]:
    """Extract the managed fields from raw serverDZ.cfg text."""
    values: dict[str, Any] = {}
    for key, spec in field_specs("server").items():
        assignment = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=", re.I | re.M)
        matches = list(_pattern(key).finditer(text))
        # Cross-check assignment counts so malformed or duplicate keys are rejected
        if len(list(assignment.finditer(text))) != len(matches):
            raise ConfigurationFileError(f"{key} has a malformed assignment")
        # A managed key may appear at most once
        if len(matches) > 1:
            raise ConfigurationFileError(f"{key} has duplicate assignments")
        if matches:
            values[key] = _parse_value(key, matches[0].group("value"), spec.kind)
    return values


def _pattern(key: str) -> re.Pattern[str]:
    """Build the regex that matches one key's assignment on a single line."""
    # Keep the leading text and trailing suffix so rewrites are loss-minimizing
    return re.compile(
        rf"^(?P<prefix>[ \t]*{re.escape(key)}[ \t]*=[ \t]*)"
        rf'(?P<value>"(?:\\.|[^"\\\r\n])*"|[^;\r\n]*)'
        rf"(?P<suffix>;[^\r\n]*)(?=\r?$)",
        re.I | re.M,
    )


def _parse_value(key: str, raw: str, kind: str) -> Any:
    """Convert raw assignment text into a validated typed value."""
    value = raw.strip()
    try:
        if kind == "string":
            # Strings must be JSON-quoted so escapes round-trip exactly
            if len(value) < 2 or value[0] != '"' or value[-1] != '"':
                raise ValueError
            parsed = json.loads(value)
        else:
            # Numeric kinds accept plain integers only
            if re.fullmatch(r"[+-]?[0-9]+", value) is None:
                raise ValueError
            parsed = int(value)
            if kind == "boolean":
                if parsed not in (0, 1):
                    raise ValueError
                parsed = bool(parsed)
        return validate_value(key, parsed, field_specs("server")[key])
    except (ValueError, json.JSONDecodeError, ConfigurationValidationError) as error:
        raise ConfigurationFileError(f"{key} has an invalid value") from error


def _encode(value: object) -> str:
    """Render one Python value in serverDZ.cfg assignment syntax."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)
