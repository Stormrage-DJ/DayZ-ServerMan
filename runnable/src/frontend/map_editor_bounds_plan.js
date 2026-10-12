// Prepare explicit bounds changes without changing stored object geometry or records.
"use strict";

// D1 names and units remain the scalar validation authority for manual entry.
const mapBoundsFields = Object.freeze(["x_min", "z_min", "width", "height"]);

// Missing text is not zero; positive subnormal extents retain application precision.
function mapBoundsRead(fields) {
  const bounds = {}, problems = [];
  for (const name of mapBoundsFields) {
    const text = fields[name].value.trim(), value = text ? Number(text) : NaN;
    const origin = name.endsWith("_min");
    const valid = Number.isFinite(value) && (origin ? Math.abs(value) <= 1000000 : value > 0 && value <= 100000);
    fields[name].setAttribute("aria-invalid", String(!valid));
    if (!valid) problems.push(window.ServerManMapWording[origin ? "boundsOriginInvalid" : "boundsExtentInvalid"]);
    bounds[name] = value;
  }
  return {bounds: problems.length ? null : bounds, problems: [...new Set(problems)]};
}

// The same mission-derived spelling is used by the existing backend neutral-plan builder.
function mapBoundsTerrainId(value) {
  const known = value.capabilities?.terrain_id;
  if (known) return known;
  const name = String(value.mission_name || "").replace(/[^A-Za-z0-9._-]/g, "");
  return name ? ("unknown-" + name).slice(0, 64) : "unknown";
}

// Generate an eight-byte stable identifier once for the new manual candidate.
function mapBoundsIdentifier() {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

// Copy the loaded document or construct only the accepted neutral stored form.
function mapBoundsCandidate(value, bounds, source, identifier) {
  const plan = value.plan ? structuredClone(value.plan) : {
    format: "dayz-serverman/mission-map-plan", version: 1, plan_id: identifier,
    association: structuredClone(value.association),
    terrain: {terrain_id: mapBoundsTerrainId(value)}, global_abundance: 0, objects: [], background: null,
    records: {originals: [], baseline: null, applied: null},
  };
  plan.terrain = {...plan.terrain, bounds: structuredClone(bounds), bounds_source: source, bounds_confirmed: true};
  return plan;
}

// Only changed previously confirmed bounds with existing placements require this review.
function mapBoundsNeedsReview(value, bounds) {
  return Boolean(value.plan?.terrain?.bounds_confirmed && value.plan.objects?.length &&
    mapBoundsFields.some((name) => value.plan.terrain.bounds[name] !== bounds[name]));
}

// Native placement data is shown verbatim as text, with no reprojection or repair.
function mapBoundsReview(value, bounds, message = null) {
  const words = window.ServerManMapWording;
  return `${message ?? words.boundsReviewMessage}\n${words.boundsPrevious}: ${JSON.stringify(value.plan.terrain.bounds)}\n` +
    `${words.boundsProposed}: ${JSON.stringify(bounds)}\n` +
    value.plan.objects.map((object) => `${object.name} (${object.id}): ${JSON.stringify(object)}`).join("\n");
}

// These pure helpers never save, mutate a loaded object or derive Apply availability.
window.ServerManMapBoundsPlan = Object.freeze({fields: mapBoundsFields, read: mapBoundsRead,
  terrainId: mapBoundsTerrainId, identifier: mapBoundsIdentifier, candidate: mapBoundsCandidate,
  needsReview: mapBoundsNeedsReview, review: mapBoundsReview});
