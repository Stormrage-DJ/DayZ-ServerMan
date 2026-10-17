"""Review blocks of the import report of D7 (leaf 10.33); `map copy` (10.7) uses the same blocks.

The order follows the Product Owner slots of 2026-10-16 14:26:38 in evidence 10.1: terrain, bounds, object
count, new IDs, insertion, global capacity, the three lists, the discarded fields, the result and the
placement review. A review names each object by the answer: its name, else its ID (D12).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.mission_map_loot_report_texts import UNAVAILABLE_TEXTS
from . import map_wording as words
from .commands.common import profile_line
from .map_inspect_wording import signed
from .map_publish_wording import NOT_OFFERED, filled
from .map_render import number_text
from .output import Block, Line, Value, sentence

# Rows under a heading of the review
ROW = "  "


def transfer_blocks(profile: Mapping[str, Any] | None, report: Mapping[str, Any],
                    labels: Mapping[str, str] = words.IMPORT_LABELS) -> list[Block]:
    """Return the review of an import or copy report, after the profile line.

    `labels` holds the labels that name a file: `IMPORT_LABELS` for import, `COPY_LABELS` for copy.
    """
    blocks: list[Block] = [profile_line(profile), sentence(words.TRANSFER_HEADING)]
    blocks += [_terrain(report, labels), _bounds(report, labels),
               filled(labels["objects"], count=str(report.get("object_count", 0)))]
    remapped = [entry for entry in report.get("remapped") or [] if isinstance(entry, Mapping)]
    blocks.append(filled(words.NEW_IDS, count=str(len(remapped))))
    blocks += [sentence(ROW, Value(str(entry.get("from")))) for entry in remapped]
    blocks.append(sentence(labels["top"] if report.get("insertion") == "top" else labels["replace"]))
    blocks += _capacity(report, labels)
    unavailable = [sentence(ROW, Value(_name(item)), ": ", Value(reason_text(item)))
                   for item in _items(report, "unavailable")]
    blocks += _listed(words.UNAVAILABLE_HEADING, unavailable)
    blocks += _listed(words.EXCLUDED_HEADING, [_excluded(item) for item in _items(report, "excluded")])
    blocks += _listed(words.UNMAPPED_HEADING, [_unmapped(item) for item in _items(report, "unmapped")])
    discarded = list(report.get("discarded") or [])
    if discarded:
        blocks.append(filled(labels["discarded"], count=str(len(discarded))))
    objects, vertices = report.get("result_object_count", 0), report.get("result_vertex_count", 0)
    blocks.append(filled(words.RESULT_FORMS[(objects == 1, vertices == 1)], objects=str(objects),
                         vertices=str(vertices)))
    if report.get("placement_review"):
        blocks.append(sentence(words.PLACEMENT_REVIEW))
    return blocks


def _terrain(report: Mapping[str, Any], labels: Mapping[str, str]) -> Line:
    """Return the terrain line: the same terrain, or both terrain IDs."""
    if report.get("terrain_match"):
        return sentence(words.TERRAIN_SAME)
    return filled(labels["terrain"], imported=str(report.get("imported_terrain_id")),
                  own=str(report.get("destination_terrain_id")))


def _bounds(report: Mapping[str, Any], labels: Mapping[str, str]) -> Line:
    """Return the bounds line: the same bounds, or the difference of the file or source plan in metres."""
    if report.get("bounds_equal"):
        return sentence(words.BOUNDS_SAME)
    difference = report.get("bounds_difference") if isinstance(report.get("bounds_difference"), Mapping) else {}
    return filled(labels["bounds"], x=number_text(difference.get("x_min")), z=number_text(difference.get("z_min")),
                  w=number_text(difference.get("width")), h=number_text(difference.get("height")))


def _capacity(report: Mapping[str, Any], labels: Mapping[str, str]) -> list[Block]:
    """Return the global capacity line; Merge shows it only when the file or source plan has another value (D7)."""
    capacity = report.get("global_abundance") if isinstance(report.get("global_abundance"), Mapping) else {}
    imported, destination = capacity.get("imported", 0), capacity.get("destination", 0)
    if report.get("insertion") == "replace":
        return [filled(words.CAPACITY_REPLACE, imported=signed(imported))]
    if imported == destination:
        return []
    return [filled(labels["capacity"], destination=signed(destination), imported=signed(imported))]


def reason_text(item: Mapping[str, Any]) -> str:
    """Return the existing text of an unavailable object: the tier text and the provider texts of `map show`.

    A native infected event that the mission does not offer uses the window text of the Apply review. A provider
    that the read did not return has its own text; another reason without a text uses the fallback of `map show`.
    """
    reason, provider = str(item.get("reason")), str(item.get("provider"))
    if reason in UNAVAILABLE_TEXTS:
        return UNAVAILABLE_TEXTS[reason]
    if reason == "event_not_offered":
        return NOT_OFFERED.format(event=item.get("event_name"))
    if reason == "provider_unavailable":
        return words.PROVIDER_UNREAD
    if provider in words.PROVIDER_NAMES:
        return words.provider_reason(provider, {"reason": reason})
    return words.NO_REASON_TEXT


def _items(report: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    """Return the entries of one list of the report."""
    return [item for item in report.get(key) or [] if isinstance(item, Mapping)]


def _listed(heading: str, rows: list[Line]) -> list[Block]:
    """Return a heading with its rows, or nothing for an empty list."""
    return [sentence(heading), *rows] if rows else []


def _name(item: Mapping[str, Any]) -> str:
    """Return the name of an object, else its ID."""
    return str(item.get("name") or item.get("id") or "")


def _excluded(item: Mapping[str, Any]) -> Line:
    """Return one excluded object; an encounter that leaves with its linked loot says so."""
    loot = item.get("linked_loot")
    if isinstance(loot, Mapping) and loot.get("enabled"):
        return sentence(ROW, *filled(words.WITH_LINKED_LOOT, name=_name(item)).parts)
    return sentence(ROW, Value(_name(item)))


def _unmapped(item: Mapping[str, Any]) -> Line:
    """Return one item that is not imported, as the answer words it: its ID, then its reason."""
    identifier, reason = item.get("id"), str(item.get("reason") or "")
    if identifier:
        return sentence(ROW, Value(str(identifier)), ": ", Value(reason))
    return sentence(ROW, Value(reason))
