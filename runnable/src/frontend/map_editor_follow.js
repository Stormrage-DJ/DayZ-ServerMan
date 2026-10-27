// Follow the operation lane from the editor (8.18, Architect 2026-10-15 15:46:42, ruling 6). While the document is
// visible, it reads the events every 1.5 s and publishes the lane-wide busy state. It reports each record, and each
// end of a map-affecting kind from any origin. The first read goes to the end of the history and does not act.
"use strict";

// Kinds whose end changes the mission files or the map editor records. Each end state of a backup restore and a
// profile restore counts. operation() keeps their short form (AR-7, Architect 2026-10-16 14:33:32 in evidence 10.1).
const mapFollowKinds = Object.freeze(["APPLY_MISSION_MAP", "RESTORE_MISSION_MAP_ORIGINAL", "RESTORE_MISSION_MAP_PREVIOUS",
  "APPLY_MEDICAL_FEATURE", "RESTORE_BACKUP", "RESTORE_PROFILE_FROM_BACKUP", "ADOPT_MISSION_MAP"]);
// States in which an operation waits for or holds the lane
const mapFollowActive = Object.freeze(["ACCEPTED", "QUEUED", "RUNNING", "CANCELLING"]);
// The wait between two reads, and the page size of one read
const mapFollowTimes = Object.freeze({wait: 1500, page: 100});
// cursor: the next after_sequence, null before the first read; session: the lane session of the cursor;
// active: the records that wait or run; ended: the operations whose end was seen; handlers: {record, ended};
// polling: a read runs; published: the last published busy state.
const mapFollowState = {cursor: null, session: null, active: new Map(), ended: new Set(), handlers: null, timer: null,
  polling: false, published: ""};

// One events read: the page, or low (the cursor is older than the history) or high (it is past the newest event)
async function mapFollowRead(after) {
  try {
    return {page: await window.ServerManMapBridge.operationEvents(after)};
  } catch (error) {
    const code = error.result?.error?.code;
    if (code === "EVENT_CURSOR_EXPIRED") return {low: true};
    if (code === "INVALID_REQUEST") return {high: true};
    throw error;
  }
}

// Find the oldest cursor that the history accepts, so no kept event is skipped (Architect 2026-10-16 15:49:45).
// A refused read gives no end cursor. So the search doubles the step from a cursor that is too old, or halves
// the range under one that is too new. After the first accepted cursor, it halves the range below that cursor.
async function mapFollowFind(cursor, high) {
  let low = high ? -1 : cursor, top = high ? cursor : null, step = 1, found = null;
  for (let tries = 0; tries < 64; tries += 1) {
    if (found && found.cursor - low <= 1) return found;
    const upper = found ? found.cursor : top, probe = upper === null ? low + step : Math.floor((low + upper) / 2);
    step *= 2;
    const read = await mapFollowRead(probe);
    if (read.page) found = {cursor: probe, page: read.page};
    else if (read.low) low = probe;
    else top = probe;
  }
  throw new Error("The operation events could not be read.");
}

// Keep one record: active records hold the lane, an end is seen once. Returns true for the first end of a
// map-affecting kind. quiet: the first read, which only learns the state.
function mapFollowTrack(record, quiet = false) {
  const state = mapFollowState, id = record?.operation_id, known = state.active.get(id);
  if (typeof id !== "string" || state.ended.has(id) || (known && Number(known.revision) > Number(record.revision))) return false;
  const active = mapFollowActive.includes(record.state);
  if (active) state.active.set(id, record);
  else {
    state.active.delete(id);
    state.ended.add(id);
  }
  if (!quiet) state.handlers.record(record);
  return !quiet && !active && mapFollowKinds.includes(record.kind);
}

// Publish the lane-wide busy state with the label of the operation that holds the lane, as the main window does
function mapFollowPublish() {
  const state = mapFollowState, records = [...state.active.values()];
  const lead = records.find((record) => ["RUNNING", "CANCELLING"].includes(record.state)) ?? records[0] ?? null;
  const name = lead ? window.ServerManOperationLabels.kind(lead.kind).name : "", signature = `${Boolean(lead)}|${name}`;
  if (signature === state.published) return;
  state.published = signature;
  window.ServerManBusy.publish(Boolean(lead), name);
}

// Read every new event page. The first read learns only which operations still run. An expired cursor or a new
// session counts as one unknown change (ruling 6).
async function mapFollowStep() {
  const state = mapFollowState, bridge = window.ServerManMapBridge, first = state.cursor === null, last = new Map();
  let cursor = state.cursor ?? 0, unknown = false, ended = false;
  for (;;) {
    let page = null;
    const read = await mapFollowRead(cursor);
    if (read.page) page = read.page;
    else {
      ({cursor, page} = await mapFollowFind(cursor, read.high));
      unknown = !first;
    }
    if (state.session !== null && page.session_id !== state.session) {
      unknown = true;
      state.active.clear();
    }
    state.session = page.session_id;
    if (first) {
      for (const event of page.events) if (event.kind === "state") last.set(event.operation_id, event.payload?.state);
    } else {
      for (const id of new Set(page.events.map((event) => event.operation_id))) ended = mapFollowTrack(await bridge.operation(id)) || ended;
    }
    cursor = page.next_cursor;
    if (page.events.length < mapFollowTimes.page) break;
  }
  for (const [id, value] of last) {
    if (mapFollowActive.includes(value)) mapFollowTrack(await bridge.operation(id), true);
    else state.ended.add(id);
  }
  state.cursor = cursor;
  mapFollowPublish();
  if (unknown || ended) state.handlers.ended(unknown);
}

// One poll; a failed read waits for the next one. The main window reports a host that does not answer.
async function mapFollowPoll() {
  const state = mapFollowState;
  clearTimeout(state.timer);
  state.timer = null;
  if (state.polling || document.hidden || !state.handlers) return;
  state.polling = true;
  try { await mapFollowStep(); } catch (_error) { /* The next poll reads again from the same cursor */ }
  finally {
    state.polling = false;
    if (!document.hidden) state.timer = setTimeout(mapFollowPoll, mapFollowTimes.wait);
  }
}

// Read a submitted operation at once, so the slot and the busy lock do not wait for the next poll
async function mapFollowAdopt(id) {
  try {
    const ended = mapFollowTrack(await window.ServerManMapBridge.operation(id));
    mapFollowPublish();
    if (ended) mapFollowState.handlers.ended(false);
  } catch (_error) { /* The next poll delivers the record */ }
}

// The editor starts the follower once with its handlers; a visible document reads at once
window.ServerManMapFollow = Object.freeze({
  start: (handlers) => {
    if (mapFollowState.handlers) return;
    mapFollowState.handlers = handlers;
    document.addEventListener("visibilitychange", () => { if (!document.hidden) void mapFollowPoll(); });
    void mapFollowPoll();
  },
  adopt: mapFollowAdopt, kinds: mapFollowKinds,
});
