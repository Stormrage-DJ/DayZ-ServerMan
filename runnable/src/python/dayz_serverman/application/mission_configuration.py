"""Profile-scoped mission configuration workflow."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, Callable

from ..adapters.windows.shared_files import read_text_shared
from ..domain.mission_configuration import (
    MissionValidationError, validate_current_state, validate_mission_updates,
    validate_proposed_state,
)
from ..domain.models import RevisionConflict
from ..repositories.atomic_file import AtomicFilePublisher, ContentChangedError
from ..repositories.configuration_common import digest_bytes
from ..repositories.mission_files import (
    TARGET_PATHS, convert_starter_file, load_mission_file, transform_mission_file,
)
from .profiles import ProfileService
from .settings import SettingsService, SettingsValidationError

# Lowercase SHA-256 hex digest expected from mission file proofs
DIGEST = re.compile(r"[0-9a-f]{64}")
# Matches the template assignment inside a DayZ server config
MISSION_TEMPLATE = re.compile(r"\btemplate\s*=\s*[\"']([^\"']+)[\"']\s*;", re.IGNORECASE)
# Accepts only plain template file names such as dayzOffline.chernarusplus
SAFE_MISSION_TEMPLATE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


class MissionPathError(RuntimeError):
    """Raised when a mission target cannot be resolved safely."""
    pass


class MissionConfigurationService:
    """Load, preview, and publish profile-scoped mission file changes."""

    def __init__(self, profiles: ProfileService, settings: SettingsService, publisher: AtomicFilePublisher | None = None) -> None:
        """Store collaborators and default the atomic file publisher."""
        self._profiles = profiles
        self._settings = settings
        self._publisher = publisher or AtomicFilePublisher()

    def load(self, profile_id: object, target: str) -> dict[str, Any]:
        """Return the persisted mission file values with their proofs."""
        # Resolve the profile and its contained mission file first
        profile, settings_revision, relative, path = self._resolve(profile_id, target)
        snapshot = load_mission_file(path, target)
        # Validate the persisted values before returning them to the interface
        _validate_persisted(target, snapshot["values"])
        result = {
            "profile_id": profile.values.profile_id,
            "target": target,
            "relative_path": relative,
            "profile_revision": profile.revision,
            "settings_revision": settings_revision,
            "digest": snapshot["digest"],
            "values": snapshot["values"],
            "adoption_required": bool(snapshot.get("adoption_required", False)),
        }
        if target == "starter_loadout":
            # Starter loadouts additionally expose their legacy-conversion state
            result["conversion_required"] = bool(snapshot.get("conversion_required", False))
            result["conversion"] = snapshot.get("conversion")
        return result

    def preview(self, profile_id: object, target: str, expected_profile_revision: object,
                expected_settings_revision: object, expected_digest: object,
                raw_updates: object) -> dict[str, Any]:
        """Validate proposed updates and return the change preview."""
        # Validate the requested field updates before touching the file
        updates = validate_mission_updates(target, raw_updates)
        # Resolve the target and prove both revisions still match
        profile, settings_revision, relative, path = self._resolve(profile_id, target)
        _revision("profile", expected_profile_revision, profile.revision)
        _revision("settings", expected_settings_revision, settings_revision)
        digest = _digest(expected_digest)
        snapshot = load_mission_file(path, target)
        _validate_persisted(target, snapshot["values"])
        # Require the loaded digest so a concurrent edit cannot be overwritten
        if snapshot["digest"] != digest:
            raise ContentChangedError("mission file changed after it was loaded")
        validate_proposed_state(target, snapshot["values"], updates)
        proposed = transform_mission_file(snapshot, target, updates)
        return {
            "profile_id": profile.values.profile_id,
            "target": target,
            "relative_path": relative,
            "profile_revision": profile.revision,
            "settings_revision": settings_revision,
            "expected_digest": digest,
            "proposed_digest": digest_bytes(proposed),
            "changed_fields": _summary(updates),
            "adopts_marker_region": bool(snapshot.get("adoption_required", False)),
        }

    def apply(self, profile_id: object, target: str, expected_profile_revision: object,
              expected_settings_revision: object, expected_digest: object,
              raw_updates: object, checkpoint: Callable[[str, int], None]) -> dict[str, Any]:
        """Publish validated mission updates under a digest guard."""
        # Validate updates and re-prove revisions and digest before publishing
        updates = validate_mission_updates(target, raw_updates)
        profile, settings_revision, relative, path = self._resolve(profile_id, target)
        _revision("profile", expected_profile_revision, profile.revision)
        _revision("settings", expected_settings_revision, settings_revision)
        digest = _digest(expected_digest)
        snapshot = load_mission_file(path, target)
        checkpoint("loaded", 20)
        _validate_persisted(target, snapshot["values"])
        # Publish only while the reviewed digest still matches
        if snapshot["digest"] != digest:
            raise ContentChangedError("mission file changed after it was loaded")
        validate_proposed_state(target, snapshot["values"], updates)
        proposed = transform_mission_file(snapshot, target, updates)
        checkpoint("validated", 60)
        published = self._publisher.publish(path, proposed, digest)
        checkpoint("published", 90)
        # Re-read the file so a failed swap cannot be reported as success
        if load_mission_file(path, target)["digest"] != published:
            raise OSError("published mission file failed verification")
        checkpoint("verified", 100)
        return {"profile_id": profile.values.profile_id, "target": target,
                "relative_path": relative, "digest": published,
                "changed_fields": _summary(updates)}

    def convert_starter(self, profile_id: object, expected_profile_revision: object,
                        expected_settings_revision: object, expected_digest: object,
                        checkpoint: Callable[[str, int], None]) -> dict[str, Any]:
        """Convert a legacy starter loadout and publish the current form."""
        # Resolve the starter file and re-prove revisions and digest
        profile, settings_revision, relative, path = self._resolve(profile_id, "starter_loadout")
        _revision("profile", expected_profile_revision, profile.revision)
        _revision("settings", expected_settings_revision, settings_revision)
        digest = _digest(expected_digest)
        snapshot = load_mission_file(path, "starter_loadout")
        checkpoint("loaded", 20)
        if snapshot["digest"] != digest:
            raise ContentChangedError("mission file changed after it was loaded")
        # Require the conversion flag recorded when the file was loaded
        if not snapshot.get("conversion_required"):
            raise ConfigurationFileError("init.c has no recognizable legacy starter loadout to convert")
        original_items = list(snapshot["values"]["items"])
        proposed = convert_starter_file(snapshot)
        checkpoint("validated", 60)
        published = self._publisher.publish(path, proposed, digest)
        checkpoint("published", 90)
        verified = load_mission_file(path, "starter_loadout")
        # The converted file must drop the legacy flag and keep the same items
        if (verified["digest"] != published or verified.get("conversion_required")
                or verified["values"]["items"] != original_items):
            raise OSError("converted starter loadout failed verification")
        checkpoint("verified", 100)
        return {"profile_id": profile.values.profile_id, "target": "starter_loadout",
                "relative_path": relative, "digest": published,
                "items": original_items}

    def _resolve(self, profile_id: object, target: str) -> tuple[Any, int, str, Path]:
        """Resolve a supported mission target and prove it stays in the root."""
        # Reject unsupported targets before touching profile state
        if target not in TARGET_PATHS:
            raise MissionPathError("mission target is not supported")
        profile = self._profiles.read(profile_id)
        settings = self._settings.load()
        # Require a configured DayZ root so containment can be proven
        if settings.dayz_root is None or settings.revision is None:
            raise SettingsValidationError("DayZ root is not configured")
        root = Path(settings.dayz_root).resolve(strict=True)
        mission_root, mission = resolve_profile_mission(root, profile)
        # Prove the mission file never escapes the resolved mission folder
        path = _contained(mission, mission / TARGET_PATHS[target], directory=False)
        relative = str(Path(mission_root) / TARGET_PATHS[target])
        return profile, settings.revision, relative, path


def resolve_profile_mission(root: Path, profile: Any) -> tuple[str, Path]:
    """Resolve an explicit mission root or infer it from the profile's server config."""
    relative = profile.values.mission_root
    if relative is None:
        # Fall back to the mission template named in the server config
        config = _contained(root, root / profile.values.server_config, directory=False)
        match = MISSION_TEMPLATE.search(read_text_shared(config, encoding="utf-8", errors="replace"))
        template = match.group(1).strip() if match else ""
        # Only plain template names may become part of the mission path
        if SAFE_MISSION_TEMPLATE.fullmatch(template) is None:
            raise MissionPathError("server config has no safe mission template")
        relative = str(Path("mpmissions") / template)
    return relative, _contained(root, root / relative, directory=True)


def _contained(root: Path, candidate: Path, *, directory: bool) -> Path:
    """Prove a candidate path stays inside the DayZ root without reparse points."""
    # Walk the lexical path and refuse symlinks or reparse points
    lexical = candidate if directory else candidate.parent
    while lexical != root.parent:
        if lexical.exists():
            info = os.lstat(lexical)
            attrs = getattr(info, "st_file_attributes", 0)
            if lexical.is_symlink() or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise MissionPathError("mission target crosses a reparse point")
        if lexical == root:
            break
        lexical = lexical.parent
    # Resolve strictly and require the real path to sit under the root
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise MissionPathError("mission target escapes or is missing from the DayZ root") from error
    # Re-check the resolved chain for reparse points
    current = resolved if directory else resolved.parent
    while current != root:
        attrs = getattr(os.lstat(current), "st_file_attributes", 0)
        if current.is_symlink() or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise MissionPathError("mission target crosses a reparse point")
        current = current.parent
    # Enforce the expected file type for the resolved target
    if (directory and not resolved.is_dir()) or (not directory and not resolved.is_file()):
        raise MissionPathError("mission target has the wrong file type")
    return resolved


def _revision(name: str, value: object, current: int) -> None:
    """Reject a revision proof that does not match the current value."""
    if not isinstance(value, int) or isinstance(value, bool) or value != current:
        raise RevisionConflict(f"expected {name} revision does not match")


def _digest(value: object) -> str:
    """Return a validated lowercase SHA-256 mission digest proof."""
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise ContentChangedError("expected mission file digest is invalid")
    return value


def _summary(updates: dict[str, Any]) -> list[str]:
    """Return the changed field names in interface-friendly form."""
    if "events" in updates:
        # Event updates summarize nested event and field pairs
        return [f"{name}.{field}" for name, fields in updates["events"].items() for field in fields]
    return list(updates)


def _validate_persisted(target: str, values: dict[str, Any]) -> None:
    """Reject files whose persisted values fail domain validation."""
    try:
        validate_current_state(target, values)
    except MissionValidationError as error:
        raise ConfigurationFileError("persisted mission configuration is invalid") from error
