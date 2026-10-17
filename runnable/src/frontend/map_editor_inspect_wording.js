// Texts of the tier control, the tier read and the Inspect panel (4.6); backend sentences are exact copies.
"use strict";

// Exact copies of backend catalogue texts; tests.test_mission_map_inspect_wording compares them with Python
const mapInspectBackendTexts = Object.freeze({
  // CAPACITY_REASON_TEXTS point_target: a point selects no building (Product Owner 2026-10-13 15:19:49)
  pointCapacity: "Building capacity: inspect a zone to see the buildings it covers.",
  // KEPT_TEXTS planner_content: an affected planner placement keeps its capacity (6.8)
  plannerContent: "This building uses a PlannerRich_ group of an earlier planner tool. The map editor does not change planner content, so its capacity stays unchanged.",
  // UNAVAILABLE_TEXTS tier_not_holdable: a stored tier that the value layer cannot hold
  tierNotHoldable: "The tier map of this mission cannot hold this tier. Choose another tier or Inherit.",
});

// Why a building group of an object answer has no capacity; the codes come from the 6.7, 6.8 and 6.9 checks
// (UI-UX 2026-10-14 00:15:02 and 00:33:42)
const mapInspectCapacityReasons = Object.freeze({
  source_missing: "Their group is not in mapgroupproto.xml, so their capacity is not known.",
  source_ambiguous: "Their group name is in mapgroupproto.xml more than once, so their capacity is not known.",
  dirt_placement_not_movable: "They are in mapgroupdirt.xml, so Apply cannot change their capacity.",
  ceiling_unresolved: "Their capacity is not set in mapgroupproto.xml, so it cannot be changed.",
  default_missing: "mapgroupproto.xml has no default capacity for them, so their capacity cannot be changed.",
  default_duplicate: "mapgroupproto.xml has more than one default capacity for them, so their capacity cannot be changed.",
  ceiling_lexical: "A lootmax value of their group in mapgroupproto.xml is not a whole number, so their capacity cannot be changed.",
  container_ambiguous: "Their group has two containers with the same name, so their capacity cannot be changed.",
  ceiling_unsupported: "This adjustment would raise their capacity above the supported limit. Choose a smaller adjustment.",
  file_invalid: "A mapgroup file of this mission is not valid, so no building capacity is shown.",
  reserved_conflict: "A mapgroup file uses a group name that the map editor reserves, so no building capacity is shown.",
  placement_geometry: "A building placement has no usable position or rotation, so no building capacity is shown.",
});

// A capacity change in percent, without a space before the sign, as in "+50%"
function mapInspectSigned(value) {
  return `${value > 0 ? "+" : ""}${value}%`;
}

// The container ceilings in answer order: an unchanged one shows only its value; after four, the rest are counted
function mapInspectContainers(containers) {
  const shown = containers.slice(0, 4).map((item) => item.baseline === item.realized ? `${item.name} ${item.baseline}` :
    `${item.name} ${item.baseline} to ${item.realized}`).join(", ");
  return `Containers: ${shown}${containers.length > 4 ? `, and ${containers.length - 4} more.` : "."}`;
}

// The group capacity sentence of one building group (4.6); the Apply preview loot rows use it too (8.10)
function mapInspectGroupCapacity(group) {
  return `Group capacity: ${group.baseline} now, ${group.realized} with this plan.`;
}

// The reason of an entry without capacity; an ambiguous container is named when the answer names it
function mapInspectCapacityReason(entry) {
  if (entry.reason === "container_ambiguous" && entry.field) {
    return `Their group has more than one container named ${entry.field}, so their capacity cannot be changed.`;
  }
  return mapInspectCapacityReasons[entry.reason] ?? "The capacity of these buildings cannot be shown.";
}

// One building group of an object answer (6.9; UI-UX 2026-10-14 00:15:02 and 00:33:42). Order: head, group
// capacity, containers, point-limited containers, group bound, bound text, generated group.
function mapInspectCapacityEntry(entry) {
  const count = entry.placements, adjustment = entry.adjustment === null ? "" : ` at ${mapInspectSigned(entry.adjustment)}`;
  const head = `${entry.source ?? "Buildings"}: ${count} ${count === 1 ? "building" : "buildings"}${adjustment}.`;
  // A kept placement keeps its baseline capacity; its reason says why
  if (entry.kept) return `${head} ${entry.kept === "planner_content" ? mapInspectBackendTexts.plannerContent : "The capacity stays unchanged."}`;
  if (!entry.capacity) return `${head} ${mapInspectCapacityReason(entry)}`;
  // Capacity is a native upper limit, not a live item count (R08)
  const capacity = entry.capacity, group = capacity.group, containers = capacity.containers ?? [];
  const parts = [head, mapInspectGroupCapacity(group)];
  if (containers.length) parts.push(mapInspectContainers(containers));
  // The window names each point-limited container in place of the generic backend point text
  for (const item of containers.filter((container) => container.point_limited)) {
    parts.push(`The spawn points of ${item.name} allow ${item.point_bound} at most.`);
  }
  if (capacity.achievable !== null && capacity.achievable < group.realized) parts.push(`The spawn points allow ${capacity.achievable} at most.`);
  if (capacity.bound_text) parts.push(capacity.bound_text);
  if (capacity.generated_group) parts.push(`Apply writes them to group ${capacity.generated_group}.`);
  return parts.join(" ");
}

// The cause of "(not writable)" for a tier override while tier writes are unavailable (UI-UX 2026-10-14 00:15:02)
function mapInspectWritesCause(reason) {
  if (reason === "bounds_not_confirmed") return "Tiers cannot be written until the terrain bounds are confirmed. The effective tier is the original tier.";
  if (reason === "bounds_differ_from_header") return "Tiers cannot be written, because the terrain bounds differ from the tier map. The effective tier is the original tier.";
  return "Tiers cannot be written in this mission. The effective tier is the original tier.";
}

// A tier set in value-flag order; an empty set has no tier
function mapInspectTiers(tiers) {
  return tiers.length ? tiers.join(", ") : "None";
}

// The failure of a read: its reason and whether Read again can help (T7). An invalid plan shows Save's errors.
function mapInspectFailure(error, lost, nameOf) {
  if (lost) return {reason: "The read stopped.", again: true};
  const details = error?.result?.error?.details ?? {};
  // Invalid saved data refuses as Save does; the set-aside action is in the plan state (Product Owner 00:33:43)
  if (details.reason === "invalid_saved_data") return {reason: "The saved map plan is not valid.", again: false};
  if (details.reason === "invalid_document") {
    const problems = (details.problems ?? []).map((problem) => nameOf(problem?.object_id) ?
      `${nameOf(problem.object_id)}: ${problem.message}` : problem?.message).filter(Boolean);
    return {reason: ["The plan has an error. Correct it first.", ...problems].join(" "), again: false};
  }
  // Mission file and tier map refusals use the tier-layer reasons of the editor wording
  const reason = window.ServerManMapWording.displayReason("tier", details);
  return {reason: reason ?? error?.message ?? "The map editor received an invalid answer.", again: true};
}

// Public 4.6 wording; the editor wording keeps the texts of earlier leaves.
window.ServerManMapInspectWording = Object.freeze({
  ...mapInspectBackendTexts,
  tierLabel: "Tier", inherit: "Inherit (original)",
  notHoldable: (tier) => `${tier} (not holdable)`, notWritable: (tier) => `${tier} (not writable)`,
  // T3 and T5: plain "Reading tiers", after about 1 s with the percent of the sampling rows
  reading: (stale, percent) => `Effective tier: ${stale ? "The mission files changed. Reading tiers again" : "Reading tiers"}` +
    (percent === null ? "" : `, ${percent}%`),
  effective: (tiers) => `Effective tier: ${mapInspectTiers(tiers)}`,
  original: (tiers) => `Original: ${mapInspectTiers(tiers)}`,
  notRead: (reason) => `Effective tier: Not read. ${reason}`,
  noTierMap: "This mission has no usable tier map, so the effective tier cannot be read.", writesCause: mapInspectWritesCause,
  readAgain: "Read again", inspectPoint: "Inspect a point",
  inspectHint: (name) => `Click a point inside ${name} to see its tier.`,
  capacityTitle: "Building capacity", capacityNone: "This zone covers no buildings.",
  capacityEntry: mapInspectCapacityEntry, groupCapacity: mapInspectGroupCapacity, failure: mapInspectFailure, signed: mapInspectSigned, tiers: mapInspectTiers,
  // The Inspect panel: a point answer as terms and descriptions; progress shows only in the tier line (T1)
  inspectTitle: "Inspect", inspectTool: "Inspect", pointReading: "Reading tiers",
  pointStale: "The mission files changed. Reading tiers again",
  inspectIdle: "Choose Inspect (I) and click a point on the map.", cancelRead: "Cancel read",
  pointNotRead: (reason) => `Not read. ${reason}`,
  terms: Object.freeze({coordinates: "Coordinates", cell: "Tier map cell", original: "Original tier", effective: "Effective tier",
    winner: "Winning zone", capacity: "Capacity change", hidden: "Hidden zones", encounters: "Encounters"}),
  cell: (cell) => cell ? `Column ${cell.i}, row ${cell.j}` : "Outside the tier map",
  unknown: "Unknown", none: "None",
  fromZone: (tiers, name) => `${tiers} from ${name}`, fromOriginal: (tiers) => `${tiers} (original)`,
  notWritableZone: (name, tier) => `${name} sets ${tier}, but tiers cannot be written in this mission.`,
  winner: (name, hidden) => `${name}${hidden ? " (hidden on the map)" : ""}`,
  noWinner: "None. The original settings apply.",
  capacityOnly: "It changes only the capacity. Its tier is Inherit.",
  // A point read that a map click or Read again started is announced once (UI-UX 2026-10-14 00:15:02, item 6)
  pointAnnounce: (effective) => `Inspect: effective tier ${effective}.`,
  adjustment: (global, zone, effective) => `Global ${global}, ${zone}: ${effective}`,
  zone: (label) => `zone ${label}`, noZone: "no zone",
  hiddenCount: "They are hidden on the map and still count.",
});
