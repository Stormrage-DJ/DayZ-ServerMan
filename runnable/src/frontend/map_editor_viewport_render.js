// Draw only the complete terrain, grid and screen-edge labels from view state.
"use strict";

// SVG attributes keep geometry dynamic without inline style attributes.
function mapViewportSvg(tag, attributes, text = "") {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  element.textContent = text;
  return element;
}

// Grid labels respect the local coordinate mode and shared axis precision.
function mapViewportEdgeLabel(axis, point, gridValue, state, mode) {
  const coordinates = window.ServerManMapCoordinates;
  const nativeDecimals = coordinates.precision(1 / state.scale, 0, 3);
  const gridDecimals = coordinates.precision(Math.max(10 / (state.scale * state.bounds.width),
    10 / (state.scale * state.bounds.height)), 1, 6);
  const native = `${point.toFixed(nativeDecimals)} m`;
  const grid = gridValue.toFixed(gridDecimals);
  return `${axis} ${mode === "Grid" ? grid : (mode === "Native" ? native : `${native} / ${grid}`)}`;
}

// Prefer endpoints, then keep only labels whose rendered text does not collide.
function mapViewportSpaceLabels(labels) {
  const candidates = [...labels.children];
  const endpoints = candidates.filter((label) => label.dataset.endpoint === "true");
  const kept = [];
  for (const label of [...endpoints, ...candidates.filter((item) => !endpoints.includes(item))]) {
    const box = label.getBoundingClientRect();
    const overlaps = kept.some((other) => box.left < other.right + 8 && box.right + 8 > other.left &&
      box.top < other.bottom + 4 && box.bottom + 4 > other.top);
    if (overlaps) label.remove();
    else kept.push(box);
  }
}

// Rebuild the plain vector layer; stored bounds and plan objects are never edited.
function mapViewportRender(view, mode) {
  const state = view.snapshot();
  const svg = document.getElementById("map-vector");
  const words = window.ServerManMapWording;
  const label = document.getElementById("map-view-label");
  if (state.failure && state.width > 0 && state.height > 0) {
    const axes = Object.entries(state.failure.axes).filter(([, reason]) => reason)
      .map(([axis, reason]) => `${axis.toUpperCase()}: ${reason}`).join(" ");
    label.textContent = `${words.conversionFailure} ${words.conversionActions} ${axes}`;
    return;
  }
  svg.setAttribute("viewBox", `0 0 ${state.width} ${state.height}`);
  svg.replaceChildren();
  label.textContent = !state.bounds ? words.unknownBounds :
    (state.scale ? words.plainTerrain : words.unavailableViewport);
  if (!state.bounds || !state.scale) return;

  // Boundaries follow native Z upward and remain complete under clipping and panning.
  const bounds = state.bounds;
  const first = view.toScreen({x: bounds.x_min, z: bounds.z_min + bounds.height});
  const last = view.toScreen({x: bounds.x_min + bounds.width, z: bounds.z_min});
  const width = bounds.width * state.scale, height = bounds.height * state.scale;
  svg.append(mapViewportSvg("rect", {id: "map-terrain-surface", x: first.x, y: first.y,
    width, height, class: "map-terrain-bounds"}));
  const lines = mapViewportSvg("g", {class: "map-grid-lines"});
  const labels = mapViewportSvg("g", {class: "map-grid-labels"});
  // Labels skip crowded intermediate ticks, while all ten grid intervals remain drawn.
  const spacing = mode === "Both" ? 130 : 85;
  const strideX = Math.max(1, Math.ceil(spacing / (width / 10)));
  const strideZ = Math.max(1, Math.ceil(20 / (height / 10)));
  for (let tick = 0; tick <= 10; tick += 1) {
    const x = first.x + (last.x - first.x) * tick / 10;
    const y = last.y - (last.y - first.y) * tick / 10;
    lines.append(mapViewportSvg("line", {x1: x, y1: first.y, x2: x, y2: last.y}),
      mapViewportSvg("line", {x1: first.x, y1: y, x2: last.x, y2: y}));
    if (x >= 0 && x <= state.width && (tick % strideX === 0 || tick === 10)) {
      labels.append(mapViewportSvg("text", {x: Math.max(70, Math.min(state.width - 70, x)),
        y: state.height - 6, "text-anchor": "middle", "data-endpoint": tick === 0 || tick === 10},
      mapViewportEdgeLabel("X", bounds.x_min + bounds.width * tick / 10, tick, state, mode)));
    }
    if (y >= 0 && y <= state.height && (tick % strideZ === 0 || tick === 10)) {
      labels.append(mapViewportSvg("text", {x: 6, y: Math.max(13, Math.min(state.height - 24, y)),
        "data-endpoint": tick === 0 || tick === 10},
        mapViewportEdgeLabel("Z", bounds.z_min + bounds.height * tick / 10, tick, state, mode)));
    }
  }
  svg.append(lines, labels);
  mapViewportSpaceLabels(labels);
}

// Rendering depends on view snapshots, not backend calls or mutable plan state.
window.ServerManMapViewportRender = Object.freeze({draw: mapViewportRender});
