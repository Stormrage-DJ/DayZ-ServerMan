"""Reversible, profile-scoped legacy medical feature workflow."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.models import RevisionConflict
from ..repositories.atomic_file import AtomicFilePublisher, ContentChangedError
from ..repositories.configuration_common import digest_bytes
from ..repositories.medical_features import transform_medical_feature
from .mission_configuration import _contained, _digest, _revision, resolve_profile_mission
from .profiles import ProfileService
from .settings import SettingsService, SettingsValidationError

# Mission-relative file owned by each reversible feature
FEATURE_PATHS = {
    "medical_loot_zones": Path("mapgroupproto.xml"),
    "medical_item_spawns": Path("db/types.xml"),
}


class MedicalFeatureService:
    """Toggle legacy medical files between their original and managed states."""

    def __init__(self, profiles: ProfileService, settings: SettingsService,
                 baselines: Path, publisher: AtomicFilePublisher | None = None) -> None:
        """Bind profiles, settings, the baseline store, and the atomic publisher."""
        self._profiles, self._settings = profiles, settings
        self._baselines = baselines
        self._publisher = publisher or AtomicFilePublisher()

    def load(self, profile_id: object) -> dict[str, Any]:
        """Report each feature's enabled state, digest, and file path."""
        # Resolve the profile, settings, and mission for this query
        profile, settings_revision, mission_root, mission = self._context(profile_id)
        # Collect the current state of every reversible feature
        features = {}
        for feature, relative in FEATURE_PATHS.items():
            path = _contained(mission, mission / relative, directory=False)
            enabled, digest = self._state(profile.values.profile_id, feature, path)
            features[feature] = {"enabled": enabled, "digest": digest,
                                 "relative_path": str(Path(mission_root) / relative)}
        return {"profile_id": profile.values.profile_id, "profile_revision": profile.revision,
                "settings_revision": settings_revision, "features": features}

    def preview(self, profile_id: object, feature: str, enabled: object,
                expected_profile_revision: object, expected_settings_revision: object,
                expected_digest: object) -> dict[str, Any]:
        """Validate a toggle and return the projected digest without writing."""
        # Validate the request and resolve the target context
        context = self._validated_context(profile_id, feature, enabled, expected_profile_revision,
                                          expected_settings_revision, expected_digest)
        # Project the toggled bytes and describe the change
        proposed = self._proposed(*context, bool(enabled))
        return {"profile_id": context[0], "feature": feature, "enabled": bool(enabled),
                "expected_digest": context[3], "proposed_digest": digest_bytes(proposed),
                "changed_fields": [feature]}

    def apply(self, profile_id: object, feature: str, enabled: object,
              expected_profile_revision: object, expected_settings_revision: object,
              expected_digest: object, checkpoint: Callable[[str, int], None]) -> dict[str, Any]:
        """Toggle a feature, preserving its original bytes for reversal."""
        # Validate the request and resolve the target context
        context = self._validated_context(profile_id, feature, enabled, expected_profile_revision,
                                          expected_settings_revision, expected_digest)
        profile_key, _feature, path, digest, baseline = context
        checkpoint("loaded", 20)
        # Snapshot the original bytes once so the toggle stays reversible
        owned_baseline = self._baseline(profile_key, feature)
        if not owned_baseline.exists():
            self._write_baseline(owned_baseline, read_bytes_shared(baseline) if baseline.exists() else read_bytes_shared(path))
        # Project the managed bytes and publish them atomically
        proposed = self._proposed(profile_key, feature, path, digest, owned_baseline, bool(enabled))
        checkpoint("validated", 60)
        published = self._publisher.publish(path, proposed, digest)
        checkpoint("verified", 100)
        return {"profile_id": profile_key, "feature": feature, "enabled": bool(enabled),
                "digest": published, "changed_fields": [feature]}

    def _validated_context(self, profile_id: object, feature: str, enabled: object,
                           profile_revision: object, settings_revision: object,
                           expected_digest: object) -> tuple[str, str, Path, str, Path]:
        """Validate the toggle request and return the resolved target context."""
        # Require a known feature and an explicit on/off value
        if feature not in FEATURE_PATHS or not isinstance(enabled, bool):
            raise ValueError("medical feature request is invalid")
        profile, current_settings, _mission_root, mission = self._context(profile_id)
        # Enforce the revision guards and the expected digest
        _revision("profile", profile_revision, profile.revision)
        _revision("settings", settings_revision, current_settings)
        digest = _digest(expected_digest)
        # Locate the feature file inside the mission and read its current state
        path = _contained(mission, mission / FEATURE_PATHS[feature], directory=False)
        current_enabled, current_digest = self._state(profile.values.profile_id, feature, path)
        # Refuse stale digests and toggles that would change nothing
        if current_digest != digest:
            raise ContentChangedError("medical feature target changed after it was loaded")
        if current_enabled == enabled:
            raise ValueError("medical feature already has the requested state")
        return profile.values.profile_id, feature, path, digest, self._baseline_source(
            profile.values.profile_id, feature, path,
        )

    def _context(self, profile_id: object) -> tuple[Any, int, str, Path]:
        """Load the profile and settings and resolve the mission root safely."""
        # Load the profile and manager settings for this request
        profile = self._profiles.read(profile_id); settings = self._settings.load()
        # Require a configured, resolvable DayZ root before touching the mission
        if settings.dayz_root is None or settings.revision is None:
            raise SettingsValidationError("DayZ root is not configured")
        # Resolve the mission through the shared helper so links are rejected
        root = Path(settings.dayz_root).resolve(strict=True)
        mission_root, mission = resolve_profile_mission(root, profile)
        return profile, settings.revision, mission_root, mission

    def _state(self, profile_id: str, feature: str, path: Path) -> tuple[bool, str]:
        """Classify the file as original or managed against the stored baseline."""
        # Read the current bytes and locate the baseline file
        current = read_bytes_shared(path); current_digest = digest_bytes(current)
        baseline = self._baseline_source(profile_id, feature, path)
        # Without a baseline the file is assumed original
        if not baseline.exists():
            return False, current_digest
        original = read_bytes_shared(baseline)
        # Matching the original bytes means the feature is off
        if current_digest == digest_bytes(original):
            return False, current_digest
        # Matching the transformed bytes means the feature is on
        if current_digest == digest_bytes(transform_medical_feature(feature, original)):
            return True, current_digest
        # Anything else was edited outside the manager and must not be overwritten
        raise ContentChangedError("medical feature target differs from both its original and managed state")

    @staticmethod
    def _proposed(profile_id: str, feature: str, path: Path, digest: str,
                  baseline: Path, enabled: bool) -> bytes:
        """Return the transformed or original bytes for the requested toggle."""
        current = read_bytes_shared(path)
        # Re-check the digest so a concurrent edit cannot be overwritten
        if digest_bytes(current) != digest:
            raise ContentChangedError("medical feature target changed after it was loaded")
        # Prefer the stored baseline; fall back to current bytes when none exists
        original = read_bytes_shared(baseline) if baseline.exists() else current
        return transform_medical_feature(feature, original) if enabled else original

    def _baseline(self, profile_id: str, feature: str) -> Path:
        """Return the manager-owned baseline path for one profile and feature."""
        return self._baselines / profile_id / f"{feature}.original.xml"

    def _baseline_source(self, profile_id: str, feature: str, path: Path) -> Path:
        """Return the baseline path, accepting the legacy in-mission location."""
        owned = self._baseline(profile_id, feature)
        # Prefer the manager-owned baseline when it exists
        if owned.exists():
            return owned
        # Fall back to the legacy backup location inside the mission folder
        legacy = path.parent / ".dayz_manager_backups" / path.name
        return legacy if legacy.is_file() else owned

    @staticmethod
    def _write_baseline(path: Path, content: bytes) -> None:
        """Create the baseline file exclusively and flush it to disk."""
        # Create the parent directory, then write the baseline exactly once
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError:
            # Refuse a conflicting pre-existing baseline
            if read_bytes_shared(path) != content:
                raise ContentChangedError("medical feature baseline already exists with different content")
