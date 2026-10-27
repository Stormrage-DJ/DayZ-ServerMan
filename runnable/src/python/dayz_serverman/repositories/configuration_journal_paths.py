"""Re-derive the two permitted publication targets and all owner artifacts."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from .backup_verification import path_has_reparse
from .configuration_common import digest_bytes
from .configuration_journal import ConfigurationJournal, successor_bytes
from .restore_paths import safe_root, safe_target


def profile_target(profiles: Path, profile_id: str) -> Path:
    """Bind one existing profile file below trusted manager profile storage."""
    from ..domain.profiles import validate_profile_id
    identifier = validate_profile_id(profile_id)
    target = profiles / (identifier + ".json")
    if path_has_reparse(target) or not target.is_file():
        raise ValueError("configuration profile target is unsafe or missing")
    return target


def native_target(root: Path, relative: str) -> Path:
    """Apply the existing strict local-root and plain-file path policy."""
    return safe_target(safe_root(root), PurePosixPath(relative.replace("\\", "/")))


def journal_paths_safe(journal: ConfigurationJournal, profiles: Path, root: Path, recovery: Path) -> bool:
    """Prove exact data targets and generated artifact locations before any recovery."""
    try:
        if str(safe_root(root)) != journal.dayz_root:
            return False
        profile = profile_target(profiles, journal.profile_id)
        group = journal.groups[0]
        native = native_target(root, journal.old_profile["server_config"])
        stage = native.with_name(f".{native.name}.{journal.operation_id}.configuration-stage")
        record_stage = profile.with_name(f".{profile.name}.{journal.operation_id}.configuration-stage")
        old_copy = recovery / journal.operation_id / "native.old"
        expected = ((group.target_path, native), (group.staging_path, stage),
                    (group.recovery_path, old_copy), (journal.profile_stage, record_stage))
        if any(not isinstance(actual, str) or Path(actual) != path or path_has_reparse(path)
               for actual, path in expected):
            return False
        # The journal may change only the port and advance exactly one revision.
        if digest_bytes(successor_bytes(journal.old_profile, journal.new_port)) != journal.new_profile_digest:
            return False
        return journal.new_port != journal.old_profile["game_port"]
    except (OSError, ValueError, TypeError, KeyError):
        return False


def artifacts_known(journal: ConfigurationJournal, recovery: Path) -> bool:
    """Prove every artifact before a stage can be replaced or recovery data removed."""
    from .restore_paths import matches
    group = journal.groups[0]
    expected = ((Path(group.staging_path), group.new_digest),
                (Path(journal.profile_stage), journal.new_profile_digest),
                (Path(group.recovery_path), group.old_digest))
    for path, digest in expected:
        if path.is_symlink() or path_has_reparse(path) or (path.exists() and not matches(path, digest)):
            return False
    folder = recovery / journal.operation_id
    if folder.exists():
        if folder.is_symlink() or path_has_reparse(folder) or not folder.is_dir():
            return False
        if any(path != Path(group.recovery_path) for path in folder.iterdir()):
            return False
    return True
