"""Review blocks of the Apply answer for `map preview` and `map apply` (leaf 10.5, design D12 and D14).

The sections and their order are those of the Apply preview of the window (8.10). A section without content is
not printed.

Backend texts of the answer print unchanged: refusals, the 6.11 family text, `objects.order_text`,
`capture.text`, the `infected` notices in their order and the loot report texts. The window choices become the
CLI lines that name the options. Lists print every row; the window collapses long lists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import map_publish_wording as words
from .commands.common import profile_line
from .map_inspect_wording import group_capacity_text, signed
from .map_wording import PROVIDER_NAMES, adoption_line, provider_reason
from .output import Block, Line, Value, sentence

# Indents: section content, and the rows of a list
INDENT, ROW = "  ", "    "


def review_blocks(profile: Mapping[str, Any] | None, loaded: Mapping[str, Any],
                  answer: Mapping[str, Any]) -> list[Block]:
    """Return the review of one Apply answer: the profile, the eight sections, then the stop-server line."""
    refused = answer.get("refused") is True
    plan = plan_objects(loaded, answer)
    sections = (
        (words.REFUSALS_HEADING, _refusals(answer)),
        (words.NOTICES_HEADING, _notices(answer)),
        (words.EXTERNAL_HEADING, _external(answer, refused)),
        (words.ENCOUNTER_CHOICES_HEADING, _choices(answer, plan, refused)),
        (words.CHANGES_HEADING, _changes(_mapping(answer.get("objects")))),
        (words.ENCOUNTERS_HEADING, _encounters(answer, plan)),
        (words.LOOT_HEADING, _loot(_mapping(answer.get("loot")))),
        (words.FILES_HEADING, _files(answer)),
    )
    blocks: list[Block] = [profile_line(profile)]
    for heading, lines in sections:
        if lines:
            blocks.extend([sentence(heading), *lines])
    return [*blocks, sentence(words.STOP_SERVER)]


def plan_objects(loaded: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return the objects of the saved plan by ID when the read plan has the revision of the answer (AR-9).

    Otherwise the plan changed between the two calls, and the review names each object by its ID.
    """
    plan = _mapping(loaded.get("plan"))
    if loaded.get("revision") is None or loaded.get("revision") != _mapping(answer.get("plan")).get("revision"):
        return {}
    return {str(item.get("id")): item for item in plan.get("objects") or [] if isinstance(item, Mapping)}


def _mapping(value: object) -> Mapping[str, Any]:
    """Return a mapping of the answer, or an empty one for a missing value."""
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> list[Mapping[str, Any]]:
    """Return the mappings of a list of the answer."""
    return [item for item in value or [] if isinstance(item, Mapping)] if isinstance(value, list) else []


def _text(text: object) -> list[Block]:
    """Return one backend or window text as an indented line, or nothing for a missing text."""
    return [sentence(INDENT, Value(text))] if isinstance(text, str) and text else []


def _list(label: str | None, rows: Sequence[object]) -> list[Block]:
    """Return a list with its label; a list without rows is not printed (window: list)."""
    shown = [str(row) for row in rows if row not in (None, "")]
    if not shown:
        return []
    head = [sentence(INDENT, label)] if label else []
    return [*head, *(sentence(ROW, Value(row)) for row in shown)]


def _name(item: Mapping[str, Any]) -> str:
    """Return the name of an object of the answer, else its ID."""
    return str(item.get("name") or item.get("id"))


def _refusals(answer: Mapping[str, Any]) -> list[Block]:
    """Return the refusals, and the 6.11 family text once when no refusal carries it.

    The adoption line of `capture.adoption` follows the capture refusal (Product Owner 14:35:24 in evidence 10.1).
    """
    items = _items(answer.get("refusals"))
    messages = [str(item.get("message")) for item in items]
    family = _mapping(_mapping(answer.get("capabilities")).get("family"))
    if family.get("verified") is False and isinstance(family.get("text"), str) and family["text"] not in messages:
        messages.append(family["text"])
    lines = [_text(message) for message in messages]
    adoption = adoption_line(_mapping(answer.get("capture")).get("adoption"), capture=True)
    if adoption is not None:
        lines = lines or [[]]
        place = next((index for index, item in enumerate(items) if item.get("reason") == "editor_output"), -1)
        lines[place].append(_indented(adoption))
    return [line for group in lines for line in group]


def _notices(answer: Mapping[str, Any]) -> list[Block]:
    """Return the bounds line and the other profiles; the CLI has no unfinished outline."""
    lines: list[Block] = []
    writes = _mapping(_mapping(answer.get("capabilities")).get("tier_writes"))
    if writes.get("reason") == "bounds_not_confirmed":
        lines.append(sentence(INDENT, words.BOUNDS_NOT_CONFIRMED))
    profiles = _items(answer.get("other_profiles"))
    if profiles:
        names = [item.get("display_name") or words.DELETED_PROFILE.format(profile_id=item.get("profile_id"))
                 for item in profiles]
        lines.extend([sentence(INDENT, words.OTHER_PROFILES), *_list(None, names)])
    return lines


def _external(answer: Mapping[str, Any], refused: bool) -> list[Block]:
    """Return each file of the choices with its CLI choice line; a refused answer lists the paths only."""
    paths = [str(path) for path in _mapping(answer.get("choices")).get("external_changes") or []]
    changes = {str(item.get("path")): item for item in _items(answer.get("external_changes"))}
    if refused:
        return _list(None, paths)
    lines: list[Block] = []
    # The Replace the file note: once, when a file exists now and Keep the change is not refused for it
    if any(changes.get(path, {}).get("exists_now") is True and not _keep_refusal(changes.get(path, {}))
           for path in paths):
        lines.append(sentence(INDENT, words.REPLACE_NOTE))
    for path in paths:
        lines.extend([sentence(INDENT, Value(path)), _indented(words.filled(words.FILE_CHOICE, path=path))])
        lines.extend(_text(_keep_refusal(changes.get(path, {}))))
    return lines


def _keep_refusal(change: Mapping[str, Any]) -> str | None:
    """Return the text of a refused Keep the change, or None when Keep the change is available (8.7)."""
    message = _mapping(change.get("incorporate")).get("message")
    return str(message) if message else None


def _choices(answer: Mapping[str, Any], plan: Mapping[str, Mapping[str, Any]], refused: bool) -> list[Block]:
    """Return each unavailable encounter with its CLI choice line, its reason and the linked-loot line."""
    identifiers = [str(item) for item in _mapping(answer.get("choices")).get("unavailable_encounters") or []]
    entries = {str(item.get("id")): item for item in _items(_mapping(answer.get("encounters")).get("unavailable"))}
    if refused:
        return _list(None, [_name(entries.get(item, {"id": item})) for item in identifiers])
    lines: list[Block] = []
    for identifier in identifiers:
        entry = entries.get(identifier, {"id": identifier})
        lines.extend([sentence(INDENT, Value(_name(entry))),
                      _indented(words.filled(words.ENCOUNTER_CHOICE, id=identifier))])
        lines.extend(_text(encounter_reason(entry, plan.get(identifier), _mapping(answer.get("capabilities")))))
        if entry.get("linked_loot"):
            lines.append(sentence(INDENT, words.LINKED_LOOT))
    return lines


def encounter_reason(entry: Mapping[str, Any], item: Mapping[str, Any] | None,
                     capabilities: Mapping[str, Any]) -> str:
    """Return the 7.3 reason of an unavailable encounter: from its plan object, else from its provider (window)."""
    provider = str(entry.get("provider") or (item or {}).get("provider"))
    found = _mapping(capabilities.get("encounter_providers")).get(provider)
    if provider == "native_infected":
        events = [name for name in _mapping(found).get("event_names") or [] if isinstance(name, str)]
        if not (isinstance(found, Mapping) and found.get("available") is True and events):
            code = _mapping(found).get("reason") or ("no_matching_events" if found else "snapshot_missing")
            return provider_reason(provider, {"reason": code})
        event = _mapping((item or {}).get("properties")).get("event_name")
        return words.NOT_OFFERED.format(event=event) if item is not None and event not in events else ""
    if isinstance(found, Mapping) and isinstance(found.get("reason"), str):
        return str(found["reason"])
    fixed = {"ai_bandits": words.BANDIT_REASON, "native_wolf": words.WOLF_BEAR_REASON,
             "native_bear": words.WOLF_BEAR_REASON}
    return fixed.get(provider, "") if provider in PROVIDER_NAMES else ""


def _changes(objects: Mapping[str, Any]) -> list[Block]:
    """Return the order text, the global value, the previous-list line and the added, changed and removed lists."""
    lines = _text(objects.get("order_text"))
    current, applied = _mapping(objects.get("global")).get("current"), _mapping(objects.get("global")).get("applied")
    if isinstance(current, int) and not isinstance(current, bool):
        text = words.GLOBAL.format(current=signed(current))
        if isinstance(applied, int) and not isinstance(applied, bool) and applied != current:
            text += words.GLOBAL_APPLIED.format(applied=signed(applied))
        lines.append(sentence(INDENT, text))
    if objects.get("previous") is False:
        lines.append(sentence(INDENT, words.NO_PREVIOUS))
    for label, key in ((words.ADDED, "added"), (words.CHANGED, "changed"), (words.REMOVED, "removed")):
        lines.extend(_list(label, [_name(item) for item in _items(objects.get(key))]))
    return lines


def _encounters(answer: Mapping[str, Any], plan: Mapping[str, Mapping[str, Any]]) -> list[Block]:
    """Return the infected notices, the written encounters (named from the plan) and the plan-excluded list."""
    infected = _mapping(answer.get("infected"))
    encounters = _mapping(answer.get("encounters"))
    lines = [line for notice in _items(infected.get("notices")) for line in _text(notice.get("text"))]
    # `encounters.written` holds IDs; the saved plan names them when the revisions match (AR-9)
    written = [_name(plan.get(str(item), {"id": item})) for item in encounters.get("written") or []]
    lines.extend(_list(words.WRITTEN, written))
    # The members and the names come from the answer alone (Architect 2026-10-15 18:04:57, A-1)
    lines.extend(_list(words.EXCLUDED, [_name(item) for item in _items(encounters.get("excluded"))]))
    return lines + (_infected_values(infected) if written else [])


def _infected_values(infected: Mapping[str, Any]) -> list[Block]:
    """Return the values that limit infected, in answer order; a null value is "not read" (evidence 10.5)."""
    events = _mapping(infected.get("events"))
    rows = [words.INFECTED_EVENT.format(event=name, nominal=_read(_mapping(caps).get("nominal")),
                                        max=_read(_mapping(caps).get("max"))) for name, caps in events.items()]
    rows += [words.ZOMBIE_MAX_COUNT.format(value=_read(infected.get("zombie_max_count"))),
             words.ZONE_SPAWN_DIST.format(value=_read(infected.get("zone_spawn_dist")))]
    return _list(words.INFECTED_HEADING, rows)


def _read(value: object) -> str:
    """Return a value of the answer as text, or "not read" for null."""
    return words.NOT_READ if value is None else str(value)


def _loot(loot: Mapping[str, Any]) -> list[Block]:
    """Return the loot report in the window order: explanation, mechanisms, then the three labelled lists."""
    mechanisms = _mapping(loot.get("mechanisms"))
    fixed, shared = _mapping(mechanisms.get("fixed_dispatch")), _mapping(mechanisms.get("shared_category_capacity"))
    supply = _mapping(mechanisms.get("missing_supply"))
    lines = _text(loot.get("explanation"))
    if fixed.get("groups"):
        lines.extend([*_text(fixed.get("text")), *_list(None, [str(group) for group in fixed["groups"]])])
    if shared.get("groups"):
        rows = [f"{item.get('source')}: {', '.join(str(name) for name in item.get('categories') or [])}"
                for item in _items(shared["groups"])]
        lines.extend([*_text(shared.get("text")), *_list(None, rows)])
    # Unknown supply (categories null) has its own text and no list
    if supply and supply.get("categories") is None:
        lines.extend(_text(supply.get("text")))
    elif _mapping(supply.get("categories")):
        lines.extend([*_text(supply.get("text")), *_list(None, list(_mapping(supply["categories"])))])
    lines.extend(_list(words.LOOT_UNAVAILABLE, [f"{_name(item)}: {item.get('text')}"
                                                for item in _items(loot.get("unavailable"))]))
    capacity = [" ".join(part for part in (f"{entry.get('source')}: {group_capacity_text(dict(entry['group']))}",
                                           entry.get("bound_text")) if part)
                for entry in _items(loot.get("capacity"))
                if entry.get("class") == "changed" and isinstance(entry.get("group"), Mapping)]
    lines.extend(_list(words.LOOT_CAPACITY, capacity))
    # Each pair of name and reason once, in answer order
    kept = {(item.get("name"), item.get("reason")): f"{item.get('name')}: {item.get('text')}"
            for item in _items(loot.get("kept"))}
    lines.extend(_list(words.LOOT_KEPT, list(kept.values())))
    return lines


def _files(answer: Mapping[str, Any]) -> list[Block]:
    """Return the capture text, the files that Apply changes and the coordinated changes that it keeps."""
    lines = _text(_mapping(answer.get("capture")).get("text"))
    lines.extend(_list(words.AFFECTED, [item.get("path") for item in _items(answer.get("affected_files"))]))
    lines.extend(_list(words.COORDINATED, [item.get("path") for item in _items(answer.get("coordinated_changes"))]))
    return lines


def _indented(line: Line) -> Line:
    """Return a line with the indent of section content."""
    return sentence(INDENT, *line.parts)
