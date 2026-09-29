"""Supported shared DayZ configuration fields and value validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


class ConfigurationValidationError(ValueError):
    """Raised when shared configuration values are invalid."""
    pass


@dataclass(frozen=True)
class FieldSpec:
    """Declared kind and bounds of one configuration field."""

    kind: str
    minimum: float | None = None
    maximum: float | None = None
    secret: bool = False


# Editable serverDZ.cfg fields with their value specifications
SERVER_FIELDS: Mapping[str, FieldSpec] = {
    "hostname": FieldSpec("string"),
    "password": FieldSpec("string", secret=True),
    "passwordAdmin": FieldSpec("string", secret=True),
    "maxPlayers": FieldSpec("integer", 1, 2_147_483_647),
    "enableWhitelist": FieldSpec("boolean"),
    "disableVoN": FieldSpec("boolean"),
    "disable3rdPerson": FieldSpec("boolean"),
    "disableCrosshair": FieldSpec("boolean"),
    "disablePersonalLight": FieldSpec("boolean"),
    "lightingConfig": FieldSpec("integer", 0, 1),
    "serverTimeAcceleration": FieldSpec("integer", 0, 24),
    "serverNightTimeAcceleration": FieldSpec("integer", 0, 64),
    "serverTimePersistent": FieldSpec("boolean"),
    "storageAutoFix": FieldSpec("boolean"),
    "instanceId": FieldSpec("integer", 0, 2_147_483_647),
    "enableCfgGameplayFile": FieldSpec("boolean"),
}


# Editable cfgGameplay.json fields with their value specifications
GAMEPLAY_FIELDS: Mapping[str, FieldSpec] = {
    "GeneralData.disableBaseDamage": FieldSpec("boolean"),
    "GeneralData.disableContainerDamage": FieldSpec("boolean"),
    "GeneralData.disableRespawnDialog": FieldSpec("boolean"),
    "GeneralData.disableRespawnInUnconsciousness": FieldSpec("boolean"),
    "PlayerData.disablePersonalLight": FieldSpec("boolean"),
    "PlayerData.StaminaData.staminaMax": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.staminaMinCap": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.staminaWeightLimitThreshold": FieldSpec("number", 0, 1_000_000),
    "PlayerData.StaminaData.staminaKgToStaminaPercentPenalty": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.sprintStaminaModifierErc": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.sprintStaminaModifierCro": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.sprintSwimmingStaminaModifier": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.sprintLadderStaminaModifier": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.meleeStaminaModifier": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.obstacleTraversalStaminaModifier": FieldSpec("number", 0, 100_000),
    "PlayerData.StaminaData.holdBreathStaminaModifier": FieldSpec("number", 0, 100_000),
    "PlayerData.ShockHandlingData.shockRefillSpeedConscious": FieldSpec("number", 0, 100_000),
    "PlayerData.ShockHandlingData.shockRefillSpeedUnconscious": FieldSpec("number", 0, 100_000),
    "PlayerData.DrowningData.staminaDepletionSpeed": FieldSpec("number", 0, 100_000),
    "PlayerData.DrowningData.healthDepletionSpeed": FieldSpec("number", 0, 100_000),
    "PlayerData.MovementData.timeToSprint": FieldSpec("number", 0, 100_000),
    "PlayerData.MovementData.rotationSpeedSprint": FieldSpec("number", 0, 100_000),
    "BaseBuildingData.HologramData.disableIsCollidingBBoxCheck": FieldSpec("boolean"),
    "BaseBuildingData.HologramData.disableIsClippingRoofCheck": FieldSpec("boolean"),
    "BaseBuildingData.HologramData.disableIsCollidingGPlotCheck": FieldSpec("boolean"),
    "WorldsData.lightingConfig": FieldSpec("integer", 0, 1),
    "MapData.ignoreMapOwnership": FieldSpec("boolean"),
    "MapData.ignoreNavItemsOwnership": FieldSpec("boolean"),
    "MapData.displayPlayerPosition": FieldSpec("boolean"),
    "MapData.displayNavInfo": FieldSpec("boolean"),
    "UIData.use3DMap": FieldSpec("boolean"),
    "VehicleData.boatDecayMultiplier": FieldSpec("number", 0.0, 5.0),
}


def field_specs(target: str) -> Mapping[str, FieldSpec]:
    """Return the field specifications for a configuration target."""
    if target == "server":
        return SERVER_FIELDS
    if target == "gameplay":
        return GAMEPLAY_FIELDS
    raise ConfigurationValidationError("target must be server or gameplay")


def validate_updates(target: str, raw: object) -> dict[str, str | bool | int | float]:
    """Return validated update values for one target's fields."""
    specs = field_specs(target)
    if not isinstance(raw, dict) or not raw:
        raise ConfigurationValidationError("updates must be a non-empty object")
    # Reject fields outside the supported set
    unknown = set(raw).difference(specs)
    if unknown:
        raise ConfigurationValidationError(f"updates contain unsupported fields: {sorted(unknown)}")
    # Validate each provided value against its specification
    return {key: validate_value(key, value, specs[key]) for key, value in raw.items()}


def validate_value(key: str, value: object, spec: FieldSpec) -> str | bool | int | float:
    """Return one value validated against its field specification."""
    if spec.kind == "string":
        if not isinstance(value, str) or len(value) > 4096 or any(mark in value for mark in "\x00\r\n"):
            raise ConfigurationValidationError(f"{key} must be a safe string")
        return value
    if spec.kind == "boolean":
        if not isinstance(value, bool):
            raise ConfigurationValidationError(f"{key} must be a boolean")
        return value
    if spec.kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigurationValidationError(f"{key} must be an integer")
        number: int | float = value
    elif spec.kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ConfigurationValidationError(f"{key} must be a finite number")
        number = value
    else:
        raise AssertionError(f"unsupported field kind: {spec.kind}")
    # Bounds are inclusive and checked after type checking
    if spec.minimum is not None and number < spec.minimum:
        raise ConfigurationValidationError(f"{key} is below its supported minimum")
    if spec.maximum is not None and number > spec.maximum:
        raise ConfigurationValidationError(f"{key} is above its supported maximum")
    return value


def describe_fields(target: str, values: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return field descriptors with current values for display."""
    return [
        {
            "key": key,
            "kind": spec.kind,
            "secret": spec.secret,
            "present": key in values,
            "value": values.get(key),
        }
        for key, spec in field_specs(target).items()
    ]
