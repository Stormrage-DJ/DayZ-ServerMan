// Derive R03 coordinates and display precision without rounding stored geometry.
"use strict";

// Exact decimal literals preserve the accepted strict decade comparisons.
const mapCoordinateSteps = Object.freeze([1, 0.1, 0.01, 0.001, 0.0001, 0.00001, 0.000001]);

// Copy only real finite positive bounds; an unknown extent stays unavailable.
function mapCoordinateBounds(value) {
  if (!value || ![value.x_min, value.z_min, value.width, value.height].every(Number.isFinite) ||
      value.width <= 0 || value.height <= 0) return null;
  return Object.freeze({x_min: value.x_min, z_min: value.z_min, width: value.width, height: value.height});
}

// Detect native endpoints or centres that collapse at the application's numeric precision.
function mapCoordinateAxes(bounds) {
  const reason = (origin, extent) => {
    const end = origin + extent, centre = origin + extent / 2;
    return !Number.isFinite(end) || end <= origin ? "Native endpoint is not distinct and finite." :
      (centre <= origin || centre >= end ? "Native centre is not distinct from its endpoints." : null);
  };
  return Object.freeze({x: reason(bounds.x_min, bounds.width), z: reason(bounds.z_min, bounds.height)});
}

// Return a checked value; one failed axis never discards the other axis.
function mapCoordinateValue(value, failure) {
  return !failure && Number.isFinite(value) ? value : null;
}

// Normalize against the complete terrain, independently of the current view.
function mapCoordinateNormalized(point, bounds) {
  const axes = mapCoordinateAxes(bounds);
  return {u: mapCoordinateValue((point.x - bounds.x_min) / bounds.width, axes.x || !Number.isFinite(point.x)),
    v: mapCoordinateValue((point.z - bounds.z_min) / bounds.height, axes.z || !Number.isFinite(point.z))};
}

// Recover full-precision native metres from normalized terrain coordinates.
function mapCoordinateNative(point, bounds) {
  const axes = mapCoordinateAxes(bounds);
  return {x: mapCoordinateValue(bounds.x_min + bounds.width * point.u, axes.x || !Number.isFinite(point.u)),
    z: mapCoordinateValue(bounds.z_min + bounds.height * point.v, axes.z || !Number.isFinite(point.v))};
}

// Select the greatest permitted decimal count whose step strictly exceeds a pixel.
function mapCoordinatePrecision(displacement, minimum, maximum) {
  for (let decimals = maximum; decimals >= minimum; decimals -= 1) {
    if (displacement < mapCoordinateSteps[decimals]) return decimals;
  }
  return minimum;
}

// Round text only; both grid axes share the larger coordinate displacement.
function mapCoordinateDisplay(point, bounds, scale, mode) {
  const words = window.ServerManMapWording;
  if (!point || !bounds || !(scale > 0)) return words.unavailable;
  const nativeDecimals = mapCoordinatePrecision(1 / scale, 0, 3);
  const gridDecimals = mapCoordinatePrecision(Math.max(10 / (scale * bounds.width), 10 / (scale * bounds.height)), 1, 6);
  const grid = mapCoordinateNormalized(point, bounds);
  const axes = mapCoordinateAxes(bounds);
  const nativeText = (value, failure) => !failure && Number.isFinite(value) ? `${value.toFixed(nativeDecimals)} m` : words.unavailable;
  const gridText = (value) => value === null ? words.unavailable : (10 * value).toFixed(gridDecimals);
  const native = `${words.native} X ${nativeText(point.x, axes.x)}, Z ${nativeText(point.z, axes.z)}`;
  const normalized = `${words.grid} X ${gridText(grid.u)}, Z ${gridText(grid.v)}`;
  return mode === "Native" ? native : (mode === "Grid" ? normalized : `${native}\n${normalized}`);
}

// Coordinates and metre distances are presentation helpers, never save operations.
window.ServerManMapCoordinates = Object.freeze({
  bounds: mapCoordinateBounds, axes: mapCoordinateAxes, normalized: mapCoordinateNormalized, native: mapCoordinateNative,
  grid: (point, bounds) => {
    const normalized = mapCoordinateNormalized(point, bounds);
    return {h: normalized.u === null ? null : 10 * normalized.u, v: normalized.v === null ? null : 10 * normalized.v};
  },
  fromGrid: (point, bounds) => mapCoordinateNative({u: Number.isFinite(point.h) ? point.h / 10 : null,
    v: Number.isFinite(point.v) ? point.v / 10 : null}, bounds),
  precision: mapCoordinatePrecision, display: mapCoordinateDisplay,
  distance: (first, second) => Math.hypot(second.x - first.x, second.z - first.z),
  metres: (value, decimals = 0) => `${value.toFixed(decimals)} m`,
});
