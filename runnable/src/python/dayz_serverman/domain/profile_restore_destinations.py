"""Select deterministic restore destinations from a fully inspected installation."""

from __future__ import annotations

from dataclasses import dataclass

from .profile_restore_mapping import isolated_mission_path, positive_instance_id
from .profiles import ProfileValidationError, validate_profile_id, validate_relative_path


@dataclass(frozen=True)
class RestoreOccupancy:
    """Read-only facts supplied after safe catalog, config and disk inspection."""

    profile_ids: tuple[str, ...] = ()
    generated_ids: tuple[str, ...] = ()
    mission_paths: tuple[str, ...] = ()
    referenced_missions: tuple[str, ...] = ()
    instance_ids: tuple[int, ...] = ()
    reserved_ports: frozenset[int] = frozenset()
    ownership_known: bool = True


@dataclass(frozen=True)
class RestoreDestination:
    """The selected identity and mission/storage mapping, with no publication authority."""

    profile_id: str
    mission_root: str
    instance_id: int
    storage_policy: str


def suggest_profile_id(original: str, occupancy: RestoreOccupancy) -> str:
    """Suggest the original ID or first valid free restored suffix, within 64 characters."""
    identifier = validate_profile_id(original)
    occupied = {item.casefold() for item in (*occupancy.profile_ids, *occupancy.generated_ids)}
    if identifier.casefold() not in occupied:
        return identifier
    sequence = 1
    while True:
        suffix = "-restored" if sequence == 1 else f"-restored-{sequence}"
        candidate = identifier[:64 - len(suffix)] + suffix
        validate_profile_id(candidate)
        if candidate.casefold() not in occupied:
            return candidate
        sequence += 1


def select_restore_destination(original_mission: str, original_instance: int,
                               profile_id: str, occupancy: RestoreOccupancy, *,
                               storage_policy: str | None = None,
                               affected_profile_ids: tuple[str, ...] = (),
                               common_mission_matches: bool = False) -> RestoreDestination:
    """Choose absent originals or isolation; require proven consumers for replacement."""
    identifier = validate_profile_id(profile_id)
    mission = validate_relative_path(original_mission, "mission_root")
    assert mission is not None
    instance = positive_instance_id(original_instance)
    if not occupancy.ownership_known:
        raise ProfileValidationError("existing profile configs must be readable before restore mapping")
    occupied_ids = {item.casefold() for item in (*occupancy.profile_ids, *occupancy.generated_ids)}
    if identifier.casefold() in occupied_ids:
        raise ProfileValidationError("restore profile ID or generated folder is already occupied")
    missions = {_mission_key(item) for item in (*occupancy.mission_paths, *occupancy.referenced_missions)}
    collision = _mission_key(mission) in missions
    policy = storage_policy or ("allocate_new" if collision else "preserve_original")
    if policy == "preserve_original":
        if collision:
            raise ProfileValidationError("original mission is occupied; select isolation or cancel")
    elif policy == "allocate_new":
        mission = isolated_mission_path(identifier, mission, tuple(missions))
        used = {positive_instance_id(item) for item in occupancy.instance_ids}
        instance = 1
        while instance in used:
            instance += 1
    elif policy == "replace_existing":
        if not collision or not affected_profile_ids:
            raise ProfileValidationError("storage replacement requires known registered consumers")
        if not common_mission_matches:
            raise ProfileValidationError("common mission content differs; select isolation")
        # Caller must derive this exact consumer set from inspected configs, not UI input.
        registered = {item.casefold() for item in occupancy.profile_ids}
        if any(item.casefold() not in registered for item in affected_profile_ids):
            raise ProfileValidationError("storage replacement consumer is not registered")
    else:
        raise ProfileValidationError("restore storage policy is unsupported")
    return RestoreDestination(identifier, mission, instance, policy)


def reserve_profile_ports(game_port: int, steam_query_port: int | None) -> frozenset[int]:
    """Reserve the manager's conservative four-port game block and explicit query port."""
    _validate_port(game_port)
    if game_port > 65532:
        raise ProfileValidationError("game port block exceeds 65535")
    ports = set(range(game_port, game_port + 4))
    if steam_query_port is not None:
        _validate_port(steam_query_port)
        ports.add(steam_query_port)
    return frozenset(ports)


def suggest_restore_ports(game_port: int, steam_query_port: int,
                          occupied: frozenset[int]) -> tuple[int, int]:
    """Keep free archived ports or suggest the first free block at 100-port intervals."""
    _validate_port(game_port)
    _validate_port(steam_query_port)
    for port in occupied:
        _validate_port(port)
    if game_port <= 65532 and not reserve_profile_ports(game_port, steam_query_port) & occupied:
        return game_port, steam_query_port
    for candidate in range(2302, 65533, 100):
        if not reserve_profile_ports(candidate, candidate + 3) & occupied:
            return candidate, candidate + 3
    raise ProfileValidationError("no free restore port block is available")


def _validate_port(value: object) -> None:
    """Validate integer UDP port bounds before building reservations."""
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise ProfileValidationError("restore port must be from 1 through 65535")


def _mission_key(value: str) -> str:
    """Use the same Windows path normalization for disk and catalog facts."""
    normalized = validate_relative_path(value, "mission_root")
    assert normalized is not None
    return normalized.casefold()
