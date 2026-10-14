// Draw the canvas layers below the vector layer: terrain surface, image, then original tiers.
"use strict";

// Bottom-to-top order of the registered raster layers above the terrain surface.
const mapRasterOrder = Object.freeze(["image", "tiers"]);
const mapRasterState = {layers: new Map(), drawn: [], scratch: null};

// Read one design token of the editor document; colours are never hard-coded here.
function mapRasterToken(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// The 1-based value-flag index selects the tier colour; index 6 and above share one colour.
function mapRasterColour(index) {
  return mapRasterToken(index >= 1 && index <= 5 ? `--map-tier-${index}` : "--map-tier-other");
}

// Screen-space hatch of one tier in its full colour: dots, 45, 135, cross, horizontal or vertical lines.
function mapRasterPattern(context, index) {
  const tile = document.createElement("canvas");
  tile.width = 8;
  tile.height = 8;
  const pen = tile.getContext("2d"), colour = mapRasterColour(index);
  const kind = index >= 1 && index <= 5 ? index : 6;
  pen.strokeStyle = colour;
  pen.fillStyle = colour;
  pen.lineWidth = 1.5;
  const line = (points) => {
    pen.beginPath();
    for (const [x1, y1, x2, y2] of points) { pen.moveTo(x1, y1); pen.lineTo(x2, y2); }
    pen.stroke();
  };
  // Screen y grows downward, so a 45-degree line rises from the lower left to the upper right.
  if (kind === 1) { pen.beginPath(); pen.arc(4, 4, 1.6, 0, 2 * Math.PI); pen.fill(); }
  if (kind === 2 || kind === 4) line([[-1, 9, 9, -1], [-5, 5, 5, -5], [3, 13, 13, 3]]);
  if (kind === 3 || kind === 4) line([[-1, -1, 9, 9], [-5, 3, 5, 13], [3, -5, 13, 5]]);
  if (kind === 5) line([[0, 4, 8, 4]]);
  if (kind === 6) line([[4, 0, 4, 8]]);
  return context.createPattern(tile, "repeat");
}

// Reuse one device-pixel scratch canvas for masked layers.
function mapRasterScratch(width, height) {
  const scratch = mapRasterState.scratch ?? (mapRasterState.scratch = document.createElement("canvas"));
  if (scratch.width !== width) scratch.width = width;
  if (scratch.height !== height) scratch.height = height;
  const context = scratch.getContext("2d");
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.globalCompositeOperation = "source-over";
  context.globalAlpha = 1;
  context.clearRect(0, 0, width, height);
  return scratch;
}

// Redraw every raster layer from the view snapshot; stored geometry and calibration never change here.
function mapRasterDraw(view) {
  const canvas = document.getElementById("map-raster");
  const state = view.snapshot(), ratio = window.devicePixelRatio || 1;
  const width = Math.max(0, Math.round(state.width * ratio)), height = Math.max(0, Math.round(state.height * ratio));
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const context = canvas.getContext("2d");
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, width, height);
  mapRasterState.drawn = [];
  // The area outside the bounds keeps the viewport canvas colour.
  if (!state.bounds || !state.scale || state.failure || !width || !height) return;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const corner = view.toScreen({x: state.bounds.x_min, z: state.bounds.z_min + state.bounds.height});
  context.fillStyle = mapRasterToken("--color-surface-raised");
  context.fillRect(corner.x, corner.y, state.bounds.width * state.scale, state.bounds.height * state.scale);
  mapRasterState.drawn.push("terrain");
  for (const name of mapRasterOrder) {
    const layer = mapRasterState.layers.get(name);
    if (!layer) continue;
    context.save();
    // A layer returns true only when it drew something.
    if (layer({context, view, state, ratio, width, height})) mapRasterState.drawn.push(name);
    context.restore();
  }
}

// Draw a 1-bit mask over native (0, 0) to the world size: tinted fill at 0.55 and the full-colour hatch.
function mapRasterMask(frame, image, world, index) {
  const {context, view, state, ratio, width, height} = frame;
  const corner = view.toScreen({x: 0, z: world.height});
  if (![corner.x, corner.y].every(Number.isFinite)) return false;
  const scratch = mapRasterScratch(width, height), pen = scratch.getContext("2d");
  pen.globalAlpha = 0.55;
  pen.fillStyle = mapRasterColour(index);
  pen.fillRect(0, 0, width, height);
  pen.globalAlpha = 1;
  pen.fillStyle = mapRasterPattern(pen, index);
  pen.fillRect(0, 0, width, height);
  // Keep only the cells of the tier; no smoothing, so each cell stays a sharp square.
  pen.globalCompositeOperation = "destination-in";
  pen.imageSmoothingEnabled = false;
  pen.setTransform(ratio, 0, 0, ratio, 0, 0);
  pen.drawImage(image, corner.x, corner.y, world.width * state.scale, world.height * state.scale);
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.drawImage(scratch, 0, 0);
  return true;
}

// Layers register once; the current view comes from the viewport integration.
window.ServerManMapRaster = Object.freeze({
  register: (name, draw) => { if (mapRasterOrder.includes(name)) mapRasterState.layers.set(name, draw); },
  draw: mapRasterDraw,
  redraw: () => { const view = window.ServerManMapViewportUi.view(); if (view) mapRasterDraw(view); },
  mask: mapRasterMask, colour: mapRasterColour, pattern: mapRasterPattern,
  drawn: () => [...mapRasterState.drawn],
});
