"""Text blocks of `map show` (leaf 10.4, design D12): what the editor window reports at load, as lines and a table."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.mission_map_loot_report_texts import UNAVAILABLE_TEXTS
from . import map_wording as words
from .commands.common import labelled, profile_line
from .map_inspect_wording import signed
from .output import Block, Table, Value, sentence


def number_text(value: object, places: int = 3) -> str:
    """Return a number with at most `places` decimals and no trailing zeros; a value that is no number is empty."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def show_blocks(profile: Mapping[str, Any] | None, loaded: Mapping[str, Any]) -> list[Block]:
    """Return the lines of `map show` in the order of D12: identity, state, terrain, capabilities, plan."""
    plan = loaded.get("plan") if isinstance(loaded.get("plan"), Mapping) else None
    capabilities = loaded.get("capabilities") if isinstance(loaded.get("capabilities"), Mapping) else {}
    state = str(loaded.get("state"))
    blocks: list[Block] = [profile_line(profile), labelled(words.MISSION_LABEL, str(loaded.get("mission_name", ""))),
                           sentence(f"{words.STATE_LABEL}: {words.PLAN_STATES.get(state, state)}")]
    # The backend detail of an invalid or stale plan; the other-association state has its own sentence
    if loaded.get("detail") and state != "OTHER_ASSOCIATION":
        blocks.append(sentence(Value(str(loaded["detail"]))))
    if loaded.get("revision") is not None:
        blocks.append(labelled(words.REVISION_LABEL, str(loaded["revision"])))
    blocks.extend(_read_only(loaded))
    blocks.extend(_earlier_paths(loaded))
    blocks.extend(_terrain(plan, capabilities))
    blocks.extend(_not_available(capabilities))
    if plan is not None:
        blocks.append(sentence(f"{words.CAPACITY_LABEL}: {signed(plan.get('global_abundance') or 0)}"))
    blocks.extend(_objects(plan, loaded.get("evaluation"), capabilities))
    applied = _applied_line(loaded, plan, state)
    return blocks + ([sentence(applied)] if applied else [])


def _applied_line(loaded: Mapping[str, Any], plan: Mapping[str, Any] | None, state: str) -> str | None:
    """Return the applied-state line: none unless the plan is valid (QA-10.31-F4, Product Owner 2026-10-17)."""
    if plan is None or state != "VALID":
        return None
    if loaded.get("draft_changes"):
        return words.APPLIED_CHANGES
    filled = bool(plan.get("objects")) or bool(plan.get("global_abundance"))
    return words.APPLIED_MATCHES if filled else words.APPLIED_EMPTY


def _read_only(loaded: Mapping[str, Any]) -> list[Block]:
    """Return the window text of the read-only reason, the CLI line that names the command, and the adoption line.

    ADOPT-6 of `adoption_required` shows also while the plan stays editable. The adoption line of the load field
    shows only when no shown read-only text names adoption (Product Owner 20:58:44 in evidence 10.10).
    """
    reason = loaded.get("read_only_reason")
    shown = bool(loaded.get("read_only")) or reason == "adoption_required"
    blocks: list[Block] = []
    if shown:
        blocks.append(sentence(words.READ_ONLY_TEXTS.get(str(reason), words.READ_ONLY_FALLBACK)))
        way_out = words.read_only_way_out(reason)
        blocks += [way_out] if way_out is not None else []
    adoption = words.adoption_line(loaded.get("adoption"))
    if adoption is not None and not (shown and reason in words.ADOPTION_REASONS):
        blocks.append(adoption)
    return blocks


def _earlier_paths(loaded: Mapping[str, Any]) -> list[Block]:
    """Return the mission paths with a map plan before a path change, and the interrupted save evidence."""
    blocks: list[Block] = []
    change = loaded.get("path_change") if isinstance(loaded.get("path_change"), Mapping) else None
    folders = [folder for folder in (change or {}).get("folders", []) if isinstance(folder, Mapping)]
    if folders:
        blocks.append(sentence(words.PATH_CHANGE_HEADING))
        blocks.extend(sentence("  ", Value(str(folder.get("mission_root")))) for folder in folders)
    evidence = [str(name) for name in loaded.get("interrupted_evidence") or []]
    if evidence:
        text = words.INTERRUPTED_EVIDENCE.split("{names}")
        blocks.append(sentence(text[0], Value(", ".join(evidence)), text[1]))
    return blocks


def _terrain(plan: Mapping[str, Any] | None, capabilities: Mapping[str, Any]) -> list[Block]:
    """Return the terrain, bounds, tier writes and the unverified-family text of the mission."""
    terrain = (plan or {}).get("terrain") if isinstance((plan or {}).get("terrain"), Mapping) else {}
    identity = terrain.get("terrain_id") or capabilities.get("terrain_id")
    blocks: list[Block] = [labelled(words.TERRAIN_LABEL, str(identity)) if identity
                           else sentence(f"{words.TERRAIN_LABEL}: {words.UNKNOWN_TERRAIN}")]
    bounds = terrain.get("bounds") if isinstance(terrain.get("bounds"), Mapping) else None
    confirmed = words.BOUNDS_LINE[bool(terrain.get("bounds_confirmed"))]
    if bounds is None:
        blocks.append(sentence(f"{words.BOUNDS_LABEL}: {confirmed}."))
    else:
        blocks.append(sentence(f"{words.BOUNDS_LABEL}: {confirmed}. X minimum {number_text(bounds.get('x_min'))} m, "
                               f"Z minimum {number_text(bounds.get('z_min'))} m, width "
                               f"{number_text(bounds.get('width'))} m, height {number_text(bounds.get('height'))} m."))
    writes = capabilities.get("tier_writes") if isinstance(capabilities.get("tier_writes"), Mapping) else {}
    blocks.append(sentence(f"{words.WRITES_LABEL}: {words.WRITES_TEXTS[bool(writes.get('available'))]}"))
    family = capabilities.get("family") if isinstance(capabilities.get("family"), Mapping) else {}
    if family.get("verified") is False and isinstance(family.get("text"), str):
        blocks.append(sentence(family["text"]))
    return blocks


def _not_available(capabilities: Mapping[str, Any]) -> list[Block]:
    """Return the heading and one row for each operation or encounter provider that the mission cannot support."""
    rows: list[str] = []
    operations = [item for item in capabilities.get("unsupported") or [] if isinstance(item, Mapping)]
    for item in operations:
        operation = str(item.get("operation"))
        rows.append(f"{words.operation_name(operation)}: {words.unsupported_reason(operation, item.get('reason'))}")
    writes = capabilities.get("tier_writes") if isinstance(capabilities.get("tier_writes"), Mapping) else {}
    # Bounds reasons are not in the unsupported list; the tier write reason is the only place that has them
    if writes.get("available") is False and not any(item.get("operation") == "tier_writes" for item in operations):
        rows.append(f"{words.OPERATION_NAMES['tier_writes']}: "
                    f"{words.unsupported_reason('tier_writes', writes.get('reason'))}")
    providers = capabilities.get("encounter_providers") if isinstance(capabilities.get("encounter_providers"),
                                                                      Mapping) else {}
    rows.extend(words.provider_row(name, entry)
                for name, entry in providers.items() if name in words.PROVIDER_NAMES
                and isinstance(entry, Mapping) and entry.get("available") is False)
    if not rows:
        return []
    return [sentence(words.UNSUPPORTED_HEADING), *(sentence("  ", Value(row)) for row in rows)]


def _objects(plan: Mapping[str, Any] | None, evaluation: object, capabilities: Mapping[str, Any]) -> list[Block]:
    """Return the object table in priority order, or the sentence for a plan without objects."""
    if plan is None:
        return []
    objects = [item for item in plan.get("objects") or [] if isinstance(item, Mapping)]
    if not objects:
        return [sentence(words.NO_OBJECTS)]
    entries = {item.get("id"): item for item in (evaluation or {}).get("objects", [])
               if isinstance(evaluation, Mapping) and isinstance(item, Mapping)}
    providers = capabilities.get("encounter_providers") if isinstance(capabilities.get("encounter_providers"),
                                                                      Mapping) else {}
    rows = tuple(_object_row(item, entries.get(item.get("id"), {}), providers) for item in objects)
    return [sentence(words.OBJECTS_HEADING),
            Table(words.OBJECT_HEADINGS, rows, input_columns=frozenset({0}), value_columns=frozenset({1}))]


def _object_row(item: Mapping[str, Any], evaluated: Mapping[str, Any],
                providers: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the cells of one object: ID, name, kind, tier, zone capacity and availability with its reason."""
    linked = item.get("linked_loot") if isinstance(item.get("linked_loot"), Mapping) else None
    loot = item if item.get("kind") != "encounter" else (linked if linked and linked.get("enabled") else None)
    tier = words.INHERIT if loot is not None and loot.get("tier") is None else str(loot.get("tier")) if loot else ""
    preset = words.ZONE_PRESETS.get(str(loot.get("abundance")), "") if loot is not None else ""
    return (str(item.get("id")), str(item.get("name") or item.get("id")),
            words.OBJECT_KINDS.get(str(item.get("kind")), str(item.get("kind"))), tier, preset,
            _availability(item, evaluated, providers))


def _availability(item: Mapping[str, Any], evaluated: Mapping[str, Any], providers: Mapping[str, Any]) -> str:
    """Return "Available", or "Not available" with the reason: the provider of an encounter, else the loot checks."""
    provider = str(item.get("provider"))
    entry = providers.get(provider) if item.get("kind") == "encounter" else None
    if isinstance(entry, Mapping) and entry.get("available") is False:
        return f"{words.NOT_AVAILABLE}: {words.provider_reason(provider, entry)}"
    if evaluated.get("available") is False:
        reason = evaluated.get("reason")
        return f"{words.NOT_AVAILABLE}: {UNAVAILABLE_TEXTS.get(str(reason), words.NO_REASON_TEXT)}"
    return words.AVAILABLE
