"""Read and patch the exact server configuration context used by direct restore."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..domain.profiles import validate_relative_path
from .configuration_common import ConfigurationFileError, UTF8_BOM, newline_for
from .configuration_tokens import Assignment, assignments


@dataclass(frozen=True)
class RestoreConfiguration:
    """The archived mission and instance values that destination mapping must honor."""

    mission_template: str
    instance_id: int
    steam_query_port: int | None


def read_restore_configuration(content: bytes) -> RestoreConfiguration:
    """Validate exactly one active mission and positive instance/query values."""
    text, _ = _decode(content)
    items = assignments(text)
    template = _template(items)
    mission = _mission_name(template.raw)
    instance = _scalar(items, "instanceid")
    query = _scalar(items, "steamqueryport")
    return RestoreConfiguration(
        mission, 1 if instance is None else _positive(instance.raw, "instanceId"),
        None if query is None else _port(query.raw),
    )


def transform_restore_configuration(content: bytes, *, mission_template: str,
                                    instance_id: int, steam_query_port: int,
                                    display_name: str | None = None) -> bytes:
    """Patch owned settings only, preserving encoding, comments and unrelated classes."""
    # Parse first; ambiguous input must never be repaired by guessing.
    read_restore_configuration(content)
    text, bom = _decode(content)
    items = assignments(text)
    _mission_name(json.dumps(mission_template))
    _positive(str(instance_id), "instanceId")
    if isinstance(instance_id, bool) or isinstance(steam_query_port, bool):
        raise ConfigurationFileError("restore instance and query values must be integers")
    if not isinstance(instance_id, int) or not isinstance(steam_query_port, int):
        raise ConfigurationFileError("restore instance and query values must be integers")
    _port(str(steam_query_port))
    updates = {"instanceid": ("instanceId", instance_id),
               "steamqueryport": ("steamQueryPort", steam_query_port)}
    if display_name is not None:
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
            raise ConfigurationFileError("restore display name is invalid")
        if any(ord(mark) < 32 for mark in display_name):
            raise ConfigurationFileError("restore display name contains control characters")
        updates["hostname"] = ("hostname", display_name)
    template = _template(items)
    changes = [(template.start, template.end, json.dumps(mission_template, ensure_ascii=False))]
    additions = []
    for key, (spelling, value) in updates.items():
        # Append missing scalars at top level; mission template is never appended.
        existing = _scalar(items, key)
        encoded = json.dumps(value, ensure_ascii=False)
        if existing is None:
            additions.append(f"{spelling} = {encoded};")
        else:
            changes.append((existing.start, existing.end, encoded))
    for start, end, value in sorted(changes, reverse=True):
        text = text[:start] + value + text[end:]
    if additions:
        newline = newline_for(text)
        if text and not text.endswith(("\r", "\n")):
            text += newline
        text += newline.join(additions) + newline
    return (UTF8_BOM if bom else b"") + text.encode("utf-8")


def _decode(content: bytes) -> tuple[str, bool]:
    """Accept the repository's UTF-8 config encodings without changing the BOM."""
    try:
        return content.decode("utf-8-sig"), content.startswith(UTF8_BOM)
    except UnicodeError as error:
        raise ConfigurationFileError("server config is not valid UTF-8") from error


def _template(items: tuple[Assignment, ...]) -> Assignment:
    """Require one immediate mission-class template, excluding unrelated nested values."""
    matches = [item for item in items if item.key == "template"
               and len(item.context) == 2 and item.context[0] == "missions"]
    if len(matches) != 1:
        raise ConfigurationFileError("server config must contain exactly one active Missions template")
    return matches[0]


def _scalar(items: tuple[Assignment, ...], key: str) -> Assignment | None:
    """Read an unambiguous top-level owned setting."""
    matches = [item for item in items if item.key == key and not item.context]
    if len(matches) > 1:
        raise ConfigurationFileError(f"server config contains duplicate {key} assignments")
    return matches[0] if matches else None


def _mission_name(raw: str) -> str:
    """Require a quoted plain mission basename under Windows path rules."""
    try:
        value = json.loads(raw)
        normalized = validate_relative_path(value, "mission_template")
        if normalized is None or "\\" in normalized:
            raise ValueError
        return normalized
    except (ValueError, TypeError) as error:
        raise ConfigurationFileError("server config mission template is invalid") from error


def _positive(raw: str, field: str) -> int:
    """Read a positive integer while rejecting malformed and zero values."""
    if re.fullmatch(r"[+]?[0-9]+", raw) is None or int(raw) < 1:
        raise ConfigurationFileError(f"server config {field} must be a positive integer")
    return int(raw)


def _port(raw: str) -> int:
    """Validate the query port without allowing out-of-range bindings."""
    port = _positive(raw, "steamQueryPort")
    if port > 65535:
        raise ConfigurationFileError("server config steamQueryPort exceeds 65535")
    return port
