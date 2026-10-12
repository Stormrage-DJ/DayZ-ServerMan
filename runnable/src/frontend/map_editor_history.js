// Keep one draft-only history; authoritative save metadata never enters its snapshots.
"use strict";

// A new action branches at the current cursor, without reviving discarded redo entries.
function mapHistoryCreate() {
  let entries = [], position = 0, generation = 0;
  // Reload clears old branches and invalidates outstanding traversal previews.
  const reset = () => { entries = []; position = 0; generation += 1; };
  const push = (label, before, after) => {
    // A no-op preserves the branch; a real action discards only the current redo tail.
    if (JSON.stringify(before) === JSON.stringify(after)) return false;
    entries = entries.slice(0, position);
    entries.push({label, before: structuredClone(before), after: structuredClone(after)});
    position += 1;
    generation += 1;
    return true;
  };
  const peek = (direction) => {
    // Preview a copied target without moving the cursor before placement review.
    const index = direction === -1 ? position - 1 : position;
    if (index < 0 || index >= entries.length) return null;
    const entry = entries[index];
    return {label: entry.label, plan: structuredClone(direction === -1 ? entry.before : entry.after), generation};
  };
  const step = (direction, token) => {
    // Apply only a still-current preview after its explicit review succeeds.
    if (token !== generation || !peek(direction)) return false;
    position += direction;
    generation += 1;
    return true;
  };
  // Expose availability and position without exposing mutable history entries.
  return Object.freeze({reset, push, peek, step,
    state: () => ({position, length: entries.length, generation, undo: position > 0, redo: position < entries.length})});
}

// Snapshot copies keep callers from changing a past or future draft action.
window.ServerManMapHistory = Object.freeze({create: mapHistoryCreate});
