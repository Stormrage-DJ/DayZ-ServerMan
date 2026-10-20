// Project each authoritative capacity entry without regrouping or multiplying ceilings.
"use strict";

// Every supplied field remains reachable, including every container and technical identifier.
function mapReportFacts(value) {
  const make = window.ServerManMapDom.element;
  if (value === null || value === undefined) return make("span", "Not supplied");
  if (typeof value !== "object") return make("span", typeof value === "boolean" ? (value ? "Yes" : "No") : String(value));
  if (Array.isArray(value)) {
    const list = make("ol");
    if (!value.length) return make("span", "None");
    for (const item of value) { const row = make("li"); row.append(mapReportFacts(item)); list.append(row); }
    return list;
  }
  const list = make("dl", "", {class: "map-report-facts"});
  for (const [key, item] of Object.entries(value)) {
    const row = make("div"), label = key.replaceAll("_", " ");
    row.append(make("dt", label[0].toUpperCase() + label.slice(1)));
    const detail = make("dd"); detail.append(mapReportFacts(item)); row.append(detail); list.append(row);
  }
  return list;
}

// Use the same warning projection for visible text and literal search.
function mapReportWarnings(entry) {
  const capacity = entry.capacity;
  const warnings = [];
  if (entry.kept || entry.reason || !capacity) warnings.push(window.ServerManMapInspectWording.capacityEntry(entry));
  if (capacity?.qualified) warnings.push("Qualified upper bound");
  if (capacity?.bound_text) warnings.push(capacity.bound_text);
  if (capacity?.fixed_dispatch) warnings.push("Fixed dispatch loot is unchanged.");
  if (capacity?.containers?.some((item) => item.point_limited)) warnings.push("Some containers are limited by spawn points.");
  return warnings;
}

// Summary columns are per source entry; placements are never a capacity multiplier.
function mapReportRow(entry, ordinal, expanded, changed) {
  const make = window.ServerManMapDom.element, capacity = entry.capacity, group = capacity?.group;
  const row = make("tr", "", {"data-report-row": String(ordinal)});
  const source = make("th", entry.source ?? "Buildings", {scope: "row"}); row.append(source);
  const adjustment = entry.adjustment == null ? "Not supplied" : window.ServerManMapInspectWording.signed(entry.adjustment);
  for (const value of [entry.placements, adjustment, group?.baseline ?? "Unknown", group?.realized ?? "Unknown"]) {
    row.append(make("td", String(value)));
  }
  const cell = make("td"), details = make("details", "", {"data-report-details": String(ordinal)});
  const warnings = mapReportWarnings(entry);
  details.append(make("summary", "Details"));
  if (expanded) details.open = true;
  details.addEventListener("toggle", () => changed(ordinal, details.open));
  details.append(mapReportFacts(entry));
  if (warnings.length) cell.append(make("p", warnings.join(" "), {class: "map-report-warning"}));
  cell.append(details); row.append(cell); return row;
}

window.ServerManMapReportRows = Object.freeze({row: mapReportRow, facts: mapReportFacts,
  search: (entry) => `${JSON.stringify(entry)} ${mapReportWarnings(entry).join(" ")}`.toLocaleLowerCase()});
