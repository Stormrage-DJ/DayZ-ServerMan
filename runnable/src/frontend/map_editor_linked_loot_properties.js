// Linked loot controls of Properties (7.4): the "Link loot to this encounter" checkbox with its note, and Detach.
// Layout, focus and announcements: UI-UX Designer 2026-10-14 12:30:09; texts: Product Owner 12:28:40.
"use strict";

const mapLinkedLootPropsState = {model: null};

// One element of these controls
function mapLinkedLootPropsElement(id) {
  return document.getElementById(id);
}

// The checkbox shows for every encounter with a spawn radius, in every state; Detach shows while the linked loot has
// an area. Off, the tier and Zone capacity rows are hidden by their own modules. locked also covers a read-only draft.
function mapLinkedLootPropsRender(item, locked) {
  const loot = window.ServerManMapLinkedLoot, offered = loot.offered(item), box = mapLinkedLootPropsElement("map-linked-loot-enable");
  mapLinkedLootPropsElement("map-linked-loot").hidden = !offered;
  if (offered) {
    box.checked = item.linked_loot?.enabled === true;
    box.disabled = locked;
  }
  const detach = mapLinkedLootPropsElement("map-linked-loot-detach");
  detach.hidden = !loot.area(item);
  detach.disabled = locked;
}

// A check or a clear is one undo action, and the focus stays on the checkbox. Nothing is announced. A refusal shows
// in the Properties message, and the checkbox shows the stored state again, as Exclude does.
function mapLinkedLootPropsEnable(box) {
  const item = window.ServerManMapProperties.item(), message = mapLinkedLootPropsElement("map-properties-message");
  if (item?.kind !== "encounter") return;
  let reason = null, done = false;
  try { done = window.ServerManMapLinkedLoot.set(mapLinkedLootPropsState.model, item.id, box.checked); } catch (error) { reason = error.message; }
  message.textContent = reason ?? "";
  if (!done) window.ServerManMapProperties.render();
}

// Detach: the encounter stays selected. The focus moves to the checkbox, which is now clear, or to the map when no
// checkbox shows. Then the notice goes to the panel, the map and the live region. A refusal keeps the focus on Detach.
function mapLinkedLootPropsDetach() {
  const item = window.ServerManMapProperties.item(), tools = window.ServerManMapTools, model = mapLinkedLootPropsState.model;
  if (item?.kind !== "encounter") return;
  try {
    const id = window.ServerManMapLinkedLoot.detach(model, item.id);
    if (!id) return;
    const circle = model.snapshot().objects.find((object) => object.id === id);
    const block = mapLinkedLootPropsElement("map-linked-loot");
    (block.hidden ? mapLinkedLootPropsElement("map-viewport") : mapLinkedLootPropsElement("map-linked-loot-enable")).focus();
    tools.message(window.ServerManMapLinkedLoot.words.detached(circle.name, item.name));
  } catch (error) {
    tools.message(error.message);
  }
}

// Wire the controls once; the encounter Properties module calls this from its own init
function mapLinkedLootPropsInit(model) {
  const state = mapLinkedLootPropsState, words = window.ServerManMapLinkedLoot.words;
  if (state.model) return;
  state.model = model;
  mapLinkedLootPropsElement("map-linked-loot-enable-label").textContent = words.enable;
  mapLinkedLootPropsElement("map-linked-loot-note").textContent = words.note;
  mapLinkedLootPropsElement("map-linked-loot-detach").textContent = words.detach;
  const box = mapLinkedLootPropsElement("map-linked-loot-enable");
  box.addEventListener("change", () => mapLinkedLootPropsEnable(box));
  mapLinkedLootPropsElement("map-linked-loot-detach").addEventListener("click", mapLinkedLootPropsDetach);
}

// The model owns the undo actions; the Properties modules own the selection display
window.ServerManMapLinkedLootProperties = Object.freeze({init: mapLinkedLootPropsInit, render: mapLinkedLootPropsRender});
