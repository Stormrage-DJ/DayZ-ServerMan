// Linked loot of an encounter (7.4): its loot fields, its loot area, enable and disable, Detach, and the excluded note.
"use strict";

// Texts of 7.4. The note addition is the Product Owner text of 2026-10-14 10:17:01. The label, the note, Detach and its
// notice are the Product Owner texts of 12:28:40. The history label is the design name of the action and is not shown.
const mapLinkedLootWords = Object.freeze({applied: "Its linked loot is still applied.", detachAction: "Detach loot zone",
  enable: "Link loot to this encounter", detach: "Detach loot zone",
  note: "Linked loot uses the centre and spawn radius of this encounter. It does not depend on kills or on what the encounter spawns.",
  detached: (circle, encounter) => `${circle} detached from ${encounter}. Undo links them again.`});
// Enabled linked loot starts as Rich and Inherit (R07, plan 7.4)
const mapLinkedLootStart = Object.freeze({tier: null, abundance: "rich"});

// An encounter can link loot only with a spawn radius; the D6 loot radius of AI_Bandits is not built in this plan
function mapLinkedLootOffered(item) {
  return item?.kind === "encounter" && typeof item.radius === "number" && Number.isFinite(item.radius);
}

// The loot fields (tier and abundance) of an item: a loot zone itself, or the enabled linked loot of an encounter.
// Disabled linked loot has none, so the tier and Zone capacity rows do not show for it.
function mapLinkedLootOf(item) {
  if (item?.kind === "loot_circle" || item?.kind === "loot_polygon") return item;
  return item?.kind === "encounter" && item.linked_loot?.enabled === true ? item.linked_loot : null;
}

// The model.edit patch that changes loot fields; an encounter keeps its other linked loot fields
function mapLinkedLootPatch(item, fields) {
  return item.kind === "encounter" ? {linked_loot: {...item.linked_loot, ...fields}} : {...fields};
}

// The loot area of an encounter with enabled linked loot: a loot circle on the encounter centre and spawn radius (D6).
// It has the encounter's identity and list position, so a move or a resize of the encounter moves it too (R10).
// A provider without a spawn radius can have stored linked loot, for example from an import. That loot uses its
// stored loot radius, as the backend evaluation does. So the map shows what Apply would use.
function mapLinkedLootArea(item) {
  if (item?.kind !== "encounter" || item.linked_loot?.enabled !== true) return null;
  const radius = mapLinkedLootOffered(item) ? item.radius : item.linked_loot.radius;
  if (typeof radius !== "number" || !Number.isFinite(radius)) return null;
  return {id: item.id, kind: "loot_circle", linked: true, visible: item.visible, centre: item.centre, radius,
    tier: item.linked_loot.tier, abundance: item.linked_loot.abundance};
}

// The stored item of the draft by ID
function mapLinkedLootItem(model, id) {
  return model.snapshot()?.objects.find((object) => object.id === id) ?? null;
}

// Enable or disable the linked loot of one encounter: one undo action. Enabling starts as Rich and Inherit;
// disabling keeps the stored fields. A locked encounter refuses (model.edit).
function mapLinkedLootSet(model, id, enabled) {
  const item = mapLinkedLootItem(model, id);
  if (!mapLinkedLootOffered(item)) throw new Error(window.ServerManMapWording.invalidAction);
  const loot = enabled ? {...item.linked_loot, ...mapLinkedLootStart, enabled: true} : {...item.linked_loot, enabled: false};
  return model.edit(id, {linked_loot: loot});
}

// Detach: one undo action inserts an independent loot circle directly above the encounter. The circle has the
// centre, radius, tier and capacity preset, so the loot priority stays the same (D6). The encounter keeps its linked
// loot fields, but its linked loot is no longer enabled. So a later move of the encounter does not move the circle.
// The new circle has an automatic name and is visible and unlocked, as a new circle is. Returns the new circle ID, or null.
function mapLinkedLootDetach(model, id) {
  const item = mapLinkedLootItem(model, id), words = window.ServerManMapWording;
  if (!mapLinkedLootArea(item)) throw new Error(words.invalidAction);
  if (item.locked) throw new Error(words.objectLocked);
  const circle = {id: model.newId(), name: model.nextName("loot_circle"), kind: "loot_circle", visible: true, locked: false,
    centre: {x: item.centre.x, z: item.centre.z}, radius: mapLinkedLootArea(item).radius, tier: item.linked_loot.tier,
    abundance: item.linked_loot.abundance};
  const done = model.action(mapLinkedLootWords.detachAction, (plan) => {
    const index = plan.objects.findIndex((object) => object.id === id);
    plan.objects[index].linked_loot = {...plan.objects[index].linked_loot, enabled: false};
    plan.objects.splice(index, 0, circle);
  }, 1);
  return done ? circle.id : null;
}

// The excluded note of Properties; enabled linked loot adds that it is still applied (Product Owner 10:17:01)
function mapLinkedLootNote(item, note) {
  return mapLinkedLootOf(item) && item.kind === "encounter" ? `${note} ${mapLinkedLootWords.applied}` : note;
}

// Pure helpers over one item and the model's guarded actions; the model owns every change and the undo history
window.ServerManMapLinkedLoot = Object.freeze({words: mapLinkedLootWords, offered: mapLinkedLootOffered, of: mapLinkedLootOf,
  patch: mapLinkedLootPatch, area: mapLinkedLootArea, set: mapLinkedLootSet, detach: mapLinkedLootDetach,
  note: mapLinkedLootNote});
