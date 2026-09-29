"""Profile-scoped shared DayZ configuration workflows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..domain.configuration import describe_fields, validate_updates
from ..domain.models import RevisionConflict
from ..domain.profiles import ProfileRecord
from ..repositories.atomic_file import AtomicFilePublisher, ContentChangedError
from ..repositories.configuration_files import (
    ConfigurationFileError,
    ConfigurationSnapshot,
    digest_bytes,
    load_configuration_file,
    transform_configuration,
)
from .profiles import ProfileService
from .settings import SettingsService, SettingsValidationError
from .mission_configuration import MissionPathError, resolve_profile_mission


# SHA-256 digests are lower-case hexadecimal with exactly 64 characters
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


class ConfigurationPathError(RuntimeError):
    """Raised when a configuration target escapes the root or is not a file."""
    pass


class GameplayPrerequisiteError(RuntimeError):
    """Raised when the server configuration does not enable gameplay editing."""
    pass


class ConfigurationService:
    """Load, preview, and atomically apply profile-scoped configuration edits."""

    def __init__(
        self,
        profiles: ProfileService,
        settings: SettingsService,
        publisher: AtomicFilePublisher | None = None,
    ) -> None:
        """Bind profile, settings, and the publisher used for atomic writes."""
        self._profiles = profiles
        self._settings = settings
        self._publisher = publisher or AtomicFilePublisher()

    def load(self, profile_id: object, target: str) -> dict[str, Any]:
        """Return the current configuration view for one target."""
        # Resolve the profile, its revision, and the contained target file
        profile, settings_revision, root, path, relative = self._resolve(profile_id, target)
        # Compute the linked server digest that gameplay editing depends on
        prerequisite = self._gameplay_prerequisite(profile, root, target, None)
        # Read the file and project its described fields
        snapshot = load_configuration_file(path, target)
        return self._view(profile, settings_revision, relative, snapshot, prerequisite)

    def preview(
        self,
        profile_id: object,
        target: str,
        expected_profile_revision: object,
        expected_settings_revision: object,
        expected_digest: object,
        expected_server_digest: object,
        raw_updates: object,
    ) -> dict[str, Any]:
        """Validate proposed updates and return the projected change without writing."""
        # Validate and normalize the proposed field updates
        updates = validate_updates(target, raw_updates)
        # Resolve the target file together with its current revisions
        profile, settings_revision, root, path, relative = self._resolve(profile_id, target)
        # Require the revisions and digest the caller loaded
        _require_revision("profile", expected_profile_revision, profile.revision)
        _require_revision("settings", expected_settings_revision, settings_revision)
        digest = _require_digest(expected_digest)
        expected_server = _edit_server_digest(target, expected_server_digest)
        prerequisite = self._gameplay_prerequisite(
            profile,
            root,
            target,
            expected_server,
        )
        # Refuse projection when the file changed after it was loaded
        snapshot = load_configuration_file(path, target)
        if snapshot.digest != digest:
            raise ContentChangedError("configuration changed after it was loaded")
        # Project the updated bytes and describe the resulting fields
        proposed = transform_configuration(snapshot, updates)
        values = {**snapshot.values, **updates}
        return {
            "profile_id": profile.values.profile_id,
            "target": target,
            "relative_path": relative,
            "profile_revision": profile.revision,
            "settings_revision": settings_revision,
            "expected_digest": snapshot.digest,
            "server_config_digest": prerequisite,
            "proposed_digest": digest_bytes(proposed),
            "changed_fields": list(updates),
            "fields": describe_fields(target, values),
        }

    def apply(
        self,
        profile_id: object,
        target: str,
        expected_profile_revision: object,
        expected_settings_revision: object,
        expected_digest: object,
        expected_server_digest: object,
        raw_updates: object,
        checkpoint: Callable[[str, int], None],
    ) -> dict[str, Any]:
        """Atomically publish validated updates and return the published digest."""
        # Validate and normalize the proposed field updates
        updates = validate_updates(target, raw_updates)
        # Resolve the target file together with its current revisions
        profile, settings_revision, root, path, relative = self._resolve(profile_id, target)
        # Require the revisions and digest the caller loaded
        _require_revision("profile", expected_profile_revision, profile.revision)
        _require_revision("settings", expected_settings_revision, settings_revision)
        digest = _require_digest(expected_digest)
        expected_server = _edit_server_digest(target, expected_server_digest)
        prerequisite = self._gameplay_prerequisite(
            profile,
            root,
            target,
            expected_server,
        )
        # Refuse publication when the file changed after it was loaded
        snapshot = load_configuration_file(path, target)
        checkpoint("loaded", 20)
        if snapshot.digest != digest:
            raise ContentChangedError("configuration changed after it was loaded")
        # Transform, re-verify the prerequisite, and publish atomically
        proposed = transform_configuration(snapshot, updates)
        checkpoint("validated", 60)
        self._gameplay_prerequisite(profile, root, target, prerequisite)
        published_digest = self._publisher.publish(path, proposed, snapshot.digest)
        checkpoint("verified", 100)
        return {
            "profile_id": profile.values.profile_id,
            "target": target,
            "relative_path": relative,
            "digest": published_digest,
            "changed_fields": list(updates),
        }

    def _resolve(
        self,
        profile_id: object,
        target: str,
    ) -> tuple[ProfileRecord, int, Path, Path, str]:
        """Resolve the profile, settings revision, root, and contained target file."""
        # Load the profile and settings needed to locate the target
        profile = self._profiles.read(profile_id)
        settings = self._settings.load()
        if settings.dayz_root is None or settings.revision is None:
            raise SettingsValidationError("DayZ root is not configured")
        root = Path(settings.dayz_root).resolve(strict=False)
        # Map the target name to its profile-relative path
        if target == "server":
            relative = profile.values.server_config
        elif target == "gameplay":
            try:
                mission_root, _mission = resolve_profile_mission(root, profile)
            except MissionPathError as error:
                raise ConfigurationPathError(str(error)) from error
            relative = str(Path(mission_root) / "cfgGameplay.json")
        else:
            # Unknown targets fail domain validation with a descriptive message
            validate_updates(target, {})
            raise AssertionError("unreachable")
        # Confirm the root exists and the target stays inside it
        if not root.is_dir():
            raise ConfigurationPathError("configured DayZ root is not a directory")
        candidate = _contained_file(root, relative)
        return profile, settings.revision, root, candidate, relative

    @staticmethod
    def _gameplay_prerequisite(
        profile: ProfileRecord,
        root: Path,
        target: str,
        expected_digest: object,
    ) -> str | None:
        """Enforce the gameplay toggle and return the verified server digest."""
        # Non-gameplay targets must not carry a server digest at all
        if target != "gameplay":
            if expected_digest is not None:
                raise ContentChangedError("server configuration digest is not valid for this target")
            return None
        digest = _require_digest(expected_digest) if expected_digest is not None else None
        # Load the server config and require the gameplay file toggle
        server_path = _contained_file(root, profile.values.server_config)
        snapshot = load_configuration_file(server_path, "server")
        if snapshot.values.get("enableCfgGameplayFile") is not True:
            raise GameplayPrerequisiteError(
                "Enable the gameplay configuration file in Server settings before editing Gameplay settings."
            )
        # Reject a stale server digest so gameplay edits stay consistent
        if digest is not None and snapshot.digest != digest:
            raise ContentChangedError("server configuration changed after Gameplay settings were loaded")
        return snapshot.digest

    @staticmethod
    def _view(
        profile: ProfileRecord,
        settings_revision: int,
        relative: str,
        snapshot: ConfigurationSnapshot,
        prerequisite: str | None,
    ) -> dict[str, Any]:
        """Shape a loaded snapshot into the bridge view with revisions and digests."""
        return {
            "profile_id": profile.values.profile_id,
            "target": snapshot.target,
            "relative_path": relative,
            "profile_revision": profile.revision,
            "settings_revision": settings_revision,
            "digest": snapshot.digest,
            "server_config_digest": prerequisite,
            "fields": describe_fields(snapshot.target, snapshot.values),
        }


def _contained_file(root: Path, relative: str) -> Path:
    """Resolve a profile-relative file and reject escapes or missing targets."""
    candidate = (root / relative).resolve(strict=False)
    # Require the resolved path to stay under the DayZ root
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ConfigurationPathError("configuration target escapes the DayZ root") from error
    # Require the target to be an existing file
    if not candidate.is_file():
        raise ConfigurationPathError("configuration target is not an existing file")
    return candidate


def _require_revision(name: str, value: object, current: int) -> None:
    """Validate an expected revision value against the current revision."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RevisionConflict(f"expected {name} revision is invalid")
    if value != current:
        raise RevisionConflict(f"expected {name} revision {value}, current revision is {current}")


def _require_digest(value: object) -> str:
    """Return the value only when it is a lower-case SHA-256 digest."""
    if not isinstance(value, str) or DIGEST_PATTERN.fullmatch(value) is None:
        raise ContentChangedError("expected configuration digest is invalid")
    return value


def _edit_server_digest(target: str, value: object) -> str | None:
    """Return the gameplay server digest or require server edits to carry none."""
    if target == "gameplay":
        return _require_digest(value)
    if value is not None:
        raise ContentChangedError("server configuration digest is not valid for this target")
    return None
