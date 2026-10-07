"""Operator labels of configuration and tweak fields per target (A9, design 11.1).

Python sibling of `activity_wording.py`. The frontend catalogues `configuration_catalog.js` and
`tweaks_catalog.js` hold the same (key, label) pairs; `tests/test_cli_field_wording.py` keeps them equal.
A key is an input name of `--set` and is never printed in place of its label.
"""

from __future__ import annotations

import re

# Label per field key, per target: "server" of the configuration catalogue, the others of the tweaks catalogue
FIELD_LABELS: dict[str, dict[str, str]] = {
    "server": {
        "hostname": "Server name",
        "password": "Server password",
        "passwordAdmin": "Admin password",
        "maxPlayers": "Maximum players",
        "steamQueryPort": "Steam query port",
        "instanceId": "Instance ID",
        "enableWhitelist": "Whitelist",
        "disableVoN": "Disable voice chat",
        "disable3rdPerson": "Disable third person",
        "disableCrosshair": "Disable crosshair",
        "disablePersonalLight": "Disable personal light",
        "lightingConfig": "Lighting preset",
        "serverTimeAcceleration": "Day acceleration",
        "serverNightTimeAcceleration": "Night acceleration",
        "serverTimePersistent": "Persistent server time",
        "storageAutoFix": "Automatic storage repair",
        "enableCfgGameplayFile": "Use gameplay configuration",
    },
    "gameplay": {
        "GeneralData.disableBaseDamage": "Disable base damage",
        "GeneralData.disableContainerDamage": "Disable container damage",
        "GeneralData.disableRespawnDialog": "Disable respawn dialog",
        "GeneralData.disableRespawnInUnconsciousness": "Disable unconscious respawn",
        "PlayerData.disablePersonalLight": "Disable personal light",
        "PlayerData.ShockHandlingData.shockRefillSpeedConscious": "Conscious refill speed",
        "PlayerData.ShockHandlingData.shockRefillSpeedUnconscious": "Unconscious refill speed",
        "PlayerData.DrowningData.staminaDepletionSpeed": "Stamina depletion speed",
        "PlayerData.DrowningData.healthDepletionSpeed": "Health depletion speed",
        "BaseBuildingData.HologramData.disableIsCollidingBBoxCheck": "Disable collision check",
        "BaseBuildingData.HologramData.disableIsClippingRoofCheck": "Disable roof clipping check",
        "BaseBuildingData.HologramData.disableIsCollidingGPlotCheck": "Disable plot collision check",
        "PlayerData.StaminaData.staminaMax": "Maximum stamina",
        "PlayerData.StaminaData.staminaMinCap": "Minimum stamina",
        "PlayerData.StaminaData.staminaWeightLimitThreshold": "Weight threshold",
        "PlayerData.StaminaData.staminaKgToStaminaPercentPenalty": "Weight penalty per kg",
        "PlayerData.StaminaData.sprintStaminaModifierErc": "Standing sprint",
        "PlayerData.StaminaData.sprintStaminaModifierCro": "Crouched sprint",
        "PlayerData.StaminaData.sprintLadderStaminaModifier": "Ladder sprint",
        "PlayerData.StaminaData.sprintSwimmingStaminaModifier": "Swimming sprint",
        "PlayerData.StaminaData.meleeStaminaModifier": "Melee",
        "PlayerData.StaminaData.holdBreathStaminaModifier": "Hold breath",
        "PlayerData.StaminaData.obstacleTraversalStaminaModifier": "Obstacle traversal",
        "PlayerData.MovementData.timeToSprint": "Time to sprint",
        "PlayerData.MovementData.rotationSpeedSprint": "Sprint rotation speed",
        "MapData.ignoreMapOwnership": "Ignore map ownership",
        "MapData.ignoreNavItemsOwnership": "Ignore navigation item ownership",
        "MapData.displayPlayerPosition": "Display player position",
        "MapData.displayNavInfo": "Display navigation info",
        "UIData.use3DMap": "Use 3D map",
        "VehicleData.boatDecayMultiplier": "Boat decay multiplier",
    },
    "medical": {
        "medical_loot_zones": "Medical loot zones",
        "medical_item_spawns": "Medical item spawns",
    },
    "weather": {
        "rain_disabled": "Disable rain",
        "rain_time_min": "Minimum interval",
        "rain_time_max": "Maximum interval",
        "rain_fade": "Fade threshold",
    },
    "economy": {
        "CleanupAvoidance": "Cleanup avoidance",
        "LootSpawnAvoidance": "Loot spawn avoidance",
        "SpawnInitial": "Initial spawn",
        "RespawnAttempt": "Respawn attempt",
        "CleanupLifetimeRuined": "Ruined item lifetime",
        "CleanupLifetimeDefault": "Default item lifetime",
        "ZombieMaxCount": "Maximum infected",
        "CleanupLifetimeDeadInfected": "Dead infected lifetime",
        "ZoneSpawnDist": "Zone spawn distance",
        "AnimalMaxCount": "Maximum animals",
        "CleanupLifetimeDeadAnimal": "Dead animal lifetime",
        "CleanupLifetimeDeadPlayer": "Dead player lifetime",
        "TimeLogin": "Login time",
        "TimeLogout": "Logout time",
        "TimePenalty": "Login penalty",
        "LootDamageMin": "Minimum loot damage",
        "LootDamageMax": "Maximum loot damage",
        "FoodDecay": "Food decay",
        "WorldWetTempUpdate": "Wetness temperature updates",
    },
}
# The CLI-only label of the spawnable damage cap, which the window does not show
SPAWNABLE_DAMAGE_LABELS: dict[str, str] = {"maximum": "Maximum damage of spawned items"}
# Group headings of the event and population tables and of the starter items, as the window shows them
EVENT_GROUP_TITLES = ("Dynamic world", "Seasonal", "Repairable vehicles", "Herd animals", "Ambient animals")
# A name prefix that the event and population tables leave out
_ENTRY_PREFIX = re.compile(r"^Static|^Vehicle|^Animal|^Ambient")
_CAMEL = re.compile(r"([a-z])([A-Z])")


def field_label(target: str, key: str) -> str:
    """Return the label of a field; a key without a catalogue label reads as its words (`tweakLabel`)."""
    return FIELD_LABELS.get(target, {}).get(key) or readable_key(key)


def readable_key(key: str) -> str:
    """Turn a key into words, as the window's `tweakLabel` does for the event table columns."""
    return _CAMEL.sub(r"\1 \2", key).replace("_", " ")


def entry_name(name: str) -> str:
    """Return an event or population name as the window's table shows it (without its type prefix)."""
    return _ENTRY_PREFIX.sub("", name, count=1)
