// Own session-only authored interior opacity without changing the plan or original overlays.
"use strict";

// Full group opacity preserves the existing base wash and hatch until the user changes this window.
const mapLayerDisplayState = {opacity: 100, started: false};

// Mount a labelled native slider beside the custom-object controls once per window.
function mapLayerDisplayInit() {
  const state = mapLayerDisplayState;
  if (state.started) return;
  state.started = true;
  const make = window.ServerManMapDom.element;
  const section = make("div", "", {class: "map-custom-opacity"});
  const label = make("label", "Custom layer opacity", {for: "map-custom-opacity"});
  const row = make("div", "", {class: "map-opacity-control"});
  const slider = make("input", "", {id: "map-custom-opacity", type: "range", min: "0", max: "100", step: "10", value: "100"});
  const output = make("output", "100%", {id: "map-custom-opacity-value", for: "map-custom-opacity"});
  slider.addEventListener("input", () => {
    const value = Number(slider.value);
    // Reject unsupported steps and restore the last actual display value.
    if (!Number.isInteger(value) || value < 0 || value > 100 || value % 10 !== 0) {
      slider.value = String(state.opacity); return;
    }
    // Keep the readout and rendered interiors bound to the same actual percentage.
    state.opacity = value; output.textContent = `${value}%`;
    window.ServerManMapViewportUi.redraw();
  });
  row.append(slider, output); section.append(label, row);
  document.getElementById("map-list").before(section);
}

window.ServerManMapLayerDisplay = Object.freeze({init: mapLayerDisplayInit, fraction: () => mapLayerDisplayState.opacity / 100});
