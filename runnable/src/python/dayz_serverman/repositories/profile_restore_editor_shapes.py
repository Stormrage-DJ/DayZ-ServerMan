"""Journal shapes of the editor group of a profile restore: the file kinds, their paths and the group form (A10).

The fourth group of journal version 2 holds editor record files of the restored profile only. These pure checks
let the journal reject any other path before recovery derives a path from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Role of the fourth journal group, and the editor area below the manager root as a manager-relative prefix
EDITOR_ROLE = "editor"
AREA = "data/mission-map/"
GROUP_FIELDS = frozenset(("role", "manager_root", "created", "files"))
FILE_FIELDS = frozenset(("kind", "relative", "before", "after", "published", "held"))
# The path shape of each file kind below the editor area; the profile part must be the restored profile
_KEY = r"[0-9a-f]{32}"
_OPERATION = r"(?P<operation>[A-Za-z0-9_-]{1,64})"
_ASSOCIATION = rf"profiles/(?P<profile>[^/]+)/(?P<key>{_KEY})/"
SHAPES = {
    "plan": _ASSOCIATION + r"plan\.json",
    "baseline": _ASSOCIATION + r"baseline\.json",
    "applied": _ASSOCIATION + r"applied\.json",
    "invalidated": _ASSOCIATION + r"applied\.invalidated-[0-9]{8}T[0-9]{6}Z(?:-[0-9]+)?\.json",
    "restored_copy": _ASSOCIATION + rf"restored-(?:mission|runtime)-{_OPERATION}\.json",
    "original": rf"originals/(?P<key>{_KEY})/original\.json",
    "copied_original": rf"originals/(?P<key>{_KEY})/original\.json",
    "superseded": rf"originals/(?P<key>{_KEY})/superseded-{_OPERATION}\.json",
    "ledger": rf"ledger/(?P<key>{_KEY})/{_OPERATION}\.json",
    "content": rf"(?:{_ASSOCIATION}|originals/(?P<original>{_KEY})/)files/(?P<sha>[0-9a-f]{{64}})",
}
# The only kind whose target can exist before the restore: the active original that a supersede replaces
REPLACING = frozenset(("original",))


@dataclass(frozen=True)
class EditorFile:
    """One file that the restore writes for the new profile: its kind, its manager-relative path and its bytes.

    A copied original of the case `restore` also holds `source`: the verified archive bytes of its source original.
    """

    kind: str
    relative: str
    data: bytes
    source: bytes | None = None


def publication_order(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the groups in publication order: the editor group publishes before the profile record."""
    return [*groups[:2], *groups[3:], groups[2]]


def match_file(kind: object, relative: object, profile_id: str) -> re.Match[str]:
    """Return the parts of an editor path of its kind; refuse another shape or a record of another profile."""
    shape = SHAPES.get(kind) if isinstance(kind, str) else None
    found = None
    if shape is not None and isinstance(relative, str) and relative.startswith(AREA):
        found = re.fullmatch(shape, relative[len(AREA):])
    if found is None or found.groupdict().get("profile") not in (None, profile_id):
        raise ValueError("Restore editor file is not a record of the restored profile.")
    return found


def validate_group(group: object, profile_id: str) -> None:
    """Check the journal form of the editor group, so that recovery derives only paths of the restored profile."""
    if (not isinstance(group, dict) or set(group) != GROUP_FIELDS or group["role"] != EDITOR_ROLE
            or not isinstance(group["manager_root"], str) or not isinstance(group["created"], list)
            or not isinstance(group["files"], list) or not group["files"]):
        raise ValueError("Invalid restore editor group.")
    seen: set[str] = set()
    for item in group["files"]:
        if not isinstance(item, dict) or set(item) != FILE_FIELDS or not all(
                type(item[flag]) is bool for flag in ("published", "held")):
            raise ValueError("Invalid restore editor file.")
        match_file(item["kind"], item["relative"], profile_id)
        if item["relative"].casefold() in seen or (item["before"] is not None and item["kind"] not in REPLACING):
            raise ValueError("Restore journal attempts to replace an unowned target.")
        seen.add(item["relative"].casefold())
        for value in (item["before"], item["after"]):
            _check_inventory(value)
    # A created folder is the editor area or a folder above one of the files of the group
    for folder in group["created"]:
        if not isinstance(folder, str) or not (folder + "/").startswith(AREA) or not any(
                item["relative"].startswith(folder + "/") for item in group["files"]):
            raise ValueError("Restore editor folder is not owned by the group.")


def _check_inventory(value: object) -> None:
    """Accept an absent file or a file inventory with its SHA-256 digest and size."""
    if value is not None and (not isinstance(value, dict) or set(value) != {"type", "sha256", "size"}
                              or value["type"] != "file" or not isinstance(value["sha256"], str)
                              or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
                              or type(value["size"]) is not int or value["size"] < 0):
        raise ValueError("Restore editor file inventory is invalid.")
