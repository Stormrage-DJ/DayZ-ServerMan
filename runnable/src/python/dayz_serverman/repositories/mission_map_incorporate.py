"""The incorporation rule for one external edit (8.7, R17, Product Owner 2026-10-12 23:42:49).

An admin may keep an external edit of a value that the editor does not own.
The edit then becomes part of the working baseline, with provenance. The
working baseline never holds a value that the editor wrote (Architect F2 of
2026-10-12 23:41:40). So incorporation is refused for that file when:

- the edit changed an editor-owned value: the owned-value digest of the
  current file differs from the one that the editor last knew; or
- the reversal of the applied realization does not give the owned values of
  the old working baseline.

Replace stays available. The preview shows the result for each external
change, and Apply uses the same rule. This module reads and writes no file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .mission_map_layout import TargetClass
from .mission_map_owned import OWNED_ERRORS, owned_digest
from .mission_map_reverse import ReversalError, reverse

# Reason codes of a refused incorporation
OWNED_VALUE_CHANGED = "owned_value_changed"
REVERSAL_FAILED = "reversal_failed"
# Text of a refused incorporation, for every reason (Product Owner 2026-10-15 01:16:10 in evidence 8.7)
OWNED_VALUE_TEXT = ("The change to {path} affects values that the map editor sets, so the map editor cannot keep this "
                    "change. Use the map editor to change these values. You can still replace the file. "
                    "DayZ-ServerMan saves a copy of the current file before it replaces the file.")
# The same text for an absent file: no copy exists, so it ends after the replace sentence (PO 07:10:16, evidence 8.8)
OWNED_VALUE_ABSENT_TEXT = ("The change to {path} affects values that the map editor sets, so the map editor cannot "
                           "keep this change. Use the map editor to change these values. You can still replace the "
                           "file.")
# Errors of a file whose owned values or layout cannot be read for the reversal
READ_ERRORS = OWNED_ERRORS + (ReversalError, ValueError)


@dataclass(frozen=True)
class Incorporation:
    """The result of the incorporation rule for one file: the new baseline bytes, or the refusal."""

    path: str
    available: bool
    data: bytes | None = None
    reason: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the preview form: whether Incorporate is available and, if not, why."""
        return {"available": self.available, "reason": self.reason, "message": self.message}


def incorporation(path: str, current: bytes | None, baseline: bytes | None, reference_owned: str | None,
                  groups: list[dict[str, Any]]) -> Incorporation:
    """Return the new working-baseline bytes of one externally edited file, or the refusal for it.

    `reference_owned` is the owned-value digest that the editor last knew of the
    file, and `groups` is the realization of the current applied record.
    """
    owned, damaged = _owned(path, current)
    if damaged or owned != reference_owned:
        return _refused(path, OWNED_VALUE_CHANGED, current)
    try:
        result = reverse(path, current, baseline, groups)
        same = _projection(path, result) == _projection(path, baseline)
    except READ_ERRORS:
        return _refused(path, REVERSAL_FAILED, current)
    if not same:
        return _refused(path, REVERSAL_FAILED, current)
    return Incorporation(path, True, data=result)


def _refused(path: str, reason: str, current: bytes | None) -> Incorporation:
    """Return a refused incorporation with its reason and the admin text; an absent file has no copy to save."""
    text = OWNED_VALUE_ABSENT_TEXT if current is None else OWNED_VALUE_TEXT
    return Incorporation(path, False, reason=reason, message=text.format(path=path))


def _owned(path: str, data: bytes | None) -> tuple[str | None, bool]:
    """Return the owned-value digest and whether the owned block is damaged."""
    try:
        return owned_digest(TargetClass.MISSION, path, data), False
    except OWNED_ERRORS:
        return None, True


def _projection(path: str, data: bytes | None) -> tuple[bool, str | None]:
    """Return existence and the owned-value digest; a present file without a projection differs from absence."""
    digest = owned_digest(TargetClass.MISSION, path, data)
    if data is not None and digest is None:
        # A placement file with invalid geometry has no projection; compare its exact bytes instead
        digest = "bytes:" + hashlib.sha256(data).hexdigest()
    return data is not None, digest
