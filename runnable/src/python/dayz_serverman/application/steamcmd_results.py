"""Ordered conservative classification of sanitized SteamCMD evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.workshop import ItemOutcome, RequiredWorkshopItem
from ..security.sensitive import redact_text


# A success signature names the item SteamCMD finished downloading
SUCCESS = re.compile(r"success.*downloaded item\s+(?P<id>[0-9]+)", re.I)
# Terminal signatures name the item that ended the session
TERMINAL_ID = re.compile(r"(?:item|workshop_download_item)\s+(?P<id>[0-9]+)", re.I)
# Failure signatures are checked in conservative order, identity first
AUTHENTICATION = re.compile(r"(?:not logged on|invalid password|steam guard|login failure)", re.I)
ENTITLEMENT = re.compile(r"(?:access denied|no subscription|license|not owned)", re.I)
CONNECTION = re.compile(r"(?:no connection|timeout|connection failed|network is unreachable)", re.I)
CONTENT = re.compile(r"(?:item unavailable|file not found|download item.*failed|workshop.*failed)", re.I)


@dataclass(frozen=True)
class SessionEvidence:
    """Classification result for one SteamCMD session."""

    ordered: tuple[tuple[str, ItemOutcome], ...]
    invalid: bool
    safe_summary: str | None


def classify_output(
    lines: tuple[str, ...], items: tuple[RequiredWorkshopItem, ...],
) -> SessionEvidence:
    """Classify sanitized SteamCMD lines into ordered per-item evidence."""
    expected = tuple(item.workshop_id for item in items)
    ordered: list[tuple[str, ItemOutcome]] = []
    invalid = False
    stopped = False
    summary: str | None = None
    # Keep only recognizable signatures, sanitized and in output order
    for raw in lines:
        line = redact_text(raw[:8192]).strip()
        success = SUCCESS.search(line)
        failure = _failure_class(line)
        if success is None and failure is None:
            continue
        # Outcomes after a stop or past the expected count break the ordering
        if stopped or len(ordered) >= len(expected):
            invalid = True
            continue
        current = expected[len(ordered)]
        observed_id = success.group("id") if success else _terminal_id(line)
        # An id that skips the next expected item invalidates the session
        if observed_id is not None and observed_id != current:
            invalid = True
            continue
        # Contradictory success and failure evidence is refused
        if success is not None and failure is not None:
            invalid = True
            continue
        if success is not None:
            ordered.append((current, ItemOutcome.VERIFIED_CURRENT))
        else:
            # Record the first failure and stop accepting later outcomes
            assert failure is not None
            ordered.append((current, failure))
            summary = _safe_summary(failure)
            stopped = True
    return SessionEvidence(tuple(ordered), invalid, summary)


def terminal_outcomes(
    items: tuple[RequiredWorkshopItem, ...], evidence: SessionEvidence, *, cancelled: bool,
) -> dict[str, ItemOutcome]:
    """Derive a terminal outcome for every requested item."""
    # Any ordering violation makes every outcome unknown
    if evidence.invalid:
        return {item.workshop_id: ItemOutcome.UNKNOWN_FAILED for item in items}
    by_id = dict(evidence.ordered)
    result: dict[str, ItemOutcome] = {}
    failure_seen = False
    # Walk the requested order so untouched items inherit the run state
    for item in items:
        observed = by_id.get(item.workshop_id)
        if observed is not None:
            result[item.workshop_id] = observed
            failure_seen = failure_seen or observed not in (
                ItemOutcome.DOWNLOADED_VERIFIED,
                ItemOutcome.UPDATED_VERIFIED,
                ItemOutcome.VERIFIED_CURRENT,
            )
        elif cancelled:
            result[item.workshop_id] = ItemOutcome.CANCELLED
        # Items after a classified failure were never attempted
        elif failure_seen:
            result[item.workshop_id] = ItemOutcome.NOT_ATTEMPTED
        else:
            result[item.workshop_id] = ItemOutcome.UNKNOWN_FAILED
    return result


def _terminal_id(line: str) -> str | None:
    """Return the item id named on a terminal line, if any."""
    match = TERMINAL_ID.search(line)
    return match.group("id") if match else None


def _failure_class(line: str) -> ItemOutcome | None:
    """Classify a line into the strongest matching failure outcome."""
    if AUTHENTICATION.search(line):
        return ItemOutcome.AUTHENTICATION_FAILED
    if ENTITLEMENT.search(line):
        return ItemOutcome.ENTITLEMENT_FAILED
    if CONNECTION.search(line):
        return ItemOutcome.CONNECTION_FAILED
    if CONTENT.search(line):
        return ItemOutcome.CONTENT_FAILED
    return None


def _safe_summary(outcome: ItemOutcome) -> str:
    """Return the operator-safe summary text for a failure outcome."""
    return {
        ItemOutcome.AUTHENTICATION_FAILED: "Steam authentication is required or was rejected.",
        ItemOutcome.ENTITLEMENT_FAILED: "The selected Steam account lacks required entitlement.",
        ItemOutcome.CONNECTION_FAILED: "SteamCMD could not reach the Steam service.",
        ItemOutcome.CONTENT_FAILED: "SteamCMD reported a Workshop content failure.",
    }[outcome]


def error_code(outcome: ItemOutcome) -> str:
    """Return the bridge error code for an item outcome."""
    return {
        ItemOutcome.AUTHENTICATION_FAILED: "AUTHENTICATION_FAILED",
        ItemOutcome.ENTITLEMENT_FAILED: "ENTITLEMENT_DENIED",
        ItemOutcome.CONNECTION_FAILED: "CONNECTION_FAILED",
        ItemOutcome.CONTENT_FAILED: "WORKSHOP_CONTENT_FAILED",
        ItemOutcome.CANCELLED: "UPDATE_CANCELLED",
        ItemOutcome.NOT_ATTEMPTED: "UPDATE_NOT_ATTEMPTED",
        ItemOutcome.UNKNOWN_FAILED: "UPDATE_RESULT_UNKNOWN",
    }[outcome]
