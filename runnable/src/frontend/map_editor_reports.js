// Keep report presentation separate from the existing inspection scheduler and selection model.
"use strict";
const mapReportsState = {started: false, tab: "capacity", target: null, key: null, signature: null, result: null, query: "", open: new Set()};

// Tabs change only local presentation and never cancel or start inspection work.
function mapReportsTab(tab) {
  const state = mapReportsState;
  if (state.tab !== tab) {
    state.query = ""; document.getElementById("map-report-search").value = "";
  }
  state.tab = tab;
  for (const key of ["capacity", "point"]) {
    document.getElementById(`map-report-tab-${key}`).setAttribute("aria-pressed", String(key === tab));
  }
  document.getElementById("map-capacity-report").hidden = tab !== "capacity";
  document.getElementById("map-inspect").hidden = tab !== "point";
  document.getElementById("map-report-search").disabled = tab !== "capacity";
  mapReportsDraw();
}

// Build explicit report controls once; all original status/actions retain their IDs.
function mapReportsInit() {
  const state = mapReportsState;
  if (state.started) return;
  state.started = true;
  const make = window.ServerManMapDom.element, body = document.getElementById("map-reports-body");
  const controls = make("div", "", {class: "map-report-controls"});
  for (const [key, label] of [["capacity", "Building capacity"], ["point", "Point inspection"]]) {
    const button = make("button", label, {id: `map-report-tab-${key}`, type: "button", class: "button"});
    button.addEventListener("click", () => mapReportsTab(key)); controls.append(button);
  }
  const search = make("input", "", {id: "map-report-search", type: "search", placeholder: "Search source, container or identifier",
    "aria-label": "Search building capacity report"});
  search.addEventListener("input", () => { state.query = search.value.trim().toLocaleLowerCase(); mapReportsDraw(true); });
  const clear = make("button", "Clear search", {type: "button", class: "button"});
  clear.addEventListener("click", () => { search.value = ""; state.query = ""; mapReportsDraw(true); search.focus(); });
  controls.append(search, clear); body.prepend(controls);
  const capacity = make("section", "", {id: "map-capacity-report", "aria-label": "Building capacity report"});
  const status = make("p", "Select a loot zone to read its building capacity.", {id: "map-report-status", role: "status"});
  const summary = make("p", "", {id: "map-report-summary", "aria-live": "polite", role: "status"}), caveat = make("p", "Capacity ceilings are upper limits, not live loot totals. Item supply can remain unchanged.", {id: "map-report-caveat"});
  capacity.append(status, summary, caveat, document.getElementById("map-tier-capacity")); body.append(capacity);
  const show = make("button", "View capacity report", {id: "map-view-capacity-report", type: "button", class: "button"});
  show.addEventListener("click", () => { mapReportsTab("capacity"); window.ServerManMapWorkspace.open("report", true); });
  document.getElementById("map-zone-capacity-row").append(show); mapReportsTab("capacity");
}

// Search the current answer only. Row identities include the complete read key and answer generation.
function mapReportsDraw(filtering = false) {
  const state = mapReportsState, make = window.ServerManMapDom.element, container = document.getElementById("map-tier-capacity-list");
  const focused = filtering ? document.activeElement?.closest?.("[data-report-details]")?.dataset.reportDetails ?? null : null;
  const entries = Array.isArray(state.result?.capacity) ? state.result.capacity : [];
  container.replaceChildren();
  document.getElementById("map-report-caveat").hidden = !state.result;
  document.getElementById("map-report-summary").textContent = "";
  if (!state.result) return;
  const shown = entries.map((entry, ordinal) => ({entry, ordinal})).filter(({entry}) => !state.query || window.ServerManMapReportRows.search(entry).includes(state.query));
  document.getElementById("map-report-summary").textContent = `${shown.length} of ${entries.length} source entries`;
  const provenance = state.result.plan_source;
  if (provenance) document.getElementById("map-report-summary").append(" · Plan source: ", window.ServerManMapReportRows.facts(provenance));
  const restoreFocus = () => {
    if (focused === null) return;
    const target = [...container.querySelectorAll("[data-report-details]")].find((node) => node.dataset.reportDetails === focused);
    (target?.querySelector("summary") ?? document.getElementById("map-report-search")).focus();
  };
  if (!shown.length) { restoreFocus(); container.append(make("p", entries.length ? "No matching entries." : window.ServerManMapInspectWording.capacityNone)); return; }
  const table = make("table", "", {class: "map-capacity-table"}), head = make("thead"), titles = make("tr");
  for (const text of ["Source", "Placements", "Adjustment", "Group now", "Group proposed", "Warnings and details"]) titles.append(make("th", text, {scope: "col"}));
  head.append(titles); table.append(head);
  const rows = make("tbody"), open = state.open;
  for (const {entry, ordinal} of shown) rows.append(window.ServerManMapReportRows.row(entry, ordinal, open.has(ordinal),
    (id, expanded) => { if (expanded) open.add(id); else open.delete(id); }));
  table.append(rows); container.append(table); restoreFocus();
}

// Replaced reads clear old rows immediately; rerenders of one complete answer retain search and disclosures.
function mapReportsRender(display, target = null) {
  const state = mapReportsState;
  if (!state.started) return;
  target ??= display?.result?.object_id ?? display?.target ?? display?.key?.split("\n")[0] ?? null;
  const result = display?.mode === "done" ? display.result : null, signature = JSON.stringify([target, display?.key, display?.mode, display?.stale, display?.progress, display?.late, result]);
  if (state.signature === signature) return;
  const changedTarget = state.target !== target;
  if (state.key !== display?.key || state.result !== result) state.open = new Set();
  state.target = target;
  state.signature = signature; state.key = display?.key; state.result = result;
  if (changedTarget) { state.query = ""; document.getElementById("map-report-search").value = ""; }
  const words = window.ServerManMapInspectWording;
  document.getElementById("map-report-status").textContent = !display ? "Select a loot zone to read its building capacity." :
    display.mode === "reading" ? words.reading(display.stale, display.late ? display.progress ?? null : null) : display.mode === "failed" ? words.notRead(display.failure.reason) : "";
  mapReportsDraw();
}

window.ServerManMapReports = Object.freeze({init: mapReportsInit, render: mapReportsRender, tab: mapReportsTab});
