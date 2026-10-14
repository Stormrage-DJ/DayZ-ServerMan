// Show the tier select of the selected loot zone and its effective and original tier sets, read on demand (4.6).
"use strict";

// options: the option list as text, so a render rebuilds it only when it changes.
// read: the display of the current tier read (T1 to T8), bound to its read key.
// timer: the 1 s wait before a percent can show.
const mapTierControlState = {model: null, id: null, options: null, read: null, timer: null};

// Only loot zones have a tier select here; the encounter tool comes with task 7
const mapTierControlKinds = Object.freeze(["loot_circle", "loot_polygon"]);

// One element of the tier control
function mapTierControlElement(id) {
  return document.getElementById(id);
}

// Polite announcements go to the shared live region of the editor
function mapTierControlAnnounce(text) {
  mapTierControlElement("map-announcement").textContent = text;
}

// "Inherit (original)" first, then the tiers that the value layer can hold. A stored tier that it cannot hold
// stays as a disabled option with its reason; a tier override says "(not writable)" while tier writes are off.
function mapTierControlOptions(item, capabilities) {
  const state = mapTierControlState, words = window.ServerManMapInspectWording, select = mapTierControlElement("map-tier-select");
  const tiers = capabilities?.holdable_tiers ?? [], stored = item.tier;
  const held = stored === null ? "" : tiers.find((tier) => tier.toLowerCase() === stored.toLowerCase()) ?? null;
  const writable = capabilities?.tier_writes?.available !== false;
  const options = [["", words.inherit, false], ...tiers.map((tier) => [tier, tier === held && !writable ? words.notWritable(tier) : tier, false])];
  if (held === null) options.push([stored, words.notHoldable(stored), true]);
  const signature = JSON.stringify([item.id, options]);
  if (signature !== state.options) {
    select.replaceChildren(...options.map(([value, text, disabled]) => {
      const option = document.createElement("option");
      Object.assign(option, {value, textContent: text, disabled});
      return option;
    }));
    state.options = signature;
    // The cause of "(not writable)" goes to the message that the select names (UI-UX 2026-10-14 00:15:02)
    mapTierControlElement("map-tier-message").textContent = held === null ? words.tierNotHoldable :
      (stored !== null && !writable ? words.writesCause(capabilities?.tier_writes?.reason) : "");
  }
  select.value = held ?? stored;
}

// Draw the read (T1 to T7): plain "Reading tiers", and the percent of the sampling rows after about 1 s.
// Then the result, the original set when it differs and the building capacity, or the failure with its actions.
function mapTierControlPaint() {
  const display = mapTierControlState.read, words = window.ServerManMapInspectWording, box = mapTierControlElement("map-tier-read");
  box.hidden = !display;
  mapTierControlElement("map-tier-capacity").hidden = display?.mode !== "done";
  if (!display) return;
  const reading = display.mode === "reading", result = display.mode === "done" ? display.result : null;
  const percent = reading && display.late ? display.progress : null;
  if (percent !== null) display.percent = true;
  box.setAttribute("aria-busy", String(reading));
  mapTierControlElement("map-tier-effective").textContent = reading ? words.reading(display.stale, percent) :
    (result ? words.effective(result.effective_tiers ?? []) : words.notRead(display.failure.reason));
  const original = mapTierControlElement("map-tier-original");
  original.hidden = !result || JSON.stringify(result.effective_tiers) === JSON.stringify(result.original_tiers);
  original.textContent = result ? words.original(result.original_tiers ?? []) : "";
  mapTierControlElement("map-tier-actions").hidden = display.mode !== "failed";
  mapTierControlElement("map-tier-again").hidden = !display.failure?.again;
  // Inspect a point shows only while the Inspect tool can be chosen, because it chooses the tool (T7, QA-4.6-F2)
  mapTierControlElement("map-tier-point").hidden = !window.ServerManMapTools.usable("inspect");
  // Object answers list the buildings that the zone covers, one entry per source group and adjustment (6.9)
  const entries = Array.isArray(result?.capacity) ? result.capacity : [];
  mapTierControlElement("map-tier-capacity-list").replaceChildren(...(result ? (entries.length ? entries.map(words.capacityEntry) :
    [words.capacityNone]) : []).map((text) => Object.assign(document.createElement("li"), {textContent: text})));
}

// The callbacks of one read; an answer for a display that a newer selection or edit replaced is not shown.
function mapTierControlHandlers(display) {
  const state = mapTierControlState, words = window.ServerManMapInspectWording, current = () => state.read === display;
  const objectName = (id) => state.model.snapshot()?.objects.find((item) => item.id === id)?.name ?? null;
  return {
    // A start, a restart after a lost job and a stale restart go back to the plain text; the 1 s wait starts again
    start: (stale) => {
      if (!current()) return;
      Object.assign(display, {mode: "reading", stale, progress: null, late: false});
      clearTimeout(state.timer);
      state.timer = setTimeout(() => { if (current()) { display.late = true; mapTierControlPaint(); } }, 1000);
      mapTierControlPaint();
    },
    // Only real sampling rows give a percent: floor(done / total × 100), at most 99 until the result arrives
    progress: (job) => {
      if (!current()) return;
      if (job.phase === "sampling" && Number.isInteger(job.rows_done) && Number.isInteger(job.rows_total) && job.rows_total > 0) {
        display.progress = Math.min(99, Math.floor(job.rows_done / job.rows_total * 100));
      }
      mapTierControlPaint();
    },
    // The result is announced only when the read showed a percent (T8)
    done: (result) => {
      if (!current()) return;
      clearTimeout(state.timer);
      Object.assign(display, {mode: "done", result});
      mapTierControlPaint();
      if (display.percent) mapTierControlAnnounce(words.effective(result.effective_tiers ?? []));
    },
    // A failure is announced once; the focus does not move (T7)
    failed: ({error, lost}) => {
      if (!current()) return;
      clearTimeout(state.timer);
      Object.assign(display, {mode: "failed", failure: words.failure(error, lost, objectName)});
      mapTierControlPaint();
      mapTierControlAnnounce(words.notRead(display.failure.reason));
    },
  };
}

// Follow the selection and the draft: a newer selection or edit replaces the read. A mission without a usable
// tier map shows its reason without a read. context is null while the editor loads or closes.
function mapTierControlRender(item, status, context) {
  const state = mapTierControlState, words = window.ServerManMapInspectWording;
  const loot = Boolean(item && mapTierControlKinds.includes(item.kind));
  mapTierControlElement("map-tier-row").hidden = !loot;
  state.id = loot ? item.id : null;
  if (!loot) state.options = null;
  else {
    mapTierControlOptions(item, status.capabilities);
    mapTierControlElement("map-tier-select").disabled = item.locked || !state.model.editable();
  }
  let request = null;
  if (loot && context && !status.capabilities?.header) {
    const missing = (status.capabilities?.unsupported ?? []).find((entry) => entry.operation === "tier_layer");
    const reason = (missing && window.ServerManMapWording.displayReason("tier", {reason: missing.reason})) ?? words.noTierMap;
    if (state.read?.key !== reason) state.read = {key: reason, mode: "failed", failure: {reason, again: false}};
  } else if (loot && context) {
    const key = `${item.id}\n${context.base}`;
    if (state.read?.key !== key) state.read = {key, mode: "reading", stale: false, progress: null, late: false, percent: false};
    request = {key, plan: context.plan, target: {object: item.id}, on: mapTierControlHandlers(state.read)};
  } else state.read = null;
  window.ServerManMapInspectRead.want("tier", request);
  mapTierControlPaint();
}

// Wire the select and the two failure actions once.
function mapTierControlInit(model) {
  const state = mapTierControlState, words = window.ServerManMapInspectWording, select = mapTierControlElement("map-tier-select");
  if (state.model) return;
  state.model = model;
  for (const [id, text] of [["map-tier-label", words.tierLabel], ["map-tier-again", words.readAgain],
    ["map-tier-point", words.inspectPoint], ["map-tier-capacity-title", words.capacityTitle]]) mapTierControlElement(id).textContent = text;
  // A tier choice is one undo action and an edit, so it starts a new read (T4)
  select.addEventListener("change", () => {
    if (!state.id) return;
    try { model.edit(state.id, {tier: select.value || null}); } catch (error) { mapTierControlElement("map-tier-message").textContent = error.message; }
  });
  // Read again keeps the focus in the tier control, because the action hides while the read runs
  mapTierControlElement("map-tier-again").addEventListener("click", () => {
    select.focus();
    window.ServerManMapInspectRead.retry("tier");
  });
  // Inspect a point chooses the Inspect tool and moves the focus to the map
  mapTierControlElement("map-tier-point").addEventListener("click", () => {
    const name = model.snapshot()?.objects.find((item) => item.id === state.id)?.name ?? "";
    window.ServerManMapTools.choose("inspect");
    mapTierControlElement("map-viewport").focus();
    window.ServerManMapTools.message(words.inspectHint(name));
  });
}

// The Inspect module calls render at each selection and draft change.
window.ServerManMapTierControl = Object.freeze({init: mapTierControlInit, render: mapTierControlRender});
