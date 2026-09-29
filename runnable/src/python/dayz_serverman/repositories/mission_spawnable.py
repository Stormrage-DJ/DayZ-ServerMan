"""Strict, localized cfgspawnabletypes.xml damage-cap handling."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from .configuration_common import ConfigurationFileError
from .mission_xml import validate_xml

# Number grammar for damage attributes: optional sign, decimal or exponent form
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
# Mask comments, CDATA, and damage tags so attribute text is located exactly
TOKEN = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<damage\b[^>]*>", re.DOTALL)


def read_damage_cap(text: str) -> float:
    """Return the highest min or max damage value declared in the document."""
    root = validate_xml(text)
    nodes = list(root.iter("damage"))
    tags = _damage_tags(text)
    # The parsed elements and the raw tags must agree exactly
    if not nodes or len(nodes) != len(tags):
        raise ConfigurationFileError("spawnable damage elements are missing or ambiguous")
    values: list[float] = []
    # Pair each element with its raw tag to verify both attributes once
    for node, match in zip(nodes, tags, strict=True):
        for name in ("min", "max"):
            raw = node.get(name)
            if raw is None:
                raise ConfigurationFileError(f"spawnable damage {name} is missing")
            values.append(_number(raw, f"spawnable damage {name}"))
            # An attribute written twice cannot be patched predictably
            if len(_attribute_matches(match.group(0), name)) != 1:
                raise ConfigurationFileError(f"spawnable damage {name} is ambiguous")
    # The cap is the highest bound found anywhere in the document
    return max(values)


def lower_damage_cap(text: str, maximum: float) -> str:
    """Rewrite min and max damage attributes that exceed the requested cap."""
    current = read_damage_cap(text)
    # Only a strictly lower cap is accepted so the edit always reduces damage
    if maximum >= current:
        relation = "equal to" if maximum == current else "above"
        raise ConfigurationFileError(
            f"spawnable damage cap must be below the current maximum; requested value is {relation} it"
        )
    output: list[str] = []
    cursor = 0
    # Rebuild the text tag by tag, lowering only attributes above the cap
    for match in _damage_tags(text):
        output.append(text[cursor:match.start()])
        tag = match.group(0)
        for name in ("min", "max"):
            attribute = _attribute_matches(tag, name)[0]
            value = _number(attribute.group(3), f"spawnable damage {name}")
            if value > maximum:
                # Format with enough precision to round-trip the float
                rendered = format(maximum, ".15g")
                tag = tag[:attribute.start(3)] + rendered + tag[attribute.end(3):]
        output.append(tag)
        cursor = match.end()
    output.append(text[cursor:])
    return "".join(output)


def _damage_tags(text: str) -> list[re.Match[str]]:
    """Return raw damage tags excluding comments and CDATA blocks."""
    return [match for match in TOKEN.finditer(text) if match.group(0).startswith("<damage")]


def _attribute_matches(tag: str, name: str) -> list[re.Match[str]]:
    """Return every quoted occurrence of one attribute in a raw tag."""
    return list(re.finditer(rf'(\b{re.escape(name)}\s*=\s*)(["\'])({NUMBER})(\2)', tag))


def _number(raw: str, label: str) -> float:
    """Parse a damage attribute that must be a finite zero-to-one number."""
    # Reject anything that is not a plain numeric literal
    if re.fullmatch(NUMBER, raw) is None:
        raise ConfigurationFileError(f"{label} is malformed")
    value = float(raw)
    # Damage fractions are bounded to the zero-to-one range
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ConfigurationFileError(f"{label} must be from 0.0 through 1.0")
    return value
