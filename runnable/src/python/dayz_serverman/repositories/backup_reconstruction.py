"""Capture directory identities and verify reconstruction against archived bytes."""

import os
from pathlib import PureWindowsPath

from ..domain.backups import entry_path_key
from .backup_sources import BackupSourceError, _contained, _reject_node, safe_directory
from .profile_restore_configuration import read_restore_configuration


def directory_inventory(dayz_root, relative, prefix):
    """Capture every safe directory, including the selected tree's empty root."""
    root = safe_directory(dayz_root, "DayZ root", writable=False)
    directory = _contained(root, root.joinpath(*PureWindowsPath(relative).parts), file=False)
    result = [prefix.rstrip("/")]
    def failed(error):
        """Fail closed when a directory cannot be enumerated."""
        raise BackupSourceError("BACKUP_SOURCE_INVALID", "A source directory is inaccessible.") from error
    for parent, directories, _files in os.walk(directory, onerror=failed, followlinks=False):
        for name in directories:
            node = directory.__class__(parent) / name
            _reject_node(node)
            result.append(prefix.rstrip("/") + "/" + node.relative_to(directory).as_posix())
    return tuple(sorted(result, key=entry_path_key))


def verify_reconstruction_bytes(manifest, config_bytes):
    """Reject validly signed metadata that contradicts its configuration payload."""
    if manifest.schema_version != 3:
        return
    configuration = read_restore_configuration(config_bytes)
    metadata = manifest.reconstruction
    if configuration.mission_template != metadata["mission_template"] or configuration.instance_id != metadata["instance_id"]:
        raise ValueError("Backup configuration disagrees with reconstruction metadata.")
