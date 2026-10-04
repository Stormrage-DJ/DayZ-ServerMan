"""Strict parsing and classification of a published-file details answer."""

from __future__ import annotations

import json
import re
from typing import Any

from ..domain.update_check import WORKSHOP_CONSUMER_APP_ID, RemoteItem, RemoteItemResult

# Decimal text form accepted for the optional size field
_DECIMAL_TEXT = re.compile(r"[0-9]{1,20}")


class MalformedResponse(ValueError):
    """Raised when the body is not UTF-8, not strict JSON, or has the wrong shape."""
    pass


def parse_entries(body: bytes) -> list[Any]:
    """Return the details list of a strict UTF-8 JSON body."""
    try:
        # Reject NaN and Infinity, which strict JSON does not allow
        document = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise MalformedResponse("details answer is not strict JSON") from error
    # Require an object root with the details list in its response object
    response = document.get("response") if isinstance(document, dict) else None
    entries = response.get("publishedfiledetails") if isinstance(response, dict) else None
    if not isinstance(entries, list):
        raise MalformedResponse("details answer has the wrong shape")
    return entries


def classify_entries(valid: tuple[str, ...], entries: list[Any]) -> dict[str, RemoteItem]:
    """Return one item result for every sent id from the answered entries."""
    # Group the entries of requested ids; every other entry is ignored
    matches: dict[str, list[dict[str, Any]]] = {key: [] for key in valid}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = _entry_id(entry.get("publishedfileid"))
        if entry_id in matches:
            matches[entry_id].append(entry)
    # An absent or repeated id has no usable answer
    return {
        key: _item(key, found[0]) if len(found) == 1
        else RemoteItem(key, RemoteItemResult.INVALID)
        for key, found in matches.items()
    }


def _item(workshop_id: str, entry: dict[str, Any]) -> RemoteItem:
    """Return the item result of one entry; the first matching rule wins."""
    # Steam reports result 1 only for an item it lists
    if not _is_integer(entry.get("result")) or entry["result"] != 1:
        return RemoteItem(workshop_id, RemoteItemResult.NOT_FOUND)
    # Accept only items that belong to the DayZ Workshop
    consumer = entry.get("consumer_app_id")
    if not _is_integer(consumer) or consumer != WORKSHOP_CONSUMER_APP_ID:
        return RemoteItem(workshop_id, RemoteItemResult.WRONG_APP)
    # The update time must be a positive integer of seconds
    time_updated = entry.get("time_updated")
    if not _is_integer(time_updated) or time_updated <= 0:
        return RemoteItem(workshop_id, RemoteItemResult.INVALID)
    return RemoteItem(
        workshop_id, RemoteItemResult.OK, time_updated, _file_size(entry.get("file_size")),
    )


def _entry_id(value: Any) -> str | None:
    """Return the entry id as decimal text, or None when it has another type."""
    if isinstance(value, str):
        return value
    if _is_integer(value):
        return str(value)
    return None


def _file_size(value: Any) -> int | None:
    """Return the size from an integer or decimal text; None when absent or malformed."""
    if _is_integer(value):
        return value if value >= 0 else None
    if isinstance(value, str) and _DECIMAL_TEXT.fullmatch(value) is not None:
        return int(value)
    return None


def _is_integer(value: Any) -> bool:
    """Return whether the value is an integer and not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def _reject_constant(name: str) -> None:
    """Refuse the non-standard JSON constants NaN and Infinity."""
    raise ValueError(f"non-standard JSON constant: {name}")
