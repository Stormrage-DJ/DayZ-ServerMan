"""Build complete deterministic restore plans from verified archives and occupancy."""

import hashlib
from pathlib import PureWindowsPath

from ..domain.backups import manifest_digest
from ..domain.profile_restore_destinations import select_restore_destination, suggest_profile_id, suggest_restore_ports, reserve_profile_ports
from ..domain.profile_restore_mapping import map_mission_inventory, STORAGE_NAME, MISSION_OWNER_MARKER
from ..domain.profiles import ProfileInput, ProfileRecord, ProfileValidationError
from ..repositories.profile_restore_configuration import read_restore_configuration, transform_restore_configuration
from ..repositories.profile_restore_inventory import inventory
from .profile_restore_occupancy import inspect_restore_occupancy, _contained


def build_preview(directory, manifest, root, settings_revision, records, profile_root, request, ports):
    """Bind every allocation, consumer and target to a reviewable fingerprint."""
    metadata = manifest.reconstruction
    original = ProfileInput.parse(metadata["profile"])
    occupancy = inspect_restore_occupancy(root, records, ports)
    identifier = request["profile_id"] or suggest_profile_id(original.profile_id, occupancy)
    affected = consumers(root, records, metadata["resolved_mission_root"], metadata["instance_id"])
    mission = _contained(root, metadata["resolved_mission_root"])
    disk = inventory(mission)
    matches = common_inventory(disk) == archived_common(manifest)
    destination = select_restore_destination(metadata["resolved_mission_root"], metadata["instance_id"], identifier, occupancy,
                                             storage_policy=request["storage_policy"], affected_profile_ids=affected,
                                             common_mission_matches=matches)
    config_bytes = (directory / metadata["config_entry"]).read_bytes()
    config = read_restore_configuration(config_bytes)
    game, query = suggest_restore_ports(original.game_port, config.steam_query_port or 27016, occupancy.reserved_ports)
    game = request["game_port"] if request["game_port"] is not None else game
    query = request["steam_query_port"] if request["steam_query_port"] is not None else query
    if reserve_profile_ports(game, query) & occupancy.reserved_ports:
        raise ProfileValidationError("Restore ports conflict with registered profiles or bound UDP endpoints.")
    values = dict(ProfileRecord(0, original).fields())
    values.update(profile_id=identifier, display_name=request["display_name"] or original.display_name,
                  server_config=f"serverman\\{identifier}\\serverDZ.cfg", runtime_profile=f"serverman\\{identifier}\\profile",
                  mission_root=destination.mission_root, game_port=game)
    profile = ProfileInput.parse(values)
    transformed = transform_restore_configuration(config_bytes, mission_template=PureWindowsPath(destination.mission_root).name,
                                                   instance_id=destination.instance_id, steam_query_port=query,
                                                   display_name=profile.display_name if profile.display_name != original.display_name else None)
    prefix = metadata["mission_prefix"]
    relative = tuple(entry.path[len(prefix):] for entry in manifest.entries if entry.path.startswith(prefix))
    relative += tuple(path[len(prefix):] for path in manifest.directories if path.startswith(prefix))
    mapping = map_mission_inventory(relative, metadata["instance_id"], destination.instance_id)
    replacement = destination.storage_policy == "replace_existing"
    mission_target = _contained(root, destination.mission_root)
    target = mission_target / f"storage_{destination.instance_id}" if replacement else mission_target
    generated = _contained(root, f"serverman\\{identifier}")
    record_path = profile_root / f"{identifier}.json"
    target_inventory = inventory(target)
    if replacement and target_inventory is not None and target_inventory["type"] != "directory":
        raise ProfileValidationError("World storage target is not a directory.")
    missing_mods = [mod.directory for mod in profile.mods if not _contained(root, mod.directory).is_dir()]
    preview = {"backup_id": manifest.backup_id, "manifest_digest": manifest.manifest_digest,
               "profile": ProfileRecord(0, profile).to_dict(), "mission_root": destination.mission_root,
               "instance_id": destination.instance_id, "storage_policy": destination.storage_policy,
               "game_port": game, "steam_query_port": query,
               "affected_profile_ids": list(affected) if replacement else [], "missing_mods": missing_mods,
               "server_executable_present": _contained(root, profile.server_executable).is_file(),
               "settings_revision": settings_revision,
               "warnings": (["Missing mods must be installed from Mods before starting."] if missing_mods else []),
               "request": dict(request)}
    if replacement and not metadata["selected_storage_present"]:
        preview["warnings"].append("The selected world was absent in this backup. Replacement removes the existing selected world and retains a recovery copy.")
    facts = {"preview": preview, "root": str(root), "profiles": [record.to_dict() for record in records],
             "profile_inventory": inventory(profile_root), "generated": inventory(generated),
             "target": target_inventory, "common_mission": common_inventory(disk),
             "archive_sha256": hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest(),
             "transformed_config_sha256": hashlib.sha256(transformed).hexdigest()}
    if facts["generated"] is not None or inventory(record_path) is not None:
        raise ProfileValidationError("Restore profile ID or generated directory is occupied.")
    preview["preview_fingerprint"] = manifest_digest(facts)
    return preview, profile, transformed, mapping, target_inventory


def consumers(root, records, mission, instance):
    """Derive the exact registered consumer set from every validated config."""
    result = []
    for record in records:
        config = read_restore_configuration(_contained(root, record.values.server_config).read_bytes())
        resolved = record.values.mission_root or str(PureWindowsPath("mpmissions", config.mission_template))
        if resolved.casefold() == mission.casefold() and config.instance_id == instance:
            result.append(record.values.profile_id)
    return tuple(sorted(result))


def common_inventory(value):
    """Compare common mission bytes and empty directories excluding worlds and ownership."""
    if value is None or value["type"] != "directory":
        return None
    def common(path):
        """Keep only paths outside positive storage trees and the ownership marker."""
        head = path.split("/")[0]
        match = STORAGE_NAME.fullmatch(head)
        return head.casefold() != MISSION_OWNER_MARKER and not (match and int(match.group(1)) > 0)
    return {"directories": sorted((path for path in value["directories"] if common(path)), key=str.casefold),
            "files": {path: item for path, item in value["files"].items() if common(path)}}


def archived_common(manifest):
    """Project the signed complete mission inventory into a common-content comparison."""
    prefix = manifest.reconstruction["mission_prefix"]
    return common_inventory({"type": "directory",
        "directories": [path[len(prefix):] for path in manifest.directories if path.startswith(prefix)],
        "files": {entry.path[len(prefix):]: {"size": entry.size, "sha256": entry.sha256} for entry in manifest.entries if entry.path.startswith(prefix)}})
