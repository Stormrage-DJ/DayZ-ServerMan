// Advisory drawing-time geometry of the D1 rules; the backend D1 check stays authoritative.
"use strict";

// D1 spacing between two outline vertices, and the smallest outline area, in native metres
const mapOutlineSpacing = 0.001;
const mapOutlineArea = 1;

// Inclusive terrain bounds, as D1 checks each circle centre and polygon vertex
function mapOutlineInside(bounds, point) {
  return Boolean(bounds && point) && point.x >= bounds.x_min && point.x <= bounds.x_min + bounds.width &&
    point.z >= bounds.z_min && point.z <= bounds.z_min + bounds.height;
}

// Floating-point turn sign of a→b→c; edge cases can differ from the exact backend test (Architect 17:30:12)
function mapOutlineTurn(a, b, c) {
  return Math.sign((a.x - c.x) * (b.z - c.z) - (a.z - c.z) * (b.x - c.x));
}

// Point p lies inside the box of segment a–b; with a zero turn it lies on the segment
function mapOutlineBox(a, b, p) {
  return Math.min(a.x, b.x) <= p.x && p.x <= Math.max(a.x, b.x) && Math.min(a.z, b.z) <= p.z && p.z <= Math.max(a.z, b.z);
}

// Closed segments a–b and c–d share at least one point
function mapOutlineMeet(a, b, c, d) {
  const first = mapOutlineTurn(a, b, c), second = mapOutlineTurn(a, b, d);
  const third = mapOutlineTurn(c, d, a), fourth = mapOutlineTurn(c, d, b);
  if (first * second < 0 && third * fourth < 0) return true;
  return (first === 0 && mapOutlineBox(a, b, c)) || (second === 0 && mapOutlineBox(a, b, d)) ||
    (third === 0 && mapOutlineBox(c, d, a)) || (fourth === 0 && mapOutlineBox(c, d, b));
}

// Indices of edges that break a simple outline; with 3 or more points, edge n−1 is the closing edge
function mapOutlineCrossings(vertices) {
  const count = vertices.length, closed = count >= 3, edges = closed ? count : count - 1, bad = new Set();
  const end = (index) => vertices[(index + 1) % count];
  for (let first = 0; first < edges; first += 1) {
    for (let second = first + 1; second < edges; second += 1) {
      // Neighbouring edges share their common vertex; the turn test below checks them
      const neighbours = second === first + 1 || (closed && first === 0 && second === count - 1);
      if (!neighbours && mapOutlineMeet(vertices[first], end(first), vertices[second], end(second))) {
        bad.add(first);
        bad.add(second);
      }
    }
  }
  // Neighbouring edges on one line overlap when both other ends lie on the same side of the corner
  for (let corner = closed ? 0 : 1; corner < (closed ? count : count - 1); corner += 1) {
    const before = vertices[(corner + count - 1) % count], at = vertices[corner], after = end(corner);
    if (mapOutlineTurn(before, at, after) === 0 &&
        (after.x - at.x) * (before.x - at.x) + (after.z - at.z) * (before.z - at.z) > 0) {
      bad.add((corner + count - 1) % count);
      bad.add(corner);
    }
  }
  return bad;
}

// Absolute shoelace area of the closed outline
function mapOutlineAreaOf(vertices) {
  let total = 0;
  vertices.forEach((point, index) => {
    const next = vertices[(index + 1) % vertices.length];
    total += point.x * next.z - next.x * point.z;
  });
  return Math.abs(total) / 2;
}

// The wording key of the first local reason that refuses Finish, or null
function mapOutlineProblem(vertices) {
  if (vertices.length < 3) return "outlineNeedsPoints";
  if (mapOutlineCrossings(vertices).size) return "outlineCrosses";
  return mapOutlineAreaOf(vertices) < mapOutlineArea ? "outlineArea" : null;
}

// A new vertex closer than the D1 spacing to an existing vertex is refused
function mapOutlineTooClose(vertices, point) {
  return vertices.some((vertex) => Math.hypot(vertex.x - point.x, vertex.z - point.z) < mapOutlineSpacing);
}

// Selection hit test of one loot zone: on an edge counts as inside, else the even-odd rule
function mapOutlineContains(item, point) {
  if (item.kind === "loot_circle") return Math.hypot(item.centre.x - point.x, item.centre.z - point.z) <= item.radius;
  if (item.kind !== "loot_polygon") return false;
  let inside = false;
  item.vertices.forEach((a, index) => {
    const b = item.vertices[(index + 1) % item.vertices.length];
    if (mapOutlineTurn(a, b, point) === 0 && mapOutlineBox(a, b, point)) inside = true;
  });
  if (inside) return true;
  item.vertices.forEach((a, index) => {
    const b = item.vertices[(index + 1) % item.vertices.length];
    if ((a.z > point.z) !== (b.z > point.z) && point.x < a.x + (point.z - a.z) * (b.x - a.x) / (b.z - a.z)) inside = !inside;
  });
  return inside;
}

// Pure helpers: no draft, history, save or rendering state lives here.
window.ServerManMapOutline = Object.freeze({inside: mapOutlineInside, crossings: mapOutlineCrossings,
  problem: mapOutlineProblem, tooClose: mapOutlineTooClose, contains: mapOutlineContains});
