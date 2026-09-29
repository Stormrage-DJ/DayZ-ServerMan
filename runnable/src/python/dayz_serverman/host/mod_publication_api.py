"""Named browser methods for reviewed mod publication."""

from __future__ import annotations

from typing import Any


class ModPublicationHostMethods:
    """Native methods for the reviewed mod publication workflow."""
    def preview_mod_publication(
        self, profile_id: object, expected_profile_revision: object,
        expected_semantic_profile_digest: object, expected_settings_revision: object,
        update_operation_id: object,
    ) -> dict[str, Any]:
        """Preview publishing mods and keys for a profile."""
        return self._invoke("preview_mod_publication", {
            "profile_id": profile_id, "expected_profile_revision": expected_profile_revision,
            "expected_semantic_profile_digest": expected_semantic_profile_digest,
            "expected_settings_revision": expected_settings_revision,
            "update_operation_id": update_operation_id,
        })

    def publish_mods_and_keys(
        self, profile_id: object, expected_profile_revision: object,
        expected_semantic_profile_digest: object, expected_settings_revision: object,
        update_operation_id: object, publication_fingerprint: object,
    ) -> dict[str, Any]:
        """Publish reviewed mods and keys for a profile."""
        return self._invoke("publish_mods_and_keys", {
            "profile_id": profile_id, "expected_profile_revision": expected_profile_revision,
            "expected_semantic_profile_digest": expected_semantic_profile_digest,
            "expected_settings_revision": expected_settings_revision,
            "update_operation_id": update_operation_id,
            "publication_fingerprint": publication_fingerprint,
        })
