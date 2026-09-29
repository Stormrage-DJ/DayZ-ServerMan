"""Named browser host methods for reversible medical features."""

from __future__ import annotations

from typing import Any


class MedicalFeatureHostMethods:
    """Native methods for the reversible medical feature editor."""
    def load_medical_features(self, profile_id: object) -> dict[str, Any]:
        """Load medical feature states for a profile."""
        return self._invoke("load_medical_features", {"profile_id": profile_id})

    def preview_medical_feature(
        self, profile_id: object, feature: object, enabled: object,
        expected_profile_revision: object, expected_settings_revision: object,
        expected_digest: object,
    ) -> dict[str, Any]:
        """Preview enabling or disabling one medical feature."""
        return self._medical_feature_edit("preview_medical_feature", profile_id, feature, enabled,
            expected_profile_revision, expected_settings_revision, expected_digest)

    def apply_medical_feature(
        self, profile_id: object, feature: object, enabled: object,
        expected_profile_revision: object, expected_settings_revision: object,
        expected_digest: object,
    ) -> dict[str, Any]:
        """Apply a reviewed medical feature change."""
        return self._medical_feature_edit("apply_medical_feature", profile_id, feature, enabled,
            expected_profile_revision, expected_settings_revision, expected_digest)

    def _medical_feature_edit(
        self, method: str, profile_id: object, feature: object, enabled: object,
        expected_profile_revision: object, expected_settings_revision: object,
        expected_digest: object,
    ) -> dict[str, Any]:
        """Dispatch a medical feature edit request."""
        return self._invoke(method, {"profile_id": profile_id, "feature": feature,
            "enabled": enabled, "expected_profile_revision": expected_profile_revision,
            "expected_settings_revision": expected_settings_revision,
            "expected_digest": expected_digest})
