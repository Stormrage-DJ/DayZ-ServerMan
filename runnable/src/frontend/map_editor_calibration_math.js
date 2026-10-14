// Solve and check image calibrations: X = a·px + b·py + c and Z = d·px + e·py + f (D1, 3.16).
"use strict";

// The D1 determinant floor; a smaller |a·e − b·d| cannot be inverted.
const mapCalibrationMinimum = 1e-12;

// A new image starts fitted to the terrain bounds, north up, with pixel (0, 0) at the north-west corner.
function mapCalibrationFit(bounds, width, height) {
  return {a: bounds.width / width, b: 0, c: bounds.x_min, d: 0, e: -bounds.height / height, f: bounds.z_min + bounds.height};
}

// Determinant of a 3 × 3 matrix given by rows.
function mapCalibrationDeterminant(rows) {
  const [[a, b, c], [d, e, f], [g, h, i]] = rows;
  return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
}

// Solve a to f exactly from three image points and their native coordinates (Cramer's rule).
function mapCalibrationSolve(points) {
  const [first, second, third] = points.map((point) => point.pixel);
  // Twice the triangle area in square pixels; points on one line cannot fix a plane mapping
  const area = (second.x - first.x) * (third.y - first.y) - (third.x - first.x) * (second.y - first.y);
  if (!(Math.abs(area) >= 1)) return {error: "collinear"};
  const matrix = points.map(({pixel}) => [pixel.x, pixel.y, 1]);
  const determinant = mapCalibrationDeterminant(matrix);
  const solve = (targets) => [0, 1, 2].map((column) => mapCalibrationDeterminant(
    matrix.map((row, index) => row.map((value, place) => (place === column ? targets[index] : value)))) / determinant);
  const [a, b, c] = solve(points.map(({native}) => native.x));
  const [d, e, f] = solve(points.map(({native}) => native.z));
  const calibration = {a, b, c, d, e, f};
  if (!Object.values(calibration).every(Number.isFinite) || Math.abs(a * e - b * d) < mapCalibrationMinimum) {
    return {error: "singular"};
  }
  return {calibration};
}

// Warnings do not block: shear above 1 degree, axis scales that differ by more than 2 %, or a mirror.
function mapCalibrationWarnings(value) {
  const {a, b, d, e} = value;
  const angle = Math.atan2(Math.abs(a * e - b * d), a * b + d * e) * 180 / Math.PI;
  const first = Math.hypot(a, d), second = Math.hypot(b, e);
  return {shear: Math.abs(angle - 90) > 1, scale: Math.abs(first - second) > 0.02 * Math.max(first, second),
    mirror: a * e - b * d > 0};
}

// Map an image pixel to native metres.
function mapCalibrationApply(value, pixel) {
  return {x: value.a * pixel.x + value.b * pixel.y + value.c, z: value.d * pixel.x + value.e * pixel.y + value.f};
}

// Map native metres back to the image pixel under the shown calibration.
function mapCalibrationInverse(value, native) {
  const determinant = value.a * value.e - value.b * value.d, x = native.x - value.c, z = native.z - value.f;
  return {x: (value.e * x - value.b * z) / determinant, y: (value.a * z - value.d * x) / determinant};
}

// Pure helpers: they never change a plan, a view or a stored calibration.
window.ServerManMapCalibrationMath = Object.freeze({fit: mapCalibrationFit, solve: mapCalibrationSolve,
  warnings: mapCalibrationWarnings, apply: mapCalibrationApply, inverse: mapCalibrationInverse});
