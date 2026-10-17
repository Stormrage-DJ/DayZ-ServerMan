"""Adoption cases, routes and refusal reasons, and the offer fields `adoption` and `capture.adoption` (leaf 10.13).

The rules are D2 "Adoption Rules (10.2)" of detailed-design.md with the Architect rulings of 2026-10-16 14:33:32
(AR-6, UX-2) in evidence 10.1, and 14:53:17 (OP-2) and 15:29:18 (V-10.2-F2) in evidence 10.2.

These functions are pure. The caller gives the facts that it took from the records, the folders and the path
state. No function here reads a mission file or compares a file (UX-2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The four adoption cases of the load field and of the review
MOVE, COPY, RETIRED, RESTORE = "move", "copy", "retired", "restore"
CASES = (MOVE, COPY, RETIRED, RESTORE)
# The two routes to a copy (OP-1): the path change of this profile, or the 8.6 capture refusal
PATH_CHANGE, CAPTURE = "path_change", "capture"
# The read-only reasons of the path-change state (D2)
MISSION_PATH_CHANGED, RETURNED = "mission_path_changed", "returned_to_earlier_path"
# Closed refusal reasons of the adoption review (D13, exit 3)
NOTHING_TO_ADOPT, ADOPTION_REFUSED = "nothing_to_adopt", "adoption_refused"
# Version of the adoption answer and of its fingerprint basis
ADOPTION_VERSION = 1
# Lane kind and D4 operation kind of the adoption commit (AR-3, leaf 10.35)
ADOPT_LANE_KIND, ADOPT_KIND = "ADOPT_MISSION_MAP", "adopt"
# An original that the new key already has is kept. It has the same files as the source original, or other
# files (Architect 2026-10-17 11:42:47 in evidence 10.35)
KEPT_SAME, KEPT_DIFFERENT = "same", "different"


def path_change_case(old_folder_exists: bool, shared: bool) -> str:
    """Return the case after a path change of this profile (OP-2).

    The case is `move` only when the old mission folder no longer exists and no other profile resolves to the
    source key. Every other case is `copy`.
    """
    return MOVE if not old_folder_exists and not shared else COPY


def load_case(path_reason: str | None, invalidated: bool, *, old_folder_exists: bool = True,
              shared: bool = True) -> str | None:
    """Return the case of the load field from the path state and the records, or None when nothing is offered.

    `path_reason` is the read-only reason of the path-change state, else None. `invalidated` says that the active
    folder holds an invalidated last-applied record that no adoption closed (AR-6). The folder facts matter only
    after a path change; their defaults give `copy`, which never moves records.
    """
    if path_reason == RETURNED:
        return RETIRED
    if path_reason == MISSION_PATH_CHANGED:
        return path_change_case(old_folder_exists, shared)
    return RESTORE if invalidated else None


def offer(case: str | None) -> dict[str, str] | None:
    """Return the value of an offer field: null, or `{"case": ...}` (UX-2)."""
    if case is None:
        return None
    if case not in CASES:
        raise ValueError(f"unknown adoption case: {case}")
    return {"case": case}


def capture_case(capture: Mapping[str, Any]) -> str | None:
    """Return `copy` when the 8.6 capture check refused with `editor_output` for a source key with an original.

    The capture route always gives `copy`, also for a renamed mission folder (V-10.2-F2). Any other capture
    result, also a refusal of reserved names or an output match without an original, offers nothing.
    """
    if capture.get("result") == "refused" and capture.get("reason") == "editor_output" \
            and capture.get("adoption_required") is True:
        return COPY
    return None
