"""Pure candidate builder of the editor territory in `env/zombie_territories.xml` (leaf 7.5, A8, D8).

The editor owns one territory element between two reserved marker comments.
The candidate is the working baseline with that element inserted before the
root end tag, so every original byte stays as it is. The owned-value
projection is the marked block. Preview caps come from the event `nominal`
and `max` values in `db/events.xml` and `ZombieMaxCount` in `db/globals.xml`.
This module reads and writes no file; the caller supplies the bytes.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from xml.etree.ElementTree import Element

from ..domain.mission_map_objects import parse_object
from ..domain.mission_map_providers import offered_infected_events
from ..domain.mission_map_values import ObjectRuleError, Provider
from .configuration_common import UTF8_BOM, ConfigurationFileError
from .mission_xml import validate_xml

# Reserved marker comments directly before and after the editor territory element (D8)
MARKER_BEGIN = b"<!-- dayz-serverman mission-map begin -->"
MARKER_END = b"<!-- dayz-serverman mission-map end -->"
# Any spelling of the marker text, including a damaged or partial marker, counts as reserved
MARKER_TEXT = re.compile(rb"dayz-serverman\s+mission-map", re.IGNORECASE)
# Display colour of the editor territory, opaque red in the native decimal ARGB form
TERRITORY_COLOR = "4294901760"
# Root end tag; the element goes directly before its line
ROOT_END = re.compile(rb"</territory-type\s*>")
# Indentation of the territory element and of its zones, as in the installed missions
TERRITORY_INDENT, ZONE_INDENT = b"    ", b"        "

# Refusal reasons; each one refuses the whole file and writes nothing
MARKER_CONFLICT = "marker_conflict"
TERRITORY_FILE_MISSING = "territory_file_missing"
TERRITORY_FILE_INVALID = "territory_file_invalid"
EVENTS_FILE_INVALID = "events_file_invalid"
ENCOUNTER_INVALID = "encounter_invalid"
EVENT_NOT_OFFERED = "event_not_offered"


class TerritoryRefused(ValueError):
    """Refusal of the territory file with a named reason and, when known, the encounter it concerns."""

    def __init__(self, reason: str, message: str, *, object_id: str | None = None) -> None:
        """Keep the reason code and the encounter identifier for the preview report."""
        self.reason, self.object_id = reason, object_id
        super().__init__(message)


@dataclass(frozen=True)
class TerritoryCandidate:
    """Candidate bytes, owned block, zone sources and preview caps; None content and block keep a file absent."""

    content: bytes | None
    owned: bytes | None
    encounter_ids: tuple[str, ...]
    caps: dict[str, Any] = field(default_factory=dict)


def infected_encounters(objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the native infected encounters that are not excluded, in plan order, after D1 checks."""
    result = []
    for item in objects:
        if item.get("kind") != "encounter" or item.get("provider") != Provider.NATIVE_INFECTED.value:
            continue
        # Re-check D1 so only valid names and numbers can reach the XML text
        try:
            parsed = parse_object(item)
        except ObjectRuleError as error:
            raise TerritoryRefused(ENCOUNTER_INVALID, f"Encounter is invalid: {error}",
                                   object_id=item.get("id") if isinstance(item.get("id"), str) else None) from error
        if not parsed["excluded"]:
            result.append(parsed)
    return result


def check_reserved(data: bytes) -> None:
    """Refuse an original or working baseline that already holds editor marker text (D8 conflict)."""
    if MARKER_TEXT.search(data) is not None:
        raise TerritoryRefused(MARKER_CONFLICT, "env/zombie_territories.xml already holds a mission-map marker. "
                                                "No file was changed.")


def owned_projection(data: bytes | None) -> bytes | None:
    """Return the marked block with both markers, empty bytes without markers, or None for an absent file.

    One begin marker followed by one end marker is the only accepted form.
    Any other marker text is ambiguous and refuses the file.
    """
    # An absent file has a null projection, which differs from a present file without a block (D8)
    if data is None:
        return None
    found = len(MARKER_TEXT.findall(data))
    if found == 0:
        return b""
    start, end = data.find(MARKER_BEGIN), data.find(MARKER_END)
    if found != 2 or start < 0 or end < start:
        raise TerritoryRefused(MARKER_CONFLICT, "env/zombie_territories.xml has damaged mission-map markers. "
                                                "No file was changed.")
    return data[start:end + len(MARKER_END)]


def owned_digest(data: bytes | None) -> str | None:
    """Return the D3 owned-value digest: SHA-256 of the marked block, or None for an absent file."""
    projection = owned_projection(data)
    return None if projection is None else hashlib.sha256(projection).hexdigest()


def build_territory_candidate(baseline: bytes | None, objects: Iterable[dict[str, Any]], *,
                              events: bytes | None, globals_xml: bytes | None) -> TerritoryCandidate:
    """Rebuild the editor territory from the working baseline and the infected encounters of the plan."""
    encounters = infected_encounters(objects)
    caps = infected_caps(events, globals_xml, [item["properties"]["event_name"] for item in encounters])
    if baseline is None:
        if encounters:
            raise TerritoryRefused(TERRITORY_FILE_MISSING, "env/zombie_territories.xml does not exist.")
        return TerritoryCandidate(None, None, (), caps)
    check_reserved(baseline)
    territories = _xml(baseline, "territory-type", TERRITORY_FILE_INVALID)
    # Removal and an empty plan both give the baseline bytes unchanged
    if not encounters:
        return TerritoryCandidate(baseline, b"", (), caps)
    offered = set(offered_infected_events(territories, _xml(events, "events", EVENTS_FILE_INVALID)))
    for item in encounters:
        if item["properties"]["event_name"] not in offered:
            raise TerritoryRefused(EVENT_NOT_OFFERED, "Encounter event is not an offered infected event.",
                                   object_id=item["id"])
    content = _insert_block(baseline, encounters)
    # The candidate must still parse with every original zone and one added territory
    result = _xml(content, "territory-type", TERRITORY_FILE_INVALID)
    if len(result.findall("territory")) != len(territories.findall("territory")) + 1:
        raise TerritoryRefused(TERRITORY_FILE_INVALID, "env/zombie_territories.xml has an unsupported layout.")
    return TerritoryCandidate(content, owned_projection(content), tuple(item["id"] for item in encounters), caps)


def infected_caps(events: bytes | None, globals_xml: bytes | None, names: Iterable[str]) -> dict[str, Any]:
    """Return event `nominal` and `max` for each named event and `ZombieMaxCount`; unknown values are None."""
    event_values: dict[str, dict[str, int | None]] = {}
    root = _optional_xml(events, "events")
    for name in sorted(set(names)):
        nodes = [] if root is None else [node for node in root.findall("event") if node.get("name") == name]
        # A missing or duplicated event gives no trustworthy cap
        node = nodes[0] if len(nodes) == 1 else None
        event_values[name] = {key: None if node is None else _integer_child(node, key) for key in ("nominal", "max")}
    limit = None
    root = _optional_xml(globals_xml, "variables")
    if root is not None:
        values = [node.get("value") for node in root.findall("var") if node.get("name") == "ZombieMaxCount"]
        limit = _integer(values[0]) if len(values) == 1 else None
    return {"events": event_values, "zombie_max_count": limit}


def _insert_block(baseline: bytes, encounters: list[dict[str, Any]]) -> bytes:
    """Insert the marked territory before the root end tag line; no other byte changes."""
    ends = list(ROOT_END.finditer(baseline))
    # Only whitespace may follow the root end tag, so the match cannot sit in a trailing comment
    if not ends or baseline[ends[-1].end():].strip():
        raise TerritoryRefused(TERRITORY_FILE_INVALID, "env/zombie_territories.xml has an unsupported layout.")
    newline = b"\r\n" if b"\r\n" in baseline else b"\n"
    zones = [ZONE_INDENT + _zone(item) for item in encounters]
    lines = [TERRITORY_INDENT + MARKER_BEGIN, TERRITORY_INDENT + f'<territory color="{TERRITORY_COLOR}">'.encode(),
             *zones, TERRITORY_INDENT + b"</territory>", TERRITORY_INDENT + MARKER_END]
    block = newline.join(lines) + newline
    position = ends[-1].start()
    line_start = baseline.rfind(b"\n", 0, position) + 1
    # An end tag on its own line keeps its line; otherwise the block starts on a new line
    if not baseline[line_start:position].strip():
        return baseline[:line_start] + block + baseline[line_start:]
    return baseline[:position] + newline + block + baseline[position:]


def _zone(item: dict[str, Any]) -> bytes:
    """Return one native zone element with the attribute order of the installed missions."""
    values = item["properties"]
    counts = " ".join(f'{key}="{values[key]}"' for key in ("smin", "smax", "dmin", "dmax"))
    text = (f'<zone name="{values["event_name"]}" {counts} x="{_number(item["centre"]["x"])}" '
            f'z="{_number(item["centre"]["z"])}" r="{_number(item["radius"])}"/>')
    return text.encode("utf-8")


def _number(value: float) -> str:
    """Return the shortest round-trip decimal without an exponent or a trailing `.0`."""
    text = format(Decimal(repr(float(value))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _xml(data: bytes | None, root_tag: str, reason: str) -> Element:
    """Parse strict UTF-8 mission XML with the expected root, or refuse with the reason."""
    root = _optional_xml(data, root_tag)
    if root is None:
        raise TerritoryRefused(reason, f"Mission XML with the root {root_tag} is missing or invalid.")
    return root


def _optional_xml(data: bytes | None, root_tag: str) -> Element | None:
    """Return the parsed root, or None for absent, invalid or differently rooted XML."""
    if data is None:
        return None
    try:
        root = validate_xml(data.removeprefix(UTF8_BOM).decode("utf-8"))
    except (UnicodeDecodeError, ConfigurationFileError):
        return None
    return root if root.tag == root_tag else None


def _integer_child(node: Element, name: str) -> int | None:
    """Return the integer text of exactly one child element, or None."""
    children = node.findall(name)
    return _integer(children[0].text) if len(children) == 1 else None


def _integer(text: str | None) -> int | None:
    """Return a plain non-negative integer, or None for any other text."""
    stripped = (text or "").strip()
    return int(stripped) if re.fullmatch(r"[0-9]{1,10}", stripped) else None
