"""Loss-minimizing XML readers and localized patchers."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from .configuration_common import ConfigurationFileError


def validate_xml(text: str) -> ET.Element:
    """Parse mission XML strictly and reject namespace-tagged documents."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ConfigurationFileError("mission XML is malformed") from error
    # Namespaced tags cannot be patched by the localized text editors
    if any("}" in node.tag or ":" in node.tag for node in root.iter() if isinstance(node.tag, str)):
        raise ConfigurationFileError("namespaced mission XML cannot be patched safely")
    return root


def patch_named_vars(text: str, updates: dict[str, Any]) -> str:
    """Patch var element value attributes for the named mission variables."""
    root = validate_xml(text)
    names = [node.get("name") for node in root.findall("var")]
    # Apply each requested variable one at a time
    for name, value in updates.items():
        # The name must identify exactly one var element
        if names.count(name) != 1:
            raise ConfigurationFileError(f"mission variable {name} is missing or duplicated")
        pattern = re.compile(
            rf'(<var\b(?=[^>]*\bname\s*=\s*(["\']){re.escape(name)}\2)[^>]*\bvalue\s*=\s*)(["\'])([^"\']*)(\3)',
        )
        # Render booleans and loot damage values in their expected literal forms
        rendered = (f"{float(value):.2f}" if name.startswith("LootDamage")
                    else "1" if value is True else "0" if value is False else str(value))
        text, count = pattern.subn(lambda match: match.group(1) + match.group(3) + rendered + match.group(5), text)
        # A count other than one means the attribute could not be replaced safely
        if count != 1:
            raise ConfigurationFileError(f"mission variable {name} cannot be patched safely")
    return text


def patch_attributes(text: str, element: str, updates: dict[str, str]) -> str:
    """Patch attributes of the single element with the given name."""
    validate_xml(text)
    # Require exactly one opening tag so the edit target is unambiguous
    blocks = list(re.finditer(rf"<{element}\b[^>]*>", text))
    if len(blocks) != 1:
        raise ConfigurationFileError(f"{element} is missing or ambiguous")
    original = blocks[0].group(0)
    changed = original
    # Replace each named attribute once, preserving its quoting
    for name, value in updates.items():
        pattern = re.compile(rf'(\b{re.escape(name)}\s*=\s*)(["\'])([^"\']*)(\2)')
        changed, count = pattern.subn(lambda match: match.group(1) + match.group(2) + value + match.group(4), changed)
        if count != 1:
            raise ConfigurationFileError(f"{element}.{name} is missing or duplicated")
    # Splice the modified tag back into the document text
    return text[:blocks[0].start()] + changed + text[blocks[0].end():]


def patch_event_values(text: str, updates: dict[str, dict[str, int]]) -> str:
    """Patch event and population child values for the named events."""
    root = validate_xml(text)
    nodes = root.findall("event")
    names = [node.get("name") for node in nodes]
    # Apply each event update against a single matching element
    for event_name, fields in updates.items():
        if names.count(event_name) != 1:
            raise ConfigurationFileError(f"event {event_name} is missing or duplicated")
        event_pattern = re.compile(
            rf'(<event\b[^>]*\bname\s*=\s*(["\']){re.escape(event_name)}\2[^>]*>)(.*?)(</event\s*>)',
            re.DOTALL,
        )
        matches = list(event_pattern.finditer(text))
        if len(matches) != 1:
            raise ConfigurationFileError(f"event {event_name} cannot be patched safely")
        body = matches[0].group(3)
        # Replace each field value once inside the event body
        for field, value in fields.items():
            pattern = re.compile(rf'(<{re.escape(field)}>\s*)[-+]?\d+(\s*</{re.escape(field)}>)')
            body, count = pattern.subn(rf"\g<1>{value}\g<2>", body)
            if count != 1:
                raise ConfigurationFileError(f"event {event_name}.{field} is missing or duplicated")
        # Splice the patched body back between the event tags
        text = text[:matches[0].start(3)] + body + text[matches[0].end(3):]
    return text
