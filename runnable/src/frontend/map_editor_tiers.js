// Show the original mission tiers with display-only visibility and opacity controls.
"use strict";

// Display state lasts for this window; the plan and the mission files never change here.
const mapTiersState = {shown: true, opacity: 100, enabled: false, generation: 0, missionKey: null, tiers: [],
  reason: null, layers: new Map(), failures: new Map(), reading: false, visible: new Map()};

// Unassigned and the actual named Tier1 start hidden; unusual flag names keep their real order and visibility.
function mapTiersVisible(tier) {
  const key = tier === null ? null : tier.toLowerCase(), choices = mapTiersState.visible;
  if (!choices.has(key)) choices.set(key, key !== null && key !== "tier1");
  return choices.get(key);
}

// The capability read names why a mission has no usable tier layer.
function mapTiersReason(value) {
  const capabilities = value?.capabilities;
  const missing = (capabilities?.unsupported ?? []).find((item) => item.operation === "tier_layer");
  if (missing) return window.ServerManMapWording.displayReason("tier", {reason: missing.reason}) ?? missing.reason;
  return capabilities?.header && capabilities.holdable_tiers?.length ? null : window.ServerManMapWording.tiersNone;
}

// Rebuild the legend: each tier with its swatch and pattern, or the reason that no layer shows.
function mapTiersLegend() {
  const state = mapTiersState, words = window.ServerManMapWording;
  const status = document.getElementById("map-tiers-status"), legend = document.getElementById("map-tiers-legend");
  const focused = document.activeElement?.matches("[data-map-tier-control]") ?
    {tier: document.activeElement.dataset.mapTierControl, unassigned: document.activeElement.dataset.mapUnassigned} : null;
  document.getElementById("map-tiers-toggle").checked = state.shown;
  legend.hidden = !state.shown || Boolean(state.reason);
  legend.replaceChildren();
  status.textContent = !state.shown ? words.tiersHidden : (state.reason ?? (state.reading ? words.tiersReading : ""));
  if (legend.hidden) return;
  [...state.tiers, null].forEach((tier, position) => {
    const name = tier ?? "Unassigned", colour = tier === null ? 1 : position + 1;
    const item = document.createElement("li"), swatch = document.createElement("canvas");
    swatch.className = "map-tier-swatch";
    swatch.width = 18;
    swatch.height = 18;
    swatch.setAttribute("aria-hidden", "true");
    const pen = swatch.getContext("2d");
    pen.globalAlpha = 0.55;
    pen.fillStyle = window.ServerManMapRaster.colour(colour);
    pen.fillRect(0, 0, 18, 18);
    pen.globalAlpha = 1;
    pen.fillStyle = window.ServerManMapRaster.pattern(pen, colour);
    pen.fillRect(0, 0, 18, 18);
    const failure = state.failures.get(tier);
    item.dataset.mapTier = name;
    const label = window.ServerManMapDom.element("span", failure ? words.tierFailed(name, failure) : name,
      {class: "map-tier-name"});
    const accessible = tier === null ? "Unassigned (no tier flags)" : name;
    const shown = mapTiersVisible(tier), toggle = window.ServerManMapDom.element("button", "Visible", {
      type: "button", class: "button map-tier-visibility", "data-map-tier-control": tier ?? "", "data-map-unassigned": String(tier === null),
      "aria-label": `Visible: ${accessible}`, "aria-pressed": String(shown), title: `${shown ? "Hide" : "Show"} ${name}`});
    window.ServerManMapIcons.button(toggle, shown ? "visible" : "hidden");
    toggle.addEventListener("click", () => {
      state.visible.set(tier === null ? null : tier.toLowerCase(), !mapTiersVisible(tier));
      mapTiersLegend(); window.ServerManMapRaster.redraw(); mapTiersRead();
    });
    item.append(swatch, label, toggle);
    legend.append(item);
  });
  // Async reads and display toggles retain keyboard focus on the same real tier.
  if (focused) [...legend.querySelectorAll("button")].find((button) =>
    button.dataset.mapTierControl === focused.tier && button.dataset.mapUnassigned === focused.unassigned)?.focus();
}

// Decode one answer and check that the image has exactly the header cell grid.
async function mapTiersDecode(value) {
  const image = new Image();
  image.src = `data:image/png;base64,${value.png}`;
  await image.decode();
  if (image.naturalWidth !== value.columns || image.naturalHeight !== value.rows) throw new Error(window.ServerManMapWording.tiersChanged);
  return image;
}

// Read the missing layers one at a time; a newer load, toggle or close drops every late answer.
async function mapTiersRead() {
  const state = mapTiersState, token = state.generation;
  if (state.reading || !state.shown || !state.enabled || state.reason || !state.missionKey) return;
  state.reading = true;
  mapTiersLegend();
  try {
    for (const tier of [...state.tiers, ...(mapTiersVisible(null) ? [null] : [])]) {
      if (state.layers.has(tier) || state.failures.has(tier)) continue;
      try {
        const value = await window.ServerManMapBridge.readTierLayer(state.missionKey, tier);
        const image = await mapTiersDecode(value);
        if (token !== state.generation) return;
        // Every layer must come from the same areaflags.map content
        const other = state.layers.values().next().value;
        if (other && other.sha256 !== value.areaflags_sha256) {
          state.layers.clear();
          state.reason = window.ServerManMapWording.tiersChanged;
          return;
        }
        state.layers.set(tier, {image, sha256: value.areaflags_sha256,
          world: {width: value.world_width, height: value.world_height}});
      } catch (error) {
        if (token !== state.generation) return;
        const reason = error?.result?.error?.details?.reason;
        // A budget or header refusal applies to the whole mission, not only to this tier
        if (["over_budget", "tier_header_invalid", "limits_invalid", "areaflags_missing"].includes(reason)) {
          state.layers.clear();
          state.reason = window.ServerManMapWording.displayError("tier", error);
          return;
        }
        state.failures.set(tier, window.ServerManMapWording.displayError("tier", error));
      }
      mapTiersLegend();
      window.ServerManMapRaster.redraw();
    }
  } finally {
    if (token === state.generation) {
      state.reading = false;
      mapTiersLegend();
      window.ServerManMapRaster.redraw();
      // A user may enable Unassigned while the named-tier batch is already running.
      if (mapTiersVisible(null) && !state.layers.has(null) && !state.failures.has(null) && !state.reason) mapTiersRead();
    }
  }
}

// Draw each loaded tier in value-flag order; the toggle hides them without dropping the reads.
function mapTiersDraw(frame) {
  const state = mapTiersState;
  if (!state.shown || state.reason) return false;
  // Fade the complete tint and hatch together; the underlying image keeps its opacity.
  frame.context.globalAlpha = state.opacity / 100;
  let drawn = false;
  [...state.tiers, null].forEach((tier, position) => {
    const layer = state.layers.get(tier);
    if (layer && mapTiersVisible(tier)) drawn = window.ServerManMapRaster.mask(frame, layer.image, layer.world, tier === null ? 1 : position + 1) || drawn;
  });
  return drawn;
}

// Start a fresh set of reads for a new authoritative load; null clears the overlay.
function mapTiersLoad(value) {
  const state = mapTiersState;
  state.generation += 1;
  state.reading = false;
  state.layers.clear();
  state.failures.clear();
  state.missionKey = value?.association?.mission_key ?? null;
  state.tiers = value ? [...(value.capabilities?.holdable_tiers ?? [])] : [];
  state.reason = value ? mapTiersReason(value) : null;
  mapTiersLegend();
  window.ServerManMapRaster.redraw();
  mapTiersRead();
}

// The toggle changes display only; no plan, save or history action follows.
window.ServerManMapTiers = Object.freeze({
  init: () => {
    const toggle = document.getElementById("map-tiers-toggle");
    // Opacity is local display state, independent of draft changes and layer visibility.
    const opacity = document.getElementById("map-tiers-opacity");
    opacity.addEventListener("input", () => {
      const value = Number(opacity.value);
      if (!Number.isInteger(value) || value < 0 || value > 100 || value % 10 !== 0) return;
      mapTiersState.opacity = value;
      document.getElementById("map-tiers-opacity-value").textContent = `${value}%`;
      window.ServerManMapRaster.redraw();
    });
    document.getElementById("map-tiers-title").textContent = window.ServerManMapWording.originalTiers;
    document.getElementById("map-tiers-toggle-label").textContent = window.ServerManMapWording.originalTiers;
    document.getElementById("map-tiers-legend").setAttribute("aria-label", window.ServerManMapWording.tierLegend);
    toggle.addEventListener("change", () => {
      mapTiersState.shown = toggle.checked;
      mapTiersLegend();
      window.ServerManMapRaster.redraw();
      mapTiersRead();
    });
    window.ServerManMapRaster.register("tiers", mapTiersDraw);
    mapTiersLegend();
  },
  load: mapTiersLoad,
  // Closing drops running reads; a released close reads the missing layers again.
  enabled: (enabled) => {
    mapTiersState.enabled = enabled;
    if (!enabled) { mapTiersState.generation += 1; mapTiersState.reading = false; } else mapTiersRead();
  },
  shown: () => mapTiersState.shown,
  // Preserve the existing named-tier readiness contract; Unassigned reads lazily when shown.
  loaded: () => [...mapTiersState.layers.keys()].filter((tier) => tier !== null),
});
