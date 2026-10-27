// Catalog data for the tweaks workspace: fields, groups, and tabs.
"use strict";

// Describe one tweakable field; a percent field also names its slider range.
const field = (key, label, kind, hint, pair = "", range = null) => ({ key, label, kind, hint, pair, range });
// Slider range of the loot percentages, and of the initial loot share.
const lootRange = Object.freeze({ min: 0, max: 300, step: 5 });
const initialRange = Object.freeze({ min: 0, max: 100, step: 5 });
// Describe one loot percentage slider.
const percent = (key, label, hint, range = lootRange) => field(key, label, "percent", hint, "", range);
// Describe one titled group of fields within a target.
const group = (title, target, fields, hint = "") => ({ title, target, fields, hint });

// Gameplay subtabs with their field groups and helper text.
const gameplayTabs = [
  { id: "player", label: "Player", groups: [
    group("Damage & respawn", "gameplay", [
      field("GeneralData.disableBaseDamage", "Disable base damage", "boolean", "Prevents player-built bases from taking damage."),
      field("GeneralData.disableContainerDamage", "Disable container damage", "boolean", "Prevents tents, barrels, and other containers from taking damage."),
      field("GeneralData.disableRespawnDialog", "Disable respawn dialog", "boolean", "Skips the normal respawn-choice dialog after death."),
      field("GeneralData.disableRespawnInUnconsciousness", "Disable unconscious respawn", "boolean", "Stops unconscious players from choosing to respawn."),
      field("PlayerData.disablePersonalLight", "Disable personal light", "boolean", "Removes the local night-time glow around each player."),
    ]),
    group("Shock", "gameplay", [
      field("PlayerData.ShockHandlingData.shockRefillSpeedConscious", "Conscious refill speed", "number", "Shock restored per second. Higher values recover consciousness sooner.", "shock-refill"),
      field("PlayerData.ShockHandlingData.shockRefillSpeedUnconscious", "Unconscious refill speed", "number", "Shock restored per second. Higher values recover consciousness sooner.", "shock-refill"),
    ]),
    group("Drowning", "gameplay", [
      field("PlayerData.DrowningData.staminaDepletionSpeed", "Stamina depletion speed", "number", "How quickly stamina and then health are lost while drowning.", "drowning-loss"),
      field("PlayerData.DrowningData.healthDepletionSpeed", "Health depletion speed", "number", "How quickly stamina and then health are lost while drowning.", "drowning-loss"),
    ]),
    group("Base building hologram", "gameplay", [
      field("BaseBuildingData.HologramData.disableIsCollidingBBoxCheck", "Disable collision check", "boolean", "Allows placement even when the object's bounding box collides."),
      field("BaseBuildingData.HologramData.disableIsClippingRoofCheck", "Disable roof clipping check", "boolean", "Allows placement that clips into a roof."),
      field("BaseBuildingData.HologramData.disableIsCollidingGPlotCheck", "Disable plot collision check", "boolean", "Allows placement that intersects restricted garden-plot space."),
    ]),
  ]},
  { id: "stamina", label: "Stamina", groups: [
    group("Capacity & weight", "gameplay", [
      field("PlayerData.StaminaData.staminaMax", "Maximum stamina", "number", "Upper and lower stamina limits available to a player.", "stamina-bounds"),
      field("PlayerData.StaminaData.staminaMinCap", "Minimum stamina", "number", "Upper and lower stamina limits available to a player.", "stamina-bounds"),
      field("PlayerData.StaminaData.staminaWeightLimitThreshold", "Weight threshold", "number", "Carried weight before the maximum-stamina penalty begins."),
      field("PlayerData.StaminaData.staminaKgToStaminaPercentPenalty", "Weight penalty per kg", "number", "Maximum-stamina percentage removed for each kilogram above the threshold."),
    ]),
    group("Activity modifiers", "gameplay", [
      field("PlayerData.StaminaData.sprintStaminaModifierErc", "Standing sprint", "number", "Stamina-use multipliers for upright and crouched sprinting.", "ground-sprint"),
      field("PlayerData.StaminaData.sprintStaminaModifierCro", "Crouched sprint", "number", "Stamina-use multipliers for upright and crouched sprinting.", "ground-sprint"),
      field("PlayerData.StaminaData.sprintLadderStaminaModifier", "Ladder sprint", "number", "Multiplier applied to stamina used while moving quickly on ladders."),
      field("PlayerData.StaminaData.sprintSwimmingStaminaModifier", "Swimming sprint", "number", "Multiplier applied to stamina used while fast swimming."),
      field("PlayerData.StaminaData.meleeStaminaModifier", "Melee", "number", "Multiplier applied to stamina used by melee attacks."),
      field("PlayerData.StaminaData.holdBreathStaminaModifier", "Hold breath", "number", "Multiplier applied to stamina used while holding breath when aiming."),
      field("PlayerData.StaminaData.obstacleTraversalStaminaModifier", "Obstacle traversal", "number", "Multiplier applied to stamina used when vaulting or climbing obstacles."),
    ]),
    group("Movement", "gameplay", [
      field("PlayerData.MovementData.timeToSprint", "Time to sprint", "number", "Delay before normal movement reaches sprint speed."),
      field("PlayerData.MovementData.rotationSpeedSprint", "Sprint rotation speed", "number", "How quickly a sprinting player can change direction."),
    ]),
  ]},
  { id: "map-loot", label: "Map & Loot", groups: [
    group("Map visibility", "gameplay", [
      field("MapData.ignoreMapOwnership", "Ignore map ownership", "boolean", "Allows the map screen without requiring a physical map item."),
      field("MapData.ignoreNavItemsOwnership", "Ignore navigation item ownership", "boolean", "Allows navigation data without the related compass or GPS items."),
      field("MapData.displayPlayerPosition", "Display player position", "boolean", "Shows the player's current position on the map."),
      field("MapData.displayNavInfo", "Display navigation info", "boolean", "Shows additional navigation information on the map."),
      field("UIData.use3DMap", "Use 3D map", "boolean", "Uses DayZ's 3D map presentation instead of the flat map."),
    ]),
    group("Medical loot", "medical", [
      field("medical_loot_zones", "Medical loot zones", "feature", "Restrict medical locations to medical supplies."),
    ], "Legacy-compatible, map-specific feature. Set the amount of medical supplies on the Loot Supply tab."),
  ]},
  { id: "supply", label: "Loot Supply", groups: [
    group("Item supply", "loot_supply", [
      percent("supply_weapons", "Weapons", "Firearms, crossbows and other items of the weapons category."),
      percent("supply_attachments", "Weapon attachments", "Optics, stocks, handguards, suppressors, bayonets and weapon lights."),
      percent("supply_magazines", "Magazines", "Loose magazines (Mag_ items)."),
      percent("supply_ammo", "Loose ammunition", "Ammunition piles (Ammo_ items)."),
      percent("supply_ammo_boxes", "Ammunition boxes", "Boxed ammunition (AmmoBox_ items)."),
      percent("supply_explosives", "Explosives", "Grenades, mines and other explosives."),
      percent("supply_food", "Food & drink", "Canned food, drinks and other items of the food category."),
      percent("supply_medical", "Medical supplies", "Bandages, medicine, blood and saline kits, and other medical items."),
      percent("supply_tools", "Tools", "Tools and other items of the tools category, without medical supplies."),
      percent("supply_clothing", "Clothing", "Clothes, vests, helmets and other items of the clothes category."),
      percent("supply_containers", "Backpacks & containers", "Backpacks, tents, barrels and other containers."),
    ], "Each percentage scales how many items of the group the central economy keeps on the map (nominal and min in db/types.xml), always from the original values. 100% keeps the original. More items also need free loot places; see the container capacity in the map editor."),
    group("Ammunition fill", "loot_supply", [
      percent("ammo_fill", "Ammunition & magazine fill", "Scales how full spawned ammunition piles and magazines are, up to completely full."),
    ]),
    group("Weapon loadout", "weapon_magazines", [
      percent("magazine_chance", "Weapons with a magazine", "Scales the chance that a spawned weapon comes with a magazine attached (cfgspawnabletypes.xml), up to always."),
    ]),
    group("Economy throughput", "economy", [
      percent("InitialSpawn", "Initial loot", "Share of each item's nominal that spawns when the server starts without stored loot.", initialRange),
      field("RespawnTypes", "Item types per respawn", "integer", "How many different item types the economy can respawn at once. Raise it with high supply percentages so the map refills sooner."),
      field("RespawnLimit", "Items per type per respawn", "integer", "How many items of one type the economy can respawn at once."),
      field("RespawnAttempt", "Respawn attempts", "integer", "How many attempts the economy makes for one item respawn."),
      field("SpawnInitial", "Initial spawn attempts", "integer", "How many initial tests are allowed for item spawn. Bohemia's default is 1200."),
      field("RestartSpawn", "Restart respawn", "integer", "Whether loot respawns toward its nominal at a server restart. Bohemia's default is 0."),
    ], "The supply percentages raise the targets; these values decide how fast the central economy reaches them."),
  ]},
  { id: "weather", label: "Weather", groups: [
    group("Rain", "weather", [
      field("rain_disabled", "Disable rain", "boolean", "Prevents the mission weather system from scheduling rain."),
      field("rain_time_min", "Minimum interval", "integer", "Minimum and maximum delay used when the mission schedules rain.", "rain-interval"),
      field("rain_time_max", "Maximum interval", "integer", "Minimum and maximum delay used when the mission schedules rain.", "rain-interval"),
      field("rain_fade", "Fade threshold", "integer", "Controls how gradually rain intensity changes between weather states."),
    ], "Map-specific weather values from the selected mission."),
  ]},
  { id: "respawns", label: "Respawns", groups: [
    group("Loot cleanup & respawn", "economy", [
      field("CleanupAvoidance", "Cleanup avoidance", "integer", "Distances around players used for cleanup and new loot placement.", "avoidance"),
      field("LootSpawnAvoidance", "Loot spawn avoidance", "integer", "Distances around players used for cleanup and new loot placement.", "avoidance"),
      field("CleanupLifetimeRuined", "Ruined item lifetime", "integer", "Seconds before ruined and ordinary abandoned items are cleaned up.", "item-lifetime"),
      field("CleanupLifetimeDefault", "Default item lifetime", "integer", "Seconds before ruined and ordinary abandoned items are cleaned up.", "item-lifetime"),
    ]),
    group("Zombies", "economy", [field("ZombieMaxCount", "Maximum infected", "integer", "Global upper limit for infected managed by the central economy."),
      field("CleanupLifetimeDeadInfected", "Dead infected lifetime", "integer", "Seconds that infected corpses remain before cleanup."), field("ZoneSpawnDist", "Zone spawn distance", "integer", "Minimum distance from players used for economy zone spawning.")]),
    group("Animals", "economy", [field("AnimalMaxCount", "Maximum animals", "integer", "Global upper limit for animals managed by the central economy."),
      field("CleanupLifetimeDeadAnimal", "Dead animal lifetime", "integer", "Seconds that animal corpses remain before cleanup.")]),
    group("Players & bodies", "economy", [field("CleanupLifetimeDeadPlayer", "Dead player lifetime", "integer", "Seconds that dead player bodies remain before cleanup."),
      field("TimeLogin", "Login time", "integer", "Seconds required to enter and leave the server.", "session-delay"), field("TimeLogout", "Logout time", "integer", "Seconds required to enter and leave the server.", "session-delay"), field("TimePenalty", "Login penalty", "integer", "Additional delay applied after rapid reconnects or server hopping.")]),
  ]},
  { id: "starter", label: "Starter Items", groups: [
    group("Starter loadout", "starter_loadout", [], "Choose the items placed into a new character's starting inventory."),
  ]},
  { id: "condition", label: "Condition & Decay", groups: [
    group("Loot condition", "economy", [field("LootDamageMin", "Minimum loot damage", "number", "Random damage range applied to newly spawned loot.", "loot-damage"), field("LootDamageMax", "Maximum loot damage", "number", "Random damage range applied to newly spawned loot.", "loot-damage")]),
    group("World decay", "economy", [field("FoodDecay", "Food decay", "boolean", "Allows perishable food to lose condition over time."), field("WorldWetTempUpdate", "Wetness temperature updates", "boolean", "Updates world items as rain, wetness, and temperature change.")]),
    group("Vehicles", "gameplay", [field("VehicleData.boatDecayMultiplier", "Boat decay multiplier", "number", "Scales how quickly abandoned boats decay; higher values accelerate decay.")]),
  ]},
];

// Dynamic and seasonal event groups used by the event tables.
const eventGroups = [
  { title: "Dynamic world", names: ["StaticHeliCrash", "StaticMilitaryConvoy", "StaticPoliceCar", "StaticPoliceSituation", "StaticTrain", "StaticAirplaneCrate", "StaticContaminatedArea"] },
  { title: "Seasonal", names: ["StaticBonfire", "StaticChristmasTree", "StaticSantaCrash"] },
];
// Vehicle and animal groups used by the population tables.
const populationGroups = [
  { title: "Repairable vehicles", names: ["VehicleCivilianSedan", "VehicleHatchback02", "VehicleOffroad02", "VehicleOffroadHatchback", "VehicleSedan02", "VehicleTruck01", "VehicleBoat"] },
  { title: "Herd animals", names: ["AnimalBear", "AnimalCow", "AnimalDeer", "AnimalGoat", "AnimalPig", "AnimalRoeDeer", "AnimalSheep", "AnimalWildBoar", "AnimalWolf"] },
  { title: "Ambient animals", names: ["AmbientFox", "AmbientHare", "AmbientHen"] },
];
// Starter item categories in their canonical order.
const starterGroups = {
  "Food & Drink": ["Apple", "Pear", "Plum", "WaterBottle", "Canteen"],
  Medical: ["BandageDressing", "TetracyclineAntibiotics", "VitaminBottle", "CharcoalTablets", "PainkillerTablets", "DisinfectantSpray", "IodineTincture", "PurificationTablets"],
  Tools: ["SteakKnife", "CanOpener", "BoxedMatches", "Heatpack", "Compass", "TouristMap"],
  Gear: ["HipPack_Medical", "TaloonBag_Green", "ChildBag_Green", "ImprovisedBag", "Rag", "DuctTape", "SewingKit", "LeatherSewingKit"],
};

// Publish the tweaks catalog for the tweaks workspace.
window.ServerManTweaksCatalog = Object.freeze({ gameplayTabs, eventGroups, populationGroups, starterGroups });
