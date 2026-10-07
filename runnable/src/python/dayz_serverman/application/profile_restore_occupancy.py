"""Inspect restore collisions conservatively without changing installation data."""

from __future__ import annotations

import stat
from pathlib import Path, PureWindowsPath

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.profile_restore_destinations import RestoreOccupancy, reserve_profile_ports
from ..domain.profile_restore_mapping import STORAGE_NAME
from ..domain.profiles import ProfileRecord, ProfileValidationError, validate_relative_path
from ..repositories.configuration_common import ConfigurationFileError
from ..repositories.profile_restore_configuration import read_restore_configuration


def inspect_restore_occupancy(dayz_root: Path, records: tuple[ProfileRecord, ...],
                              bound_udp_ports: frozenset[int]) -> RestoreOccupancy:
    """Read all profile configs and installed/custom missions; fail on unknown ownership."""
    if not dayz_root.is_absolute() or not _safe_exists(dayz_root) or not dayz_root.is_dir():
        raise ProfileValidationError("DayZ root is unavailable or unsafe for restore mapping")
    root = dayz_root.resolve(strict=True)
    mission_paths: set[str] = set()
    referenced: set[str] = set()
    instances: set[int] = set()
    ports = set(bound_udp_ports)
    for port in ports:
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ProfileValidationError("bound UDP port inventory is invalid")
    try:
        # Every config must be readable: one unknown consumer invalidates allocation.
        for record in records:
            config = _contained(root, record.values.server_config)
            context = read_restore_configuration(read_bytes_shared(config))
            mission = record.values.mission_root or str(
                PureWindowsPath("mpmissions", context.mission_template),
            )
            if PureWindowsPath(mission).name.casefold() != context.mission_template.casefold():
                raise ProfileValidationError("profile mission and config template disagree")
            referenced.add(mission)
            instances.add(context.instance_id)
            ports.update(reserve_profile_ports(record.values.game_port, context.steam_query_port or 27016))
        # Include orphan installed worlds and registered missions outside mpmissions.
        missions_root = _contained(root, "mpmissions")
        if _safe_exists(missions_root):
            for child in _children(missions_root):
                mission_paths.add(str(child.relative_to(root)))
        for relative in (*mission_paths, *referenced):
            mission = _contained(root, relative)
            if not _safe_exists(mission):
                continue
            for child in _children(mission):
                match = STORAGE_NAME.fullmatch(child.name)
                if match is not None and int(match.group(1)) > 0:
                    # Occupied names count even if a corrupt storage target is a file.
                    instances.add(int(match.group(1)))
        generated = _contained(root, "serverman")
        generated_ids = tuple(child.name for child in _children(generated)) if _safe_exists(generated) else ()
    except (OSError, ConfigurationFileError) as error:
        raise ProfileValidationError(
            "profile configs and mission storage must be readable before restore mapping",
        ) from error
    return RestoreOccupancy(
        tuple(record.values.profile_id for record in records), generated_ids,
        tuple(sorted(mission_paths, key=str.casefold)), tuple(sorted(referenced, key=str.casefold)),
        tuple(sorted(instances)), frozenset(ports),
    )


def _contained(root: Path, relative: str) -> Path:
    """Resolve a validated relative target without following links or reparse points."""
    normalized = validate_relative_path(relative, "restore occupancy path")
    assert normalized is not None
    target = root.joinpath(*PureWindowsPath(normalized).parts)
    _safe_exists(target)
    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ProfileValidationError("restore occupancy path escapes the DayZ root")
    return resolved


def _children(directory: Path) -> tuple[Path, ...]:
    """Enumerate every child and reject unsafe candidates instead of silently skipping them."""
    if not directory.is_dir():
        raise ProfileValidationError("restore occupancy directory is not readable")
    children = tuple(sorted(directory.iterdir(), key=lambda path: path.name.casefold()))
    for child in children:
        _safe_exists(child)
    return children


def _safe_exists(path: Path) -> bool:
    """Distinguish absence from unreadable data and reject even dangling links."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ProfileValidationError("restore occupancy path contains a link or reparse point")
    return True
