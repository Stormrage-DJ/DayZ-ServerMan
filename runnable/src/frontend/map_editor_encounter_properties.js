// Encounter Properties: the R11 availability block with Exclude, the type, the infected event and population (7.3).
"use strict";

const mapEncounterPropsState = {model: null, shown: null};
// The population fields, in focus order
const mapEncounterPropsKeys = Object.freeze(["smin", "smax", "dmin", "dmax"]);

// One element of this module by ID
function mapEncounterPropsElement(id) {
  return document.getElementById(id);
}

// The event options: offered names, exact and sorted; a stored name that is not offered stays selected and disabled
function mapEncounterPropsEvents(select, events, stored) {
  const words = window.ServerManMapEncounterWording, names = events.includes(stored) ? events : [stored, ...events];
  const wanted = JSON.stringify([names, stored]);
  if (select.dataset.options !== wanted) {
    select.dataset.options = wanted;
    select.replaceChildren(...names.map((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = events.includes(name) ? name : words.notOfferedOption(name);
      option.disabled = !events.includes(name);
      return option;
    }));
  }
  select.value = stored;
}

// Show the encounter parts of Properties for the selected item; other kinds hide them
function mapEncounterPropsRender(item, locked, capabilities) {
  const state = mapEncounterPropsState, words = window.ServerManMapEncounterWording, encounter = item?.kind === "encounter";
  const fields = mapEncounterPropsKeys.map((key) => mapEncounterPropsElement(`map-encounter-${key}`));
  if (state.shown !== (item?.id ?? null)) for (const field of fields) field.setAttribute("aria-invalid", "false");
  state.shown = item?.id ?? null;
  const status = encounter ? window.ServerManMapEncounters.status(item, capabilities) : null;
  const block = Boolean(status && (status.unavailable || status.excluded));
  mapEncounterPropsElement("map-encounter-availability").hidden = !block;
  mapEncounterPropsElement("map-encounter-provider-text").hidden = !encounter;
  const infected = encounter && item.provider === "native_infected";
  mapEncounterPropsElement("map-encounter-fields").hidden = !infected;
  // The linked loot checkbox and Detach follow the same item and lock (7.4)
  window.ServerManMapLinkedLootProperties.render(item, locked);
  if (!encounter) return;
  mapEncounterPropsElement("map-encounter-provider-text").textContent = words.providerText(words.name(item.provider));
  // An unavailable encounter shows its reason and the next step; an excluded one shows the excluded note (PO decision 3)
  mapEncounterPropsElement("map-encounter-reason").textContent = status.unavailable ? status.reason : "";
  // Enabled linked loot adds "Its linked loot is still applied." to the excluded note (7.4)
  mapEncounterPropsElement("map-encounter-next").textContent = status.excluded ? window.ServerManMapLinkedLoot.note(item, words.excludedNote) :
    (status.unavailable ? words.nextStep : "");
  const exclude = mapEncounterPropsElement("map-encounter-exclude");
  exclude.checked = status.excluded;
  exclude.disabled = locked;
  if (!infected) return;
  const provider = window.ServerManMapEncounters.provider(capabilities, item.provider), event = mapEncounterPropsElement("map-encounter-event");
  mapEncounterPropsEvents(event, provider.events, item.properties.event_name);
  event.disabled = locked;
  fields.forEach((field, index) => {
    field.disabled = locked;
    if (document.activeElement !== field) field.value = String(item.properties[mapEncounterPropsKeys[index]]);
  });
}

// One edit of the selected encounter is one undo action; a refusal keeps the stored value and shows the reason
function mapEncounterPropsCommit(field, patch) {
  const item = window.ServerManMapProperties.item();
  if (item?.kind !== "encounter") return;
  let reason = null;
  try { reason = patch(item); } catch (error) { reason = error.message; }
  field.setAttribute("aria-invalid", String(Boolean(reason)));
  document.getElementById("map-properties-message").textContent = reason ?? "";
  // A refused choice or check shows the stored value again; a refused typed value stays for correction (as in 4.5)
  const stored = window.ServerManMapProperties.item();
  if (reason && field.type !== "text") window.ServerManMapProperties.render();
  if (!reason && stored && field.type === "text") field.value = String(stored.properties[field.dataset.key]);
}

// A whole number from 0 to 100, and each minimum at most its maximum (PO decision 2)
function mapEncounterPropsPopulation(field) {
  const words = window.ServerManMapEncounterWording, key = field.dataset.key, text = field.value.trim();
  mapEncounterPropsCommit(field, (item) => {
    const value = Number(text);
    if (!/^[0-9]+$/.test(text) || value > 100) return words.wholeNumber(words.labels[key]);
    const properties = {...item.properties, [key]: value}, pair = key[0];
    if (properties[`${pair}min`] > properties[`${pair}max`]) return words.order[pair];
    mapEncounterPropsState.model.edit(item.id, {properties});
    return null;
  });
}

// Wire the controls once; the Properties module calls this from its own init
function mapEncounterPropsInit(model) {
  const state = mapEncounterPropsState, words = window.ServerManMapEncounterWording;
  if (state.model) return;
  state.model = model;
  window.ServerManMapLinkedLootProperties.init(model);
  for (const [id, text] of [["map-encounter-exclude-label", words.exclude], ["map-encounter-event-label", words.event],
    ["map-encounter-population-legend", words.population], ["map-encounter-population-note", words.populationNote]]) {
    mapEncounterPropsElement(id).textContent = text;
  }
  for (const key of mapEncounterPropsKeys) {
    const field = mapEncounterPropsElement(`map-encounter-${key}`);
    field.dataset.key = key;
    mapEncounterPropsElement(`map-encounter-${key}-label`).textContent = words.labels[key];
    field.addEventListener("change", () => mapEncounterPropsPopulation(field));
  }
  const event = mapEncounterPropsElement("map-encounter-event"), exclude = mapEncounterPropsElement("map-encounter-exclude");
  // A choice of another event is an explicit edit; the list never changes to another name by itself (R11)
  event.addEventListener("change", () => mapEncounterPropsCommit(event, (item) => {
    model.edit(item.id, {properties: {...item.properties, event_name: event.value}});
    return null;
  }));
  // A check excludes the encounter, a clear check includes it again; one change is one undo action
  exclude.addEventListener("change", () => mapEncounterPropsCommit(exclude, (item) => {
    model.edit(item.id, {excluded: exclude.checked});
    return null;
  }));
}

// The model owns the undo actions; the Properties module owns the selection display
window.ServerManMapEncounterProperties = Object.freeze({init: mapEncounterPropsInit, render: mapEncounterPropsRender});
