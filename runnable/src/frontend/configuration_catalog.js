// Declares the guided field catalog for the server configuration workspace.
"use strict";

// Build one field definition with its key, label, hint, and pair group.
const configurationField = (key, label, hint, pair = "") => ({ key, label, hint, pair });

// Group the editable fields into guided sections for the interface.
const serverGroups = [
  { title: "Identity & access", hint: "How the server appears and who can administer or join it.", fields: [
    configurationField("hostname", "Server name", "The name players see in the DayZ server browser."),
    configurationField("password", "Server password", "Leave empty for a public server. Players must enter this value when set."),
    configurationField("passwordAdmin", "Admin password", "Used by in-game administration tools. This is separate from the player password."),
    configurationField("maxPlayers", "Maximum players", "The highest number of simultaneous player connections."),
    configurationField("steamQueryPort", "Steam query port", "UDP port used by the Steam server browser. Allow this port through the host firewall and router."),
    configurationField("instanceId", "Instance ID", "Separates persistence when multiple servers share one installation. Keep it stable after a server goes live."),
  ]},
  { title: "Rules & visibility", hint: "Player access and client-side assistance permitted by the server.", fields: [
    configurationField("enableWhitelist", "Whitelist", "When enabled, only identities present in the whitelist can join."),
    configurationField("disableVoN", "Disable voice chat", "Turns off DayZ voice-over-network communication for every player."),
    configurationField("disable3rdPerson", "Disable third person", "Forces the first-person camera when enabled."),
    configurationField("disableCrosshair", "Disable crosshair", "Removes the built-in aiming crosshair from the player HUD."),
    configurationField("disablePersonalLight", "Disable personal light", "Removes the automatic night-time glow around players."),
    configurationField("lightingConfig", "Lighting preset", "0 uses brighter nights; 1 uses the darker night-lighting configuration."),
  ]},
  { title: "Time & persistence", hint: "How quickly time passes and how storage recovery behaves.", fields: [
    configurationField("serverTimeAcceleration", "Day acceleration", "1 is real time; higher values make daytime pass faster."),
    configurationField("serverNightTimeAcceleration", "Night acceleration", "Additional night-time multiplier applied on top of day acceleration."),
    configurationField("serverTimePersistent", "Persistent server time", "Continues the saved world time after restart instead of resetting it."),
    configurationField("storageAutoFix", "Automatic storage repair", "Allows DayZ to repair damaged persistence data during startup."),
    configurationField("enableCfgGameplayFile", "Use gameplay configuration", "Required for the Gameplay Tweaks values from cfgGameplay.json to take effect."),
  ]},
];

// Publish the configuration field catalog used by the workspace.
window.ServerManConfigurationCatalog = Object.freeze({ serverGroups });
