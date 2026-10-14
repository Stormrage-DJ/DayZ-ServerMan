// Zone capacity presets, the Global container capacity slider and the effective texts of the evaluation (5.1).
"use strict";

// R08: the seven slider steps in percent; the stored global value is the step itself
const mapAbundanceSteps = Object.freeze([-50, -25, 0, 25, 50, 75, 100]);
// R07: the four presets in the order of the select; the labels are catalogue text of the wording module
const mapAbundancePresets = Object.freeze(["sparse", "normal", "rich", "very_rich"]);

// Texts of 5.1. The explanation is a copy of the backend CAPACITY_EXPLANATION (tests/test_mission_map_abundance.py).
const mapAbundanceWords = Object.freeze({
  global: "Global container capacity", zone: "Zone capacity", globalAction: "Change global container capacity",
  explanation: "Percentages change native container capacity, not item supply. More capacity may leave the live loot amount unchanged. Fixed dispatch loot is unchanged. Preview shows the supported capacity changes and native limits.",
  // The label names the value, as the Inspect row "Capacity change" does (UI-UX 2026-10-14 10:22:45)
  updating: "Capacity change: Updating",
  notEvaluated: (reason) => (reason ? `Capacity change: Not evaluated. ${reason}` : "Capacity change: Not evaluated"),
  // No space before the sign, as in "+50%"; zero has no sign
  signed: (value) => `${value > 0 ? "+" : ""}${value}%`,
  effective: (preset, global, effective) => `Capacity change: ${preset}; with global ${global}: ${effective}`,
});

const mapAbundanceState = {model: null};

// One element of the capacity controls
function mapAbundanceElement(id) {
  return document.getElementById(id);
}

// The shown slider position as text, also for assistive technology; it is not yet an edit
function mapAbundanceValue(slider) {
  const text = mapAbundanceWords.signed(Number(slider.value));
  mapAbundanceElement("map-global-capacity-value").textContent = text;
  slider.setAttribute("aria-valuetext", text);
}

// The effective text of a zone comes from the evaluation of the saved revision (D9). The window adds nothing:
// the preset label is catalogue text, and the global and effective values are the backend numbers. While the draft
// is newer, the text is "Updating" during a wait or a save, else "Not evaluated" with the reason of the refusal.
// Returns the text and whether it is a pending text, shown muted.
function mapAbundanceEffective(item, loot, status) {
  const words = mapAbundanceWords, autosave = window.ServerManMapAutosave;
  if (status.dirty) return [autosave.draft(status) === "updating" ? words.updating : words.notEvaluated(autosave.reason()), true];
  const evaluation = status.evaluation, preset = window.ServerManMapWording.capacityPresets[loot.abundance];
  const entry = Array.isArray(evaluation?.objects) ? evaluation.objects.find((object) => object?.id === item.id) : null;
  if (!entry || !preset || !Number.isInteger(evaluation.global) || !Number.isInteger(entry.effective_adjustment)) {
    return [words.notEvaluated(""), true];
  }
  return [words.effective(preset, words.signed(evaluation.global), words.signed(entry.effective_adjustment)), false];
}

// Follow the draft, the selection and the save state. The slider shows the global value of the draft.
// The Zone capacity row shows the selected loot zone, or the enabled linked loot of the selected encounter (7.4).
// A locked item or a draft that cannot change disables them.
function mapAbundanceRender() {
  const model = mapAbundanceState.model;
  if (!model) return;
  const status = model.status(), slider = mapAbundanceElement("map-global-capacity");
  const global = status.plan?.global_abundance;
  // A slider that the pointer still holds keeps the admin's position until the release, which is the edit
  if (!slider.matches(":active")) slider.value = String(mapAbundanceSteps.includes(global) ? global : 0);
  slider.disabled = !status.plan || !model.editable();
  mapAbundanceValue(slider);
  const id = window.ServerManMapLayers.selected();
  const item = id ? status.plan?.objects?.find((object) => object.id === id) ?? null : null;
  const loot = window.ServerManMapLinkedLoot.of(item);
  mapAbundanceElement("map-zone-capacity-row").hidden = !loot;
  if (!loot) return;
  const select = mapAbundanceElement("map-zone-capacity");
  select.value = loot.abundance;
  select.disabled = item.locked || !model.editable();
  const [text, pending] = mapAbundanceEffective(item, loot, status), line = mapAbundanceElement("map-zone-capacity-effective");
  line.textContent = text;
  line.classList.toggle("map-effective-pending", pending);
}

// The slider is an edit at its release (change): one step is one undo action. A refused or unchanged value
// shows the draft's value again.
function mapAbundanceGlobal(slider) {
  const model = mapAbundanceState.model, value = Number(slider.value);
  if (mapAbundanceSteps.includes(value) && model.setGlobal(mapAbundanceWords.globalAction, value)) return;
  const global = model.snapshot()?.global_abundance;
  slider.value = String(mapAbundanceSteps.includes(global) ? global : 0);
  mapAbundanceValue(slider);
}

// A preset choice is one undo action of the selected zone or linked loot; a refusal shows its reason in the Properties message
function mapAbundanceZone(select) {
  const id = window.ServerManMapLayers.selected(), model = mapAbundanceState.model;
  const item = id ? model.snapshot()?.objects.find((object) => object.id === id) : null;
  if (!window.ServerManMapLinkedLoot.of(item)) return;
  try {
    if (!mapAbundancePresets.includes(select.value)) throw new Error(window.ServerManMapWording.invalidAction);
    model.edit(id, window.ServerManMapLinkedLoot.patch(item, {abundance: select.value}));
  } catch (error) {
    mapAbundanceElement("map-properties-message").textContent = error.message;
    mapAbundanceRender();
  }
}

// Wire the labels, the explanation, the four presets and the slider once.
function mapAbundanceInit(model) {
  const state = mapAbundanceState, words = mapAbundanceWords;
  if (state.model) return;
  state.model = model;
  for (const [id, text] of [["map-global-capacity-title", words.global], ["map-zone-capacity-label", words.zone],
    ["map-global-capacity-explanation", words.explanation], ["map-zone-capacity-explanation", words.explanation]]) {
    mapAbundanceElement(id).textContent = text;
  }
  const select = mapAbundanceElement("map-zone-capacity"), slider = mapAbundanceElement("map-global-capacity");
  select.replaceChildren(...mapAbundancePresets.map((key) =>
    Object.assign(document.createElement("option"), {value: key, textContent: window.ServerManMapWording.capacityPresets[key]})));
  Object.assign(slider, {min: String(mapAbundanceSteps[0]), max: String(mapAbundanceSteps.at(-1)), step: "25"});
  slider.addEventListener("input", () => mapAbundanceValue(slider));
  slider.addEventListener("change", () => mapAbundanceGlobal(slider));
  select.addEventListener("change", () => mapAbundanceZone(select));
  mapAbundanceRender();
}

// The Properties render and the save status call render; the model owns the undo actions.
window.ServerManMapAbundance = Object.freeze({init: mapAbundanceInit, render: mapAbundanceRender,
  steps: mapAbundanceSteps, presets: mapAbundancePresets});
