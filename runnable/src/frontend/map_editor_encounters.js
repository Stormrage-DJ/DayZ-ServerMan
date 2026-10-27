// Encounter availability from the capability snapshot, new encounter defaults, and the encounter hit tests (7.3).
"use strict";

// Providers of the Encounter type select, in this fixed order (UI-UX 10:22:15); wolves and bears come last (7.9)
const mapEncountersProviders = Object.freeze(["native_infected", "ai_bandits", "native_wolf", "native_bear"]);
// A selecting click within 12 CSS pixels of a ring line or a centre mark hits the encounter (UI-UX 10:22:15)
const mapEncountersReach = 12;
// Defaults of a new infected encounter; the event and the radius come from the mission and the tool (PO decision 1)
const mapEncountersPopulation = Object.freeze({smin: 0, smax: 0, dmin: 3, dmax: 5});

// One provider of the snapshot: placement availability, its reason text and the offered events in sorted order.
// Only native infected can place in this plan; AI_Bandits stays unavailable (customer decision A), and wolves and
// bears stay unavailable with the 7.9 text. The snapshot reason wins; the fixed text covers a snapshot without one.
function mapEncountersProvider(capabilities, provider) {
  const words = window.ServerManMapEncounterWording, entry = capabilities?.encounter_providers?.[provider];
  if (provider === "native_infected") {
    const events = Array.isArray(entry?.event_names) ? entry.event_names.filter((name) => typeof name === "string").sort() : [];
    const available = entry?.available === true && events.length > 0;
    return {provider, available, events,
      reason: available ? "" : words.infectedReason(entry?.reason ?? (entry ? "no_matching_events" : "snapshot_missing"))};
  }
  const fixed = {ai_bandits: words.banditReason, native_wolf: words.wolfBearReason, native_bear: words.wolfBearReason};
  const reason = typeof entry?.reason === "string" ? entry.reason : (fixed[provider] ?? "");
  return {provider, available: false, events: [], reason};
}

// The providers of the select, each with its availability
function mapEncountersList(capabilities) {
  return mapEncountersProviders.map((provider) => mapEncountersProvider(capabilities, provider));
}

// The R11 state of one stored encounter: unavailable with a reason, and the stored exclusion
function mapEncountersStatus(item, capabilities) {
  const provider = mapEncountersProvider(capabilities, item.provider);
  const event = item.properties?.event_name;
  let reason = provider.available ? "" : provider.reason;
  if (!reason && item.provider === "native_infected" && !provider.events.includes(event)) {
    reason = window.ServerManMapEncounterWording.notOffered(event);
  }
  return {unavailable: !provider.available || Boolean(reason), reason, excluded: item.excluded === true};
}

// A new infected encounter: first offered event, 0, 0, 3, 5 and the tool radius; linked loot is not enabled (D1)
function mapEncountersCreate(id, name, centre, radius, event) {
  return {id, name, kind: "encounter", visible: true, locked: false, centre: {x: centre.x, z: centre.z}, radius,
    provider: "native_infected", properties: {event_name: event, ...mapEncountersPopulation}, excluded: false,
    linked_loot: {enabled: false, tier: null, abundance: "rich", radius: null}};
}

// Pixel distance of a screen point from the ring line and the centre mark of one encounter
function mapEncountersGap(item, screen, view) {
  const centre = view.toScreen(item.centre), scale = view.snapshot().scale;
  const distance = Math.hypot(screen.x - centre.x, screen.y - centre.y);
  return Number.isFinite(item.radius) ? Math.min(distance, Math.abs(distance - item.radius * scale)) : distance;
}

// The visible encounter whose line or mark lies nearest to the point within reach; at equal distance the higher one
function mapEncountersHit(objects, point, view) {
  const screen = view.toScreen(point);
  let best = null, gap = mapEncountersReach;
  for (const item of objects) {
    if (item?.kind !== "encounter" || !window.ServerManMapVector.drawable(item)) continue;
    const distance = mapEncountersGap(item, screen, view);
    if (distance < gap || (distance === gap && !best)) { best = item; gap = distance; }
  }
  return best;
}

// A press that moves the selected encounter: inside its ring, or near the centre of one without a radius
function mapEncountersCovers(item, point, view) {
  if (Number.isFinite(item.radius)) return Math.hypot(point.x - item.centre.x, point.z - item.centre.z) <= item.radius;
  const screen = view.toScreen(point), centre = view.toScreen(item.centre);
  return Math.hypot(screen.x - centre.x, screen.y - centre.y) <= mapEncountersReach;
}

// Pure helpers over the snapshot and the view; the model owns every change
window.ServerManMapEncounters = Object.freeze({providers: mapEncountersProviders, provider: mapEncountersProvider,
  list: mapEncountersList, status: mapEncountersStatus, create: mapEncountersCreate, hit: mapEncountersHit,
  covers: mapEncountersCovers});
