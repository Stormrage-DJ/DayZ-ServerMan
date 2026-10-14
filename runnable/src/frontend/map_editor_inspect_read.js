// Run the Inspect reads of this editor one at a time: start, continue and cancel, restarts and dropped answers (4.6).
"use strict";

// The backend keeps one live job per editor and a new start replaces the earlier job, so the window queues reads.
// reads: the wanted read of each consumer ("tier", "point"); current: the read that runs; job: its job ID;
// generation: changes when a read stops, also at an Apply or Restore outcome; stamp: orders the wanted reads.
const mapInspectReadState = {reads: new Map(), current: null, job: null, generation: 0, stamp: 0};

// The closed reason of a bridge refusal, or null
function mapInspectReadReason(error) {
  return error?.result?.error?.details?.reason ?? null;
}

// Stop the running read: its late answers are dropped, and its job is cancelled when the window knows it.
function mapInspectReadStop() {
  const state = mapInspectReadState;
  state.generation += 1;
  if (state.job) window.ServerManMapBridge.cancelInspection(state.job).catch(() => {});
  state.job = null;
  state.current = null;
}

// Start the newest wanted read while no read runs.
function mapInspectReadNext() {
  const state = mapInspectReadState;
  if (state.current) return;
  const next = [...state.reads.values()].filter((read) => read.phase === "wanted")
    .sort((first, second) => second.stamp - first.stamp)[0];
  if (next) mapInspectReadRun(next);
}

// End a read with its result or its failure, tell its consumer, then start the next read.
function mapInspectReadEnd(read, phase, value) {
  const state = mapInspectReadState;
  Object.assign(state, {current: null, job: null});
  read.phase = phase;
  read.on[phase](value);
  mapInspectReadNext();
}

// Call start, then continue until the answer is done or failed. A stale answer starts a fresh read (T5).
// The first lost job starts once more with a new snapshot, and a second one fails (T6). The window drops an
// answer of a replaced read and of a job from before the last Apply or Restore (A181 test 15, window part).
async function mapInspectReadRun(read) {
  const state = mapInspectReadState, bridge = window.ServerManMapBridge;
  const generation = ++state.generation;
  const live = () => generation === state.generation && state.reads.get(read.name) === read;
  Object.assign(state, {current: read, job: null});
  read.phase = "running";
  read.on.start(false);
  let job = null;
  for (;;) {
    let answer;
    try {
      answer = job ? await bridge.continueInspection(job) : await bridge.inspect(read.plan, read.target);
    } catch (error) {
      if (!live()) return;
      const lost = mapInspectReadReason(error) === "inspection_unknown";
      if (!lost || read.lost) {
        mapInspectReadEnd(read, "failed", {error, lost});
        return;
      }
      // The restart is a fresh start, with a new snapshot and a new freshness check
      Object.assign(read, {lost: true});
      job = state.job = null;
      read.on.start(false);
      continue;
    }
    if (!live()) {
      // A replaced job that still runs is cancelled; the next start also retires it in the backend
      if (answer?.status === "running") bridge.cancelInspection(answer.job.id).catch(() => {});
      return;
    }
    if (answer?.status === "running") {
      job = state.job = answer.job.id;
      read.on.progress(answer.job);
    } else if (answer?.status === "stale") {
      // A stale job is retired by the backend and needs no cancel
      job = state.job = null;
      read.on.start(true);
    } else {
      const done = answer?.status === "done";
      mapInspectReadEnd(read, done ? "done" : "failed", done ? answer.result :
        {error: new Error("The map editor received an invalid answer."), lost: false});
      return;
    }
  }
}

// A consumer names the read that it needs. The same key keeps the read and its answer; another key replaces it,
// and null removes it. on: start(stale), progress(job), done(result) and failed({error, lost}).
function mapInspectReadWant(name, request) {
  const state = mapInspectReadState, previous = state.reads.get(name);
  if (previous && request && previous.key === request.key) {
    previous.on = request.on;
    return false;
  }
  state.reads.delete(name);
  if (previous && state.current === previous) mapInspectReadStop();
  if (request) state.reads.set(name, {...request, name, phase: "wanted", lost: false, stamp: ++state.stamp});
  mapInspectReadNext();
  return Boolean(request);
}

// Read again after a failure or a result: the same request, with a fresh start.
function mapInspectReadRetry(name) {
  const state = mapInspectReadState, read = state.reads.get(name);
  if (!read || ["wanted", "running"].includes(read.phase)) return;
  Object.assign(read, {phase: "wanted", lost: false, stamp: ++state.stamp});
  mapInspectReadNext();
}

// An Apply or Restore outcome changes the mission files: the stop drops every answer of an earlier job, and
// each wanted read starts again. The Apply and Restore leaves of the window call this at their outcome.
function mapInspectReadOutcome() {
  const state = mapInspectReadState;
  mapInspectReadStop();
  // No earlier answer stays on screen, also for a read that waits behind the other one (QA-4.6-F1)
  for (const read of state.reads.values()) {
    Object.assign(read, {phase: "wanted", lost: false});
    read.on.start(false);
  }
  mapInspectReadNext();
}

// The consumers own their display; this module owns the job and the order of the reads.
window.ServerManMapInspectRead = Object.freeze({want: mapInspectReadWant, retry: mapInspectReadRetry,
  outcome: mapInspectReadOutcome, running: () => mapInspectReadState.current?.name ?? null});
