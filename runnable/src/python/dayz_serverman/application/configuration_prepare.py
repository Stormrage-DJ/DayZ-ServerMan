"""Prepare profile-port and native configuration candidates without publication."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.configuration import ConfigurationValidationError, describe_fields, validate_updates
from ..domain.profiles import ProfileInput, ProfileRecord
from ..repositories.configuration_files import ConfigurationSnapshot, digest_bytes, transform_configuration
from ..repositories.profile_migration import parsed_fields


@dataclass(frozen=True)
class PreparedConfiguration:
    """Validated candidates owned by one review or Apply attempt."""

    profile: ProfileRecord
    candidate: ProfileInput
    native_updates: dict[str, Any]
    updates: dict[str, Any]
    proposed: bytes

    @property
    def profile_changed(self) -> bool:
        """Report whether the candidate needs one profile revision advance."""
        return self.candidate != self.profile.values


def prepare_configuration(
    profile: ProfileRecord, snapshot: ConfigurationSnapshot, raw: object,
) -> PreparedConfiguration:
    """Validate both owners while keeping the virtual field out of native bytes."""
    if not isinstance(raw, dict) or not raw:
        raise ConfigurationValidationError("updates must be a non-empty object")
    native = dict(raw)
    candidate = profile.values
    if snapshot.target == "server" and "game_port" in native:
        # Parse the full authoritative profile, changing only its proposed port.
        port = native.pop("game_port")
        candidate = ProfileInput.parse({**parsed_fields(profile.values), "game_port": port})
    # Empty native updates are valid only for an explicitly supplied virtual field.
    updates = validate_updates(snapshot.target, native) if native else {}
    if not native and "game_port" not in raw:
        validate_updates(snapshot.target, native)
    normalized = dict(updates)
    if snapshot.target == "server" and "game_port" in raw:
        normalized["game_port"] = candidate.game_port
    proposed = transform_configuration(snapshot, updates) if updates else snapshot.content
    return PreparedConfiguration(profile, candidate, updates, normalized, proposed)


def configuration_fields(
    target: str, values: dict[str, Any], profile: ProfileInput,
) -> list[dict[str, Any]]:
    """Add the real profile port only to the server presentation."""
    fields = describe_fields(target, values)
    if target == "server":
        fields.insert(0, {"key": "game_port", "kind": "integer", "secret": False,
                          "present": True, "value": profile.game_port})
    return fields


def preview_document(
    prepared: PreparedConfiguration, snapshot: ConfigurationSnapshot,
    settings_revision: int, relative: str, prerequisite: str | None,
) -> dict[str, Any]:
    """Describe the complete normalized change and its real old/new values."""
    before = {field["key"]: field["value"] for field in configuration_fields(
        snapshot.target, snapshot.values, prepared.profile.values)}
    return {
        "profile_id": prepared.profile.values.profile_id,
        "target": snapshot.target, "relative_path": relative,
        "profile_revision": prepared.profile.revision,
        "settings_revision": settings_revision, "expected_digest": snapshot.digest,
        "server_config_digest": prerequisite, "proposed_digest": digest_bytes(prepared.proposed),
        "changed_fields": list(prepared.updates),
        "changes": [{"key": key, "before": before.get(key), "after": value}
                    for key, value in prepared.updates.items()],
        "fields": configuration_fields(snapshot.target, {**snapshot.values, **prepared.native_updates},
                                       prepared.candidate),
    }
