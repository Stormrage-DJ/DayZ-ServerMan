"""Mission configuration file formats with localized transformations."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .configuration_common import ConfigurationFileError, digest_bytes, encode_utf8, read_utf8
from .mission_xml import patch_attributes, patch_event_values, patch_named_vars, validate_xml
from .mission_spawnable import lower_damage_cap, read_damage_cap
from .mission_starter import (
    convert_legacy_starter, legacy_starter_region, patch_starter, read_starter,
)
from ..domain.mission_configuration import (
    EVENT_FIELDS, EVENT_NAMES, GLOBAL_BOOLEAN_KEYS, GLOBAL_INTEGER_KEYS,
    GLOBAL_KEYS, LOOT_DAMAGE_KEYS, POPULATION_FIELDS, POPULATION_NAMES,
)

# In-mission path of each supported configuration target
TARGET_PATHS = {
    "economy": Path("db/globals.xml"),
    "weather": Path("cfgweather.xml"),
    "spawnable_damage": Path("cfgspawnabletypes.xml"),
    "starter_loadout": Path("init.c"),
    "events": Path("db/events.xml"),
}


def load_mission_file(path: Path, target: str) -> dict[str, Any]:
    """Load one mission file into a snapshot of text, digest, and values."""
    content, text, bom = read_utf8(path)
    _validate(target, text)
    # Capture the raw payload, decoded text, and managed values together
    snapshot = {"content": content, "text": text, "bom": bom,
                "digest": digest_bytes(content), "values": _read_values(target, text)}
    # Starter loadouts additionally report legacy markers and adoption needs
    if target == "starter_loadout":
        legacy = legacy_starter_region(text)
        _, snapshot["adoption_required"] = read_starter(text)
        snapshot["conversion_required"] = legacy is not None
        # Record the legacy region so callers can offer conversion
        if legacy is not None:
            snapshot["conversion"] = {
                "items": list(legacy.items), "start_line": legacy.start_line,
                "end_line": legacy.end_line,
            }
    return snapshot


def transform_mission_file(snapshot: dict[str, Any], target: str, updates: dict[str, Any]) -> bytes:
    """Apply updates to a snapshot and return the re-encoded file bytes."""
    text = snapshot["text"]
    # Route to the patcher that owns the selected target format
    if target == "economy":
        text = patch_named_vars(text, updates)
    elif target == "weather":
        text = _weather(text, updates)
    elif target == "spawnable_damage":
        text = lower_damage_cap(text, updates["maximum"])
    elif target == "starter_loadout":
        text = patch_starter(text, updates["items"])
    elif target == "events":
        text = patch_event_values(text, updates["events"])
    else:
        raise ConfigurationFileError("mission target is not supported")
    # Re-encode with the byte order mark the source file carried
    return encode_utf8(text, snapshot["bom"])


def convert_starter_file(snapshot: dict[str, Any]) -> bytes:
    """Convert a legacy starter loadout snapshot into marked managed form."""
    return encode_utf8(convert_legacy_starter(snapshot["text"]), snapshot["bom"])


def _validate(target: str, text: str) -> None:
    """Reject malformed content for the selected target before it is read."""
    # XML targets share one strict validation gate
    if target in {"economy", "weather", "spawnable_damage", "events"}:
        validate_xml(text)
    elif target == "starter_loadout":
        # The starter reader validates its own script format
        read_starter(text)


def _read_values(target: str, text: str) -> dict[str, Any]:
    """Read the managed values of the selected target into a plain mapping."""
    if target == "economy":
        # Economy values come from one var element per managed key
        root = validate_xml(text)
        result: dict[str, Any] = {}
        for node in root.findall("var"):
            name = node.get("name")
            if name in GLOBAL_KEYS:
                # A duplicated variable would make the read value ambiguous
                if name in result:
                    raise ConfigurationFileError(f"mission variable {name} is duplicated")
                raw = node.get("value")
                # Each key family is parsed with its own strict rule
                try:
                    if name in LOOT_DAMAGE_KEYS:
                        result[name] = _strict_number(raw, name)
                    elif name in GLOBAL_BOOLEAN_KEYS:
                        if raw not in ("0", "1"):
                            raise ValueError
                        result[name] = raw == "1"
                    elif name in GLOBAL_INTEGER_KEYS:
                        result[name] = _strict_integer(raw, name)
                except ValueError as error:
                    raise ConfigurationFileError(f"mission variable {name} is malformed") from error
        # Every managed economy key must be present and parsed
        if set(result) != GLOBAL_KEYS:
            raise ConfigurationFileError("managed economy variables are missing")
        return result
    if target == "events":
        # Event values cover both event fields and population overrides
        root = validate_xml(text)
        result: dict[str, Any] = {"events": {}}
        for node in root.findall("event"):
            name = node.get("name") or ""
            if name not in EVENT_NAMES | POPULATION_NAMES:
                continue
            fields = EVENT_FIELDS if name in EVENT_NAMES else POPULATION_FIELDS
            # A duplicated event name cannot be represented safely
            if name in result["events"]:
                raise ConfigurationFileError(f"event {name} is duplicated")
            values = {}
            for field in fields:
                elements = node.findall(field)
                if len(elements) == 1 and elements[0].text is not None:
                    try:
                        values[field] = int(elements[0].text)
                    except ValueError as error:
                        raise ConfigurationFileError(f"event {name}.{field} is malformed") from error
                elif len(elements) > 1:
                    raise ConfigurationFileError(f"event {name}.{field} is duplicated")
            # Only events with a complete managed field set are reported
            if set(values) != fields:
                raise ConfigurationFileError(f"event {name} is missing managed values")
            if values:
                result["events"][name] = values
        return result
    if target == "starter_loadout":
        # Starter snapshots report the parsed item list only
        items, _adoption = read_starter(text)
        return {"items": items}
    if target == "spawnable_damage":
        # Damage targets report their current maximum cap
        return {"maximum": read_damage_cap(text)}
    if target == "weather":
        # Weather values are decoded from the single rain block
        root = validate_xml(text)
        rain = root.find("rain")
        if rain is None:
            raise ConfigurationFileError("rain configuration is missing")
        limits, times = rain.find("limits"), rain.find("timelimits")
        changes, thresholds = rain.find("changelimits"), rain.find("thresholds")
        # All four rain sub-blocks must exist before values are read
        if limits is None or times is None or changes is None or thresholds is None:
            raise ConfigurationFileError("rain configuration is incomplete")
        # Bounds must be ordered and stay within the zero-to-one range
        try:
            if root.get("enable") not in ("0", "1") or root.get("reset") not in ("0", "1"):
                raise ConfigurationFileError("weather enable and reset must be zero or one")
            limit_min = _strict_number(limits.get("min"), "rain limits min")
            limit_max = _strict_number(limits.get("max"), "rain limits max")
            change_min = _strict_number(changes.get("min"), "rain change min")
            change_max = _strict_number(changes.get("max"), "rain change max")
            if limit_min > limit_max or change_min > change_max:
                raise ConfigurationFileError("rain minimum must not exceed maximum")
            return {"rain_disabled": root.get("enable") == "1" and limit_max == 0.0 and change_max == 0.0,
                    "rain_time_min": _strict_integer(times.get("min"), "rain time min"),
                    "rain_time_max": _strict_integer(times.get("max"), "rain time max"),
                    "rain_fade": _strict_integer(thresholds.get("end"), "rain fade")}
        except ValueError as error:
            raise ConfigurationFileError("rain configuration is malformed") from error
    return {}


def _weather(text: str, updates: dict[str, Any]) -> str:
    """Patch the single rain block in a weather document and return the text."""
    matches = list(re.finditer(r"<rain\b[^>]*>.*?</rain\s*>", text, re.DOTALL))
    # Require exactly one rain block so the edit stays localized
    if len(matches) != 1:
        raise ConfigurationFileError("rain configuration is missing or ambiguous")
    rain = matches[0].group(0)
    # The rain toggle rewrites enable, reset, and both limit pairs together
    if "rain_disabled" in updates:
        disabled = updates["rain_disabled"]
        text = patch_attributes(text, "weather", {"enable": "1" if disabled else "0", "reset": "1" if disabled else "0"})
        rain = patch_attributes(rain, "limits", {"min": "0.0", "max": "0.0" if disabled else "1.0"})
        rain = patch_attributes(rain, "changelimits", {"min": "0.0", "max": "0.0" if disabled else "1.0"})
    # Time and fade updates map to their target attribute pairs
    mapping = {"rain_time_min": ("timelimits", "min"), "rain_time_max": ("timelimits", "max"), "rain_fade": ("thresholds", "end")}
    for key, (element, attribute) in mapping.items():
        if key in updates:
            rain = patch_attributes(rain, element, {attribute: str(updates[key])})
    # Splice the patched block back into the original document
    matches = list(re.finditer(r"<rain\b[^>]*>.*?</rain\s*>", text, re.DOTALL))
    return text[:matches[0].start()] + rain + text[matches[0].end():]


def _strict_integer(raw: str | None, label: str) -> int:
    """Parse a non-negative integer that fits the game integer range."""
    # Accept only plain digits with an optional leading plus
    if raw is None or re.fullmatch(r"[+]?[0-9]+", raw) is None:
        raise ConfigurationFileError(f"{label} must be a non-negative integer")
    value = int(raw)
    # Signed 32-bit is the largest value the game stores safely
    if value > 2_147_483_647:
        raise ConfigurationFileError(f"{label} exceeds the supported integer range")
    return value


def _strict_number(raw: str | None, label: str) -> float:
    """Parse a finite number that must lie between zero and one."""
    # Accept decimal and exponent forms with an optional leading plus
    if raw is None or re.fullmatch(r"[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw) is None:
        raise ConfigurationFileError(f"{label} must be a non-negative number")
    value = float(raw)
    # Configuration values are fractions, so enforce the zero-to-one bound
    if not 0.0 <= value <= 1.0:
        raise ConfigurationFileError(f"{label} must be from 0.0 through 1.0")
    return value
