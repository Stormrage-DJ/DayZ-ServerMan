"""Stage complete destination groups and verify retained recovery copies."""

import json
import os
import shutil
from pathlib import Path, PureWindowsPath
from typing import Any
from ..domain.backups import BackupManifest

from ..domain.profile_restore_mapping import MISSION_OWNER_MARKER
from .profile_restore_inventory import inventory, safe_exists
from .backup_verification import sha256_file


def group_paths(record: dict, group: dict, dayz_root: Path, profile_root: Path) -> tuple[Path, Path, Path]:
    """Derive local stage/holding names; never trust arbitrary journal paths."""
    root = profile_root if group["role"] == "profile" else dayz_root
    target = root.joinpath(*PureWindowsPath(group["relative"]).parts)
    if not target.is_relative_to(root) or target == root:
        raise ValueError("Restore target escapes its root.")
    safe_exists(target)
    tag = record["operation_id"] + "-" + group["role"]
    stage = target.parent / (".serverman-restore-" + tag + ".stage")
    holding = target.parent / (".serverman-restore-" + tag + ".holding")
    safe_exists(stage)
    safe_exists(holding)
    return target, stage, holding


def write_bytes(path: Path, payload: bytes) -> None:
    """Exclusively write and flush staged content before publication."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def stage_groups(record: dict[str, Any], directory: Path, manifest: BackupManifest, config: bytes,
                 mapping: dict[str, str], roots: tuple[Path, Path], recovery_root: Path) -> None:
    """Materialize transformed configuration, runtime, selected world and new record."""
    paths = [group_paths(record, group, *roots) for group in record["groups"]]
    generated = paths[0][1]
    mission = paths[1][1]
    generated.mkdir()
    replacement = record["result"]["storage_policy"] == "replace_existing"
    if not replacement or manifest.reconstruction["selected_storage_present"]:
        mission.mkdir()
    write_bytes(generated / "serverDZ.cfg", config)
    (generated / "profile").mkdir()
    for name in manifest.directories:
        if name.startswith("runtime-profile/"):
            generated.joinpath("profile", *name.split("/")[1:]).mkdir(parents=True, exist_ok=True)
    prefix = manifest.reconstruction["mission_prefix"]
    selected = f"storage_{record['result']['instance_id']}"
    for name in manifest.directories:
        if name.startswith(prefix):
            relative = name[len(prefix):]
            target = mapping.get(relative)
            if target is not None:
                parts = PureWindowsPath(target).parts
                if replacement:
                    if parts[0] != selected:
                        continue
                    parts = parts[1:]
                mission.joinpath(*parts).mkdir(parents=True, exist_ok=True)
    for entry in manifest.entries:
        if entry.path.startswith("runtime-profile/"):
            target = generated.joinpath("profile", *entry.path.split("/")[1:])
        elif entry.path.startswith(prefix):
            relative = entry.path[len(prefix):]
            mapped = mapping.get(relative)
            if mapped is None:
                continue
            parts = PureWindowsPath(mapped).parts
            if replacement:
                if parts[0] != selected:
                    continue
                parts = parts[1:]
            target = mission.joinpath(*parts)
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with (directory / entry.path).open("rb") as source, target.open("xb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if target.stat().st_size != entry.size or sha256_file(target) != entry.sha256:
            raise OSError("Staged restore payload failed verification.")
    if not replacement:
        marker = {"profile_id": record["profile_id"], "mission_root": record["result"]["mission_root"], "operation_id": record["operation_id"]}
        write_bytes(mission / MISSION_OWNER_MARKER, json.dumps(marker, sort_keys=True).encode("utf-8"))
    fields = dict(record["result"]["profile"])
    fields.pop("semantic_digest")
    fields["schema_version"] = 2
    write_bytes(paths[2][1], (json.dumps(fields, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    for group, (_target, stage, _holding) in zip(record["groups"], paths):
        group["after"] = inventory(stage)
        if group["before"] is not None:
            recovery = recovery_root / record["operation_id"] / group["role"]
            safe_exists(recovery)
            recovery.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(_target, recovery)
            if inventory(recovery) != group["before"]:
                raise OSError("Restore recovery copy failed verification.")
            group["recovery"] = str(recovery)


def remove_owned(path: Path) -> None:
    """Remove an inspected operation-private file or tree without traversing links."""
    value = inventory(path)
    if value is None:
        return
    if value["type"] == "directory":
        shutil.rmtree(path)
    else:
        path.unlink()
