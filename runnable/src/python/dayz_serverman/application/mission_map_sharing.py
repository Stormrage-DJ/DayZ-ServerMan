"""The targets of the other profiles: the settings part of the D2 "shared" check (Architect 16:49:30 in evidence 10.8).

D2 calls a target shared when another profile resolves to it or has an association folder for it. The commit
check sees only the folders. This helper gives the other part: the mission key and the runtime profile key of
each other profile record. A profile whose target cannot be resolved counts as sharing it (fail closed).

A profile whose target folder does not exist has no target, as in the runtime rule of 10.12. A restore computes
the result on the operation lane before its journal starts. Profile edits also run on the lane, so the result
cannot change during the restore.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from ..repositories.mission_map_layout import TargetClass, target_key
from .mission_configuration import MissionPathError, _contained, resolve_profile_mission

# Errors of a target that cannot be resolved; the profile then counts as sharing every target of the class
UNRESOLVED = (MissionPathError, OSError, ValueError)
# A profile whose target folder does not exist; such a folder cannot be the target of an existing key
_NO_TARGET = object()


@dataclass(frozen=True)
class OtherTargets:
    """The target key of each class that each other profile resolves; None means that it cannot be resolved."""

    mission: dict[str, str | None] = field(default_factory=dict)
    runtime: dict[str, str | None] = field(default_factory=dict)

    def sharing(self, target_class: TargetClass, key: str) -> tuple[str, ...]:
        """Return the other profiles that resolve to the key, or whose target of the class cannot be resolved."""
        found = self.mission if target_class is TargetClass.MISSION else self.runtime
        return tuple(sorted(profile for profile, value in found.items() if value is None or value == key))

    def to_dict(self) -> dict[str, Any]:
        """Return the result as plain values, which a preview fingerprint can bind."""
        return {"mission": dict(sorted(self.mission.items())), "runtime": dict(sorted(self.runtime.items()))}


def other_targets(root: Path, records: Iterable[Any], profile_id: str) -> OtherTargets:
    """Resolve the mission and runtime profile targets of every profile record except `profile_id`."""
    result = OtherTargets()
    for record in records:
        other = record.values.profile_id
        if other == profile_id:
            continue
        mission = _mission(root, record)
        if mission is not _NO_TARGET:
            result.mission[other] = mission
        runtime = _runtime(root, record.values.runtime_profile)
        if runtime is not _NO_TARGET:
            result.runtime[other] = runtime
    return result


def _mission(root: Path, record: Any) -> Any:
    """Return the mission key of a profile, None when it cannot be resolved, or _NO_TARGET."""
    try:
        return target_key(TargetClass.MISSION, resolve_profile_mission(root, record)[1])
    except UNRESOLVED:
        # Only an explicit mission folder that does not exist is proven separate
        relative = record.values.mission_root
        if relative is not None and not root.joinpath(*PureWindowsPath(relative).parts).exists():
            return _NO_TARGET
        return None


def _runtime(root: Path, relative: str | None) -> Any:
    """Return the runtime profile key of a profile, None when it cannot be resolved, or _NO_TARGET."""
    if relative is None:
        return _NO_TARGET
    candidate = root.joinpath(*PureWindowsPath(relative).parts)
    try:
        return target_key(TargetClass.RUNTIME, _contained(root, candidate, directory=True))
    except UNRESOLVED:
        return None if candidate.exists() else _NO_TARGET
