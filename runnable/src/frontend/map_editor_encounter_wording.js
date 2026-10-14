// Encounter texts of leaf 7.3: provider names, reasons, Properties labels and refusals (Product Owner 2026-10-14 10:17:01).
"use strict";

// Provider names in the tool, the list row and Properties; the window never shows a raw provider key
const mapEncounterWordsNames = Object.freeze({native_infected: "Infected", ai_bandits: "AI_Bandits", native_wolf: "Wolves",
  native_bear: "Bears"});

// The snapshot does not say which file failed, so a file reason names the three files that the check reads
const mapEncounterWordsFiles = "cfgenvironment.xml, env/zombie_territories.xml or db/events.xml";

// Infected provider reasons, by the 7.2 capability code
const mapEncounterWordsInfected = Object.freeze({
  territory_not_registered: "cfgenvironment.xml does not register env/zombie_territories.xml.",
  no_matching_events: "no zone name in env/zombie_territories.xml is also an event name in db/events.xml.",
  provider_file_missing: `${mapEncounterWordsFiles} is missing.`,
  provider_file_unreadable: `${mapEncounterWordsFiles} cannot be read.`,
  provider_path_invalid: `${mapEncounterWordsFiles} is a link or is outside the mission folder.`,
  provider_configuration_invalid: `${mapEncounterWordsFiles} is not valid XML of the expected form.`,
});

// Labels of the population values: a plain name and the native key
const mapEncounterWordsLabels = Object.freeze({smin: "Static minimum (smin)", smax: "Static maximum (smax)",
  dmin: "Dynamic minimum (dmin)", dmax: "Dynamic maximum (dmax)"});

// The texts have the same names as their loot circle forms in ServerManMapWording where both exist
window.ServerManMapEncounterWording = Object.freeze({
  labels: mapEncounterWordsLabels, tool: "Encounter", type: "Encounter type", addAction: "Add encounter",
  name: (provider) => mapEncounterWordsNames[provider] ?? window.ServerManMapWording.objectFallback,
  notAvailable: (name) => `${name} (not available)`,
  toolTitle: (name) => `Encounter; ${name}`,
  infectedReason: (code) => "Infected areas are not available on this mission: " +
    (mapEncounterWordsInfected[code] ?? `the check gave the unknown result ${code}.`),
  // Product Owner text of leaf 7.9 for wolves and bears; the backend snapshot gives the same text
  wolfBearReason: "Wolf and bear territories are not supported in this version; the pack territory mapping is not verified.",
  // Decision A text of 2026-10-10; the backend snapshot gives the same text
  banditReason: "Bandit placement is not available on this terrain: no verified ground height source.",
  notOffered: (event) => `This mission does not offer the infected event ${event}.`,
  notOfferedOption: (event) => `${event} (not offered)`,
  nextStep: "Before Apply, make this encounter available, change it, or exclude it.",
  excludedNote: "Excluded: Apply does not write this encounter to the mission.",
  exclude: "Exclude this encounter from Apply", unavailableTag: "Unavailable", excludedTag: "Excluded",
  providerText: (name) => `Encounter type: ${name}`,
  event: "Infected event", population: "Infected population", spawnRadius: "Spawn radius (m)",
  populationNote: "DayZ uses these values, the event limits and ZombieMaxCount to spawn infected in this area. " +
    "DayZ-ServerMan does not promise a number of infected in the area at any time.",
  wholeNumber: (label) => `${label} must be a whole number from 0 to 100.`,
  order: {s: "Static minimum (smin) must not be more than static maximum (smax).",
    d: "Dynamic minimum (dmin) must not be more than dynamic maximum (dmax)."},
  outside: "This point is outside the terrain bounds. Click inside the bounds to place an encounter.",
  radiusEditInvalid: (limit) => `Enter a radius from 1 to ${limit} m. The encounter keeps its spawn radius.`,
  centreInvalid: (bounds) => `Enter a centre inside the terrain bounds: X from ${bounds?.x_min} to ${bounds?.x_min + bounds?.width} m, ` +
    `Z from ${bounds?.z_min} to ${bounds?.z_min + bounds?.height} m. The encounter stays where it is.`,
});
