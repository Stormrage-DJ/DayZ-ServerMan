"""Map only the selected archived world into a reviewed mission destination."""

from __future__ import annotations

import re
from pathlib import PureWindowsPath

from .profiles import ProfileValidationError, validate_profile_id, validate_relative_path

# Only top-level positive-integer storage trees represent separate worlds.
STORAGE_NAME = re.compile(r"storage_([0-9]+)", re.IGNORECASE)
MISSION_OWNER_MARKER = ".serverman-mission-owner.json"


def positive_instance_id(value: object) -> int:
    """Require a positive integer, excluding booleans at untrusted boundaries."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProfileValidationError("instance_id must be a positive integer")
    return value


def isolated_mission_path(profile_id: str, original_mission: str,
                         occupied_paths: tuple[str, ...]) -> str:
    """Allocate a private mission basename while preserving its final world suffix."""
    identifier = validate_profile_id(profile_id)
    original = validate_relative_path(original_mission, "mission_root")
    assert original is not None
    basename = PureWindowsPath(original).name
    if "." not in basename or not basename.rsplit(".", 1)[0]:
        raise ProfileValidationError("mission basename needs a world suffix for isolation")
    suffix = basename.rsplit(".", 1)[1]
    occupied = {_path_key(path) for path in occupied_paths}
    sequence = 1
    while True:
        # Numeric collision suffixes precede the world suffix used by DayZ.
        extension = "" if sequence == 1 else f"-{sequence}"
        candidate = str(PureWindowsPath("mpmissions", f"serverman-{identifier}{extension}.{suffix}"))
        if _path_key(candidate) not in occupied:
            return candidate
        sequence += 1


def map_mission_entry(relative_path: str, original_instance: int,
                      destination_instance: int) -> str | None:
    """Map mission-relative files or directories; omit other worlds and archived ownership."""
    original = positive_instance_id(original_instance)
    destination = positive_instance_id(destination_instance)
    normalized = validate_relative_path(relative_path, "mission entry")
    assert normalized is not None
    parts = PureWindowsPath(normalized).parts
    if len(parts) == 1 and parts[0].casefold() == MISSION_OWNER_MARKER:
        return None
    storage = STORAGE_NAME.fullmatch(parts[0])
    if storage is not None and int(storage.group(1)) > 0:
        if int(storage.group(1)) != original:
            return None
        # Copy binary payloads intact; only the containing directory changes.
        return str(PureWindowsPath(f"storage_{destination}", *parts[1:]))
    return normalized


def map_mission_inventory(paths: tuple[str, ...], original_instance: int,
                          destination_instance: int) -> dict[str, str]:
    """Produce a case-unique mapping for a validated file or directory inventory."""
    positive_instance_id(original_instance)
    positive_instance_id(destination_instance)
    mapped: dict[str, str] = {}
    seen_source: set[str] = set()
    seen_target: set[str] = set()
    for source in paths:
        source_key = _path_key(source)
        if source_key in seen_source:
            raise ProfileValidationError("mission inventory contains duplicate Windows paths")
        seen_source.add(source_key)
        target = map_mission_entry(source, original_instance, destination_instance)
        if target is None:
            continue
        key = _path_key(target)
        if key in seen_target:
            raise ProfileValidationError("mission mapping contains duplicate destination paths")
        seen_target.add(key)
        mapped[source] = target
    return mapped


def _path_key(path: str) -> str:
    """Normalize path separators and case before collision comparisons."""
    normalized = validate_relative_path(path, "mission path")
    assert normalized is not None
    return normalized.casefold()
