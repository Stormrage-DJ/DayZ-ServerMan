"""Reversible, profile-scoped legacy medical feature workflow."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.medical_editor_texts import FINGERPRINT_STALE_TEXT, ZoneReviewStale
from ..domain.models import RevisionConflict
from ..repositories.atomic_file import AtomicFilePublisher, ContentChangedError
from ..repositories.configuration_common import digest_bytes
from ..repositories.medical_features import project_zones
from ..repositories.medical_zone_classification import (
    LEGACY, MANAGER, ZoneBaselineClass, ZoneReading, read_zones, zone_class,
)
from ..repositories.medical_zone_fragments import has_zone_changes, zones_enabled
from .medical_editor_review import ZoneTarget, fingerprint_form
from .medical_feature_paths import FEATURE_PATHS, MedicalFeaturePaths
from .mission_configuration import DIGEST, _contained, _digest, _revision, resolve_profile_mission
from .profiles import ProfileService
from .settings import SettingsService, SettingsValidationError

if TYPE_CHECKING:
    from .medical_editor_toggle import MedicalEditorToggle

# Refusal of a loot-zone change on an editor-managed mission when no D4 toggle is bound (8.13 binds one)
EDITOR_MANAGED = "medical loot zones cannot change because the map editor manages this mission"
# Result of a loot-zone toggle on a mission where no medical group changes; it succeeds and writes nothing
NO_ZONE_CHANGE = "No medical loot group on this mission changes with this setting. No file was changed."


class MedicalFeatureService(MedicalFeaturePaths):
    """Toggle legacy medical files between their original and managed states."""

    def __init__(self, profiles: ProfileService, settings: SettingsService,
                 baselines: Path, publisher: AtomicFilePublisher | None = None, *,
                 mission_map: Path | None = None) -> None:
        """Bind profiles, settings, the baseline store, the atomic publisher and the map editor area."""
        self._profiles, self._settings = profiles, settings
        self._baselines, self._mission_map = baselines, mission_map
        self._publisher = publisher or AtomicFilePublisher()
        self._editor: MedicalEditorToggle | None = None

    def bind_editor(self, editor: MedicalEditorToggle) -> None:
        """Bind the D4 zone toggle of editor-managed missions (8.13); without it those missions refuse."""
        self._editor = editor

    def load(self, profile_id: object) -> dict[str, Any]:
        """Report each feature's enabled state, digest, and file path."""
        # Resolve the profile, settings, and mission for this query
        profile, settings_revision, _root, mission_root, mission = self._context(profile_id)
        # Collect the current state of every reversible feature
        features = {}
        zone: dict[str, Any] | None = None
        for feature, relative in FEATURE_PATHS.items():
            path = _contained(mission, mission / relative, directory=False)
            if feature == "medical_loot_zones":
                enabled, digest, zone = self._zone_state(profile.values.profile_id, mission_root, mission, path)
            else:
                enabled, digest = self._state(profile.values.profile_id, feature, path)
            features[feature] = {"enabled": enabled, "digest": digest,
                                 "relative_path": str(Path(mission_root) / relative)}
        # Additive (6.5): whether the zone toggle can use its stored original, with the reason when it cannot
        features["medical_loot_zones"]["zone_baseline"] = zone
        return {"profile_id": profile.values.profile_id, "profile_revision": profile.revision,
                "settings_revision": settings_revision, "features": features}

    def classify_zone_baseline(self, profile_id: object) -> ZoneBaselineClass | None:
        """Classify the loot-zone baseline that the toggle uses, or return None when there is none.

        On an editor-managed mission the result is unmappable with the toggle's editor-managed reason. Read-only:
        no mission file, baseline or manager metadata is written, and item-spawn baselines are not read.
        """
        profile, _settings_revision, _root, _mission_root, mission = self._context(profile_id)
        path = _contained(mission, mission / FEATURE_PATHS["medical_loot_zones"], directory=False)
        found = self._zone_reading(profile.values.profile_id, mission, path, read_bytes_shared(path))
        return None if found is None else found[0]

    def _zone_state(self, profile_key: str, mission_root: str, mission: Path,
                    path: Path) -> tuple[bool, str, dict[str, Any] | None]:
        """Return the zone state, digest and bridge classification from one scan of each file (6.5).

        A state refusal is raised as before. When it is also the classification reason, the error carries
        that classification as `zone_baseline`, and the coordinator adds it to the error details.
        """
        current = read_bytes_shared(path); digest = digest_bytes(current)
        found = self._zone_reading(profile_key, mission, path, current)
        if found is None:
            # Without a baseline the current file is the original
            return zones_enabled(current, current), digest, None
        result, reading, baseline = found
        # A legacy stored original is named from the DayZ server folder, as the feature files are
        shown = (Path(mission_root) / ".dayz_manager_backups" / FEATURE_PATHS["medical_loot_zones"]
                 if result.location == LEGACY else baseline)
        data = {"mappable": result.mappable, "reason": result.reason, "location": result.location,
                "stored_original": str(shown)}
        if reading.error is not None:
            if result.reason == str(reading.error):
                reading.error.zone_baseline = data  # type: ignore[union-attr]
            raise reading.error
        return bool(reading.enabled), digest, data

    def _zone_reading(self, profile_key: str, mission: Path, path: Path,
                      current: bytes) -> tuple[ZoneBaselineClass, ZoneReading, Path] | None:
        """Classify the loot-zone baseline that the toggle uses; return None when there is none."""
        feature = "medical_loot_zones"
        # Use the baseline that the toggle reads: the manager store first, then the legacy folder
        baseline = self._baseline_source(profile_key, feature, path)
        if not baseline.exists():
            return None
        original = read_bytes_shared(baseline)
        location = MANAGER if baseline == self._baseline(profile_key, feature) else LEGACY
        reading = read_zones(current, original)
        result = zone_class(reading, current, original, location)
        # Without a bound D4 toggle an editor-managed mission refuses before the baseline is read, so that
        # reason comes first. A file that cannot be scanned refuses with its scan error first, as the toggle does.
        unbound = self._editor is None
        if unbound and reading.reserved is not None and (reading.reserved or self._editor_record(mission)):
            result = dataclasses.replace(result, mappable=False, enabled=None, reason=EDITOR_MANAGED)
        return result, reading, baseline

    def preview(self, profile_id: object, feature: str, enabled: object,
                expected_profile_revision: object, expected_settings_revision: object,
                expected_digest: object) -> dict[str, Any]:
        """Validate a toggle and return the projected digest without writing."""
        # Validate the request and resolve the target context
        context, target = self._validated_context(profile_id, feature, enabled, expected_profile_revision,
                                                  expected_settings_revision, expected_digest)
        # An editor-managed mission reviews the coordinated toggle and returns its fingerprint (8.13)
        if target is not None and self._editor is not None:
            return self._editor.preview(target)
        # A loot-zone toggle that changes no group succeeds without a change
        if self._zones_unchanged(context[1], context[2], context[4]):
            return {"profile_id": context[0], "feature": feature, "enabled": bool(enabled),
                    "expected_digest": context[3], "proposed_digest": context[3], "changed_fields": [],
                    "message": NO_ZONE_CHANGE}
        # Project the toggled bytes and describe the change
        proposed = self._proposed(*context, bool(enabled))
        return {"profile_id": context[0], "feature": feature, "enabled": bool(enabled),
                "expected_digest": context[3], "proposed_digest": digest_bytes(proposed),
                "changed_fields": [feature]}

    def apply(self, profile_id: object, feature: str, enabled: object,
              expected_profile_revision: object, expected_settings_revision: object,
              expected_digest: object, checkpoint: Callable[[str, int], None], *,
              preview_fingerprint: object = None, operation_id: str | None = None) -> dict[str, Any]:
        """Toggle a feature, preserving its original bytes for reversal; an editor-managed mission uses D4 (8.13)."""
        # Validate the request and resolve the target context
        request = (profile_id, feature, enabled, expected_profile_revision, expected_settings_revision, expected_digest)
        try:
            context, target = self._validated_context(*request)
        except (ContentChangedError, RevisionConflict) as error:
            # A change after the zone review is a stale review also before the guard (Product Owner 14:59:22).
            # A malformed fingerprint counts as missing, so its refusal stays.
            if fingerprint_form(preview_fingerprint) and self._changed_after_review(request):
                raise ZoneReviewStale(FINGERPRINT_STALE_TEXT) from error
            raise
        if target is not None and self._editor is not None:
            return self._editor.apply(target, preview_fingerprint, checkpoint, operation_id=operation_id or uuid4().hex,
                                      reread=lambda: self._reread(request),
                                      write_baseline=self._write_baseline)
        # A fingerprint comes from the review of an editor-managed mission; this mission is not one now
        if preview_fingerprint is not None:
            raise ZoneReviewStale(FINGERPRINT_STALE_TEXT)
        profile_key, _feature, path, digest, baseline = context
        checkpoint("loaded", 20)
        # A loot-zone toggle that changes no group writes neither the baseline nor the mission file
        if self._zones_unchanged(feature, path, baseline):
            checkpoint("verified", 100)
            return {"profile_id": profile_key, "feature": feature, "enabled": bool(enabled),
                    "digest": digest, "changed_fields": [], "message": NO_ZONE_CHANGE}
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

    def _reread(self, request: tuple[object, ...]) -> ZoneTarget | None:
        """Validate the toggle request again under the guard; a changed target or revision is a stale review (8.13)."""
        try:
            return self._validated_context(*request)[1]  # type: ignore[arg-type]
        except (ContentChangedError, RevisionConflict, ValueError) as error:
            raise ZoneReviewStale(FINGERPRINT_STALE_TEXT) from error

    def _changed_after_review(self, request: tuple[object, ...]) -> bool:
        """Return whether a refused zone request is well formed and the current mission is editor-managed.

        A malformed revision or digest is a request error, not a change after the review. The mission class
        comes from the current profile and files, never from the request; when it cannot be read, the
        refusal keeps its own error.
        """
        profile_id, feature, _enabled, profile_revision, settings_revision, digest = request
        revisions = (profile_revision, settings_revision)
        well_formed = (all(isinstance(value, int) and not isinstance(value, bool) for value in revisions)
                       and isinstance(digest, str) and DIGEST.fullmatch(digest) is not None)
        if self._editor is None or feature != "medical_loot_zones" or not well_formed:
            return False
        # Classify the mission that the profile names now
        try:
            mission = self._context(profile_id)[4]
            return self._editor_managed(mission, _contained(mission, mission / FEATURE_PATHS[feature], directory=False))
        except Exception:
            return False

    def _validated_context(self, profile_id: object, feature: str, enabled: object,
                           profile_revision: object, settings_revision: object,
                           expected_digest: object) -> tuple[tuple[str, str, Path, str, Path], ZoneTarget | None]:
        """Validate the toggle request; return the resolved context, and the target of an editor-managed mission."""
        # Require a known feature and an explicit on/off value
        if feature not in FEATURE_PATHS or not isinstance(enabled, bool):
            raise ValueError("medical feature request is invalid")
        profile, current_settings, root, mission_root, mission = self._context(profile_id)
        # Enforce the revision guards and the expected digest
        _revision("profile", profile_revision, profile.revision)
        _revision("settings", settings_revision, current_settings)
        digest = _digest(expected_digest)
        # Locate the feature file inside the mission and read its current state
        path = _contained(mission, mission / FEATURE_PATHS[feature], directory=False)
        # The map editor also writes the loot groups of its missions: there the zone toggle is a D4 operation
        managed = feature == "medical_loot_zones" and self._editor_managed(mission, path)
        if managed and self._editor is None:
            raise ValueError(EDITOR_MANAGED)
        current_enabled, current_digest = self._state(profile.values.profile_id, feature, path)
        baseline = self._baseline_source(profile.values.profile_id, feature, path)
        # Refuse stale digests and toggles that would change nothing, except a zone toggle without any change
        if current_digest != digest:
            raise ContentChangedError("medical feature target changed after it was loaded")
        if current_enabled == enabled and not self._zones_unchanged(feature, path, baseline):
            raise ValueError("medical feature already has the requested state")
        pid = profile.values.profile_id
        target = ZoneTarget(pid, profile.revision, current_settings, root, mission_root, mission, enabled, digest,
                            baseline, self._baseline(pid, feature)) if managed else None
        return (pid, feature, path, digest, baseline), target

    def _context(self, profile_id: object) -> tuple[Any, int, Path, str, Path]:
        """Load the profile and settings and resolve the DayZ root and the mission root safely."""
        # Load the profile and manager settings for this request
        profile = self._profiles.read(profile_id); settings = self._settings.load()
        # Require a configured, resolvable DayZ root before touching the mission
        if settings.dayz_root is None or settings.revision is None:
            raise SettingsValidationError("DayZ root is not configured")
        # Resolve the mission through the shared helper so links are rejected
        root = Path(settings.dayz_root).resolve(strict=True)
        mission_root, mission = resolve_profile_mission(root, profile)
        return profile, settings.revision, root, mission_root, mission

    def _state(self, profile_id: str, feature: str, path: Path) -> tuple[bool, str]:
        """Classify the file as original or managed against the stored baseline."""
        # Read the current bytes and locate the baseline file
        current = read_bytes_shared(path); current_digest = digest_bytes(current)
        baseline = self._baseline_source(profile_id, feature, path)
        # Loot zones compare each medical group with its original and managed children; without a
        # baseline the current file is the original
        original = read_bytes_shared(baseline) if baseline.exists() else current
        return zones_enabled(current, original), current_digest

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
        return project_zones(current, original, enabled)

    @staticmethod
    def _zones_unchanged(feature: str, path: Path, baseline: Path) -> bool:
        """Return whether a loot-zone toggle changes no group, because no group's on and off forms differ."""
        if feature != "medical_loot_zones":
            return False
        current = read_bytes_shared(path)
        return not has_zone_changes(current, read_bytes_shared(baseline) if baseline.exists() else current)
