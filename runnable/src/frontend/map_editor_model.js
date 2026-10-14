// Own exact draft content and shared actions, separately from authoritative saved metadata.
"use strict";

// D1 uses these exact case-sensitive prefixes for automatic editable names.
const mapModelPatterns = Object.freeze({loot_circle: "Loot circle", loot_polygon: "Loot polygon", encounter: "Encounter"});

// Python D1 trims these Unicode whitespace characters and counts Unicode code points.
function mapModelName(value) {
  if (typeof value !== "string") throw new Error(window.ServerManMapWording.invalidName);
  // Trim only the backend's whitespace set, without changing internal spelling.
  const whitespace = "[\\x09-\\x0d\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
  const name = value.replace(new RegExp(`^${whitespace}+|${whitespace}+$`, "g"), "");
  if (!name || [...name].length > 80 || /[\x00-\x1f\x7f-\x9f]/.test(name)) {
    throw new Error(window.ServerManMapWording.invalidName);
  }
  return name;
}

// Exact ASCII integer patterns use BigInt, including leading zeros and very large suffixes.
function mapModelNextName(kind, objects) {
  const prefix = mapModelPatterns[kind];
  if (!prefix) throw new Error(window.ServerManMapWording.invalidKind);
  const pattern = new RegExp(`^${prefix} ([0-9]+)$`);
  // Names of every object can contribute to the requested exact prefix.
  let highest = 0n;
  for (const item of objects) {
    const match = pattern.exec(item.name);
    if (match && match[0] === item.name && BigInt(match[1]) > highest) highest = BigInt(match[1]);
  }
  return `${prefix} ${highest + 1n}`;
}

// Refuse changed identifiers, invalid names and accidental edits to locked objects.
function mapModelCheck(before, after, added = 0, removed = null) {
  // Object actions cannot change canonical metadata or remove objects; only a placement adds one.
  const protectedPlan = (plan) => { const copy = structuredClone(plan); delete copy.objects; return JSON.stringify(copy); };
  if (protectedPlan(before) !== protectedPlan(after) || !Array.isArray(after.objects) ||
      after.objects.length !== before.objects.length + added || after.objects.length > 2000) {
    throw new Error(window.ServerManMapWording.invalidAction);
  }
  const identifiers = new Set();
  // Keep names valid and identifiers unique without repairing their spelling.
  for (const item of after.objects) {
    if (typeof item.id !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(item.id) ||
        /[^A-Za-z0-9_-]/.test(item.id) || identifiers.has(item.id.toLowerCase())) {
      throw new Error(window.ServerManMapWording.invalidIdentifier);
    }
    identifiers.add(item.id.toLowerCase());
    if (!mapModelPatterns[item.kind] || mapModelName(item.name) !== item.name) throw new Error(window.ServerManMapWording.invalidName);
  }
  for (const item of before.objects) {
    // A delete removes only its unlocked object; other IDs and kinds stay stable, also when moved in priority.
    if (item.id === removed) { if (item.locked) throw new Error(window.ServerManMapWording.objectLocked); continue; }
    const next = after.objects.find((candidate) => candidate.id === item.id);
    if (!next || next.kind !== item.kind) throw new Error(window.ServerManMapWording.invalidIdentifier);
    if (item.locked) {
      // Explicit flags and ordering remain available without unlocking geometry edits.
      const content = (object) => { const copy = structuredClone(object); delete copy.visible; delete copy.locked; return JSON.stringify(copy); };
      if (!next || content(item) !== content(next)) throw new Error(window.ServerManMapWording.objectLocked);
    }
  }
  const finite = (value) => {
    // Unsupported values must not become different JSON content during submission.
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error(window.ServerManMapWording.invalidAction);
    if (!["object", "number", "string", "boolean"].includes(typeof value)) throw new Error(window.ServerManMapWording.invalidAction);
    if (value && typeof value === "object") Object.values(value).forEach(finite);
  };
  finite(after);
}

// One model belongs to this editor document; all public snapshots are independent copies.
function mapModelCreate(changed, review) {
  // Saved content and metadata stay outside the undoable draft snapshots.
  const history = window.ServerManMapHistory.create();
  let draft = null, saved = null, metadata = null, enabled = false, epoch = 0, loads = 0;
  const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  // Local equality determines whether the saved comparison still describes this draft.
  const dirty = () => !equal(draft, saved);
  // Ordinary edits require a current writable association and usable object list.
  const writable = () => Boolean(enabled && metadata && !metadata.read_only && metadata.association?.mission_key);
  const listAvailable = () => Array.isArray(draft?.objects) && draft.objects.every((item) =>
    item && typeof item.id === "string" && typeof item.name === "string" && Boolean(mapModelPatterns[item.kind]));
  const editable = () => writable() && listAvailable() && !document.querySelector("dialog");
  // Callers receive independent copies, not references into the active draft.
  const snapshot = () => structuredClone(draft);
  const status = () => ({...structuredClone(metadata), plan: snapshot(), dirty: dirty(),
    draft_identifier: saved?.plan_id ?? null,
    enabled, writable: writable(), object_list_available: listAvailable(),
    draft_changes: dirty() ? null : metadata?.draft_changes ?? null, history: history.state()});
  const notify = () => changed(status());
  // Commit one guarded object action at the administrator's current history branch.
  const action = (label, mutate, added = 0, removed = null) => {
    if (!editable()) return false;
    const candidate = snapshot();
    mutate(candidate);
    // Refusal and no-op leave both the active draft and history cursor unchanged.
    mapModelCheck(draft, candidate, added, removed);
    if (!history.push(label, draft, candidate)) return false;
    draft = candidate;
    notify();
    return true;
  };
  const edit = (id, patch) => action(window.ServerManMapWording.propertyAction, (plan) => {
    // Deliberate property edits preserve identity and respect the object's lock.
    const item = plan.objects.find((object) => object.id === id);
    if (!item || item.locked || "id" in patch || "kind" in patch) throw new Error(window.ServerManMapWording.objectLocked);
    const fields = structuredClone(patch);
    if ("name" in fields) fields.name = mapModelName(fields.name);
    Object.assign(item, fields);
  });
  const flag = (id, name) => action(window.ServerManMapWording.flagAction, (plan) => {
    // Visibility and lock never remove an object's effective contribution.
    const item = plan.objects.find((object) => object.id === id);
    if (item) item[name] = !item[name];
  });
  const move = (id, direction) => action(window.ServerManMapWording.orderAction, (plan) => {
    // Reorder the actual priority list; its boundaries are not history actions.
    const index = plan.objects.findIndex((item) => item.id === id), target = index + direction;
    if (![-1, 1].includes(direction) || index < 0 || target < 0 || target >= plan.objects.length) return;
    [plan.objects[index], plan.objects[target]] = [plan.objects[target], plan.objects[index]];
  });
  const traverse = async (direction) => {
    // Retained creation history may redo from null without enabling ordinary no-plan edits.
    if (!writable() || document.querySelector("dialog")) return false;
    const target = history.peek(direction), token = epoch;
    if (!target) return false;
    // Explicit review precedes confirmed bounds traversal; cancellation retains the cursor.
    if (!(await review(snapshot(), target.plan))) return false;
    // A load, close or newer branch invalidates a pending traversal answer.
    if (token !== epoch || !writable() || !history.step(direction, target.generation)) return false;
    draft = structuredClone(target.plan);
    notify();
    return true;
  };
  const load = (value) => {
    // Authoritative reload replaces the draft and ends every previous history branch.
    epoch += 1;
    loads += 1;
    metadata = structuredClone(value);
    draft = structuredClone(value?.plan ?? null);
    saved = snapshot();
    history.reset();
    notify();
  };
  const adopt = (submitted, result) => {
    // The saved snapshot remains exactly what was submitted, not the newer object draft.
    saved = structuredClone(submitted);
    metadata = {...metadata, association: structuredClone(result.association), revision: result.revision,
      state: "VALID", draft_changes: result.draft_changes, interrupted_evidence: structuredClone(result.interrupted_evidence)};
    notify();
  };
  const acceptBounds = (submitted, result) => {
    // Merge accepted terrain into the current branch while retaining concurrent object edits.
    const candidate = draft ? snapshot() : structuredClone(submitted);
    candidate.terrain = structuredClone(submitted.terrain);
    history.push(window.ServerManMapWording.boundsAction, draft, candidate);
    draft = candidate;
    adopt(submitted, result);
  };
  const withdraw = (object) => {
    // A refused new object leaves the draft and its history step only while no later entry edits it
    const index = draft?.objects.findIndex((item) => item.id === object.id) ?? -1;
    if (index < 0 || !history.withdraw(object.id)) return false;
    draft.objects.splice(index, 1);
    notify();
    return true;
  };
  const setBackground = (label, background) => {
    // The image reference and its calibration change as one undo action; objects and bounds stay.
    if (!writable() || !draft || document.querySelector("dialog")) return false;
    const candidate = snapshot();
    candidate.background = structuredClone(background);
    if (!history.push(label, draft, candidate)) return false;
    draft = candidate;
    notify();
    return true;
  };
  return Object.freeze({load, snapshot, status, action, edit, move, acceptBounds, setBackground, adopt, withdraw,
    // A placement enters at the top of the list (index 0); a load or close changes the epoch, only a load the loads
    add: (label, object) => action(label, (plan) => plan.objects.unshift(structuredClone(object)), 1),
    remove: (label, id) => action(label, (plan) => { plan.objects = plan.objects.filter((item) => item.id !== id); }, -1, id),
    epoch: () => epoch, loads: () => loads,
    visible: (id) => flag(id, "visible"), lock: (id) => flag(id, "locked"),
    undo: () => traverse(-1), redo: () => traverse(1),
    enabled: (value) => { enabled = value; epoch += 1; notify(); },
    editable, nextName: (kind) => mapModelNextName(kind, draft?.objects ?? []),
    newId: () => {
      // Generate a fresh ID without rewriting any existing case-insensitive identity.
      let id;
      do { id = window.ServerManMapBoundsPlan.identifier(); }
      while (draft?.objects.some((item) => item.id.toLowerCase() === id));
      return id;
    }});
}

// Future geometry consumers use the same guarded action API without implementing tools here.
window.ServerManMapModel = Object.freeze({create: mapModelCreate, nextName: mapModelNextName, name: mapModelName});
