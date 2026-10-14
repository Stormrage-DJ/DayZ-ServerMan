// The Encounter tool's options: the "Encounter type" select, its reason line, and the placement of one encounter (7.3).
"use strict";

// The chosen provider lasts for the window and survives a load again; it is not stored (UI-UX 10:22:15)
const mapEncounterToolState = {model: null, provider: null, options: ""};

// The chosen provider with its availability; the first choice is the first available provider, else the first one
function mapEncounterToolChoice() {
  const list = window.ServerManMapEncounters.list(window.ServerManMapTools.value()?.capabilities);
  const state = mapEncounterToolState;
  if (!state.provider && window.ServerManMapTools.value()?.capabilities) {
    state.provider = (list.find((entry) => entry.available) ?? list[0]).provider;
  }
  return list.find((entry) => entry.provider === state.provider) ?? list[0];
}

// Show the options and the reason; an unavailable choice marks the Encounter button with aria-disabled and its reason.
// Unavailable options stay enabled, so the keyboard can choose them and read their reason.
function mapEncounterToolRender(usable) {
  const words = window.ServerManMapEncounterWording, select = document.getElementById("map-encounter-provider");
  const list = window.ServerManMapEncounters.list(window.ServerManMapTools.value()?.capabilities), choice = mapEncounterToolChoice();
  const options = JSON.stringify(list.map((entry) => [entry.provider, entry.available]));
  if (options !== mapEncounterToolState.options) {
    mapEncounterToolState.options = options;
    select.replaceChildren(...list.map((entry) => {
      const option = document.createElement("option");
      option.value = entry.provider;
      option.textContent = entry.available ? words.name(entry.provider) : words.notAvailable(words.name(entry.provider));
      return option;
    }));
  }
  select.value = choice.provider;
  select.disabled = !usable;
  document.getElementById("map-encounter-provider-reason").textContent = choice.reason;
  const button = document.getElementById("map-tool-encounter");
  button.setAttribute("aria-disabled", String(!choice.available));
  if (choice.available) button.removeAttribute("aria-describedby");
  else button.setAttribute("aria-describedby", "map-encounter-provider-reason");
  return choice;
}

// A change to an unavailable provider ends an active Encounter tool and says why; an available one keeps it
function mapEncounterToolChange(select) {
  const tools = window.ServerManMapTools;
  mapEncounterToolState.provider = select.value;
  const choice = mapEncounterToolChoice();
  if (tools.active() === "encounter") {
    if (!choice.available) tools.end();
    tools.message(choice.available ? "" : choice.reason);
  }
  tools.refresh();
}

// One click places one infected encounter with the defaults of PO decision 1; it gives a refusal text or null
function mapEncounterToolPlace(point) {
  const tools = window.ServerManMapTools, words = window.ServerManMapEncounterWording, model = mapEncounterToolState.model;
  const bounds = tools.value().plan.terrain.bounds, choice = mapEncounterToolChoice();
  if (!choice.available) return choice.reason;
  if (!window.ServerManMapOutline.inside(bounds, point)) return words.outside;
  const radius = Math.min(tools.radius(), Math.max(bounds.width, bounds.height));
  model.add(words.addAction, window.ServerManMapEncounters.create(model.newId(), model.nextName("encounter"), point, radius,
    choice.events[0]));
  return null;
}

// Wire the select once; the tools module calls this from its own init
function mapEncounterToolInit(model) {
  const state = mapEncounterToolState, words = window.ServerManMapEncounterWording;
  if (state.model) return;
  state.model = model;
  document.getElementById("map-tool-encounter").textContent = words.tool;
  document.getElementById("map-encounter-provider-label").textContent = words.type;
  const select = document.getElementById("map-encounter-provider");
  select.addEventListener("change", () => mapEncounterToolChange(select));
}

// The tools module owns the tool state and the messages; this module owns the provider choice
window.ServerManMapEncounterTool = Object.freeze({init: mapEncounterToolInit, render: mapEncounterToolRender,
  choice: mapEncounterToolChoice, place: mapEncounterToolPlace});
