// The own Apply or restore in the status area (8.18): the one-line slot, the full text on the map and Check again
// (UI-UX Designer 2026-10-15 15:58:12, sections 2 and 5). The main-window operation bar stays the only bar.
"use strict";

// checking: a recovery check runs, so a second activation does nothing. last: the last shown slot state. kept: the
// last recovery state while Check again shows, so the action keeps its text (UI-UX 2026-10-16 15:49:13, R1).
const mapPublishStatusState = {checking: false, last: null, kept: null};
// Slot looks; each state also has its words, so the colour is never the only signal
const mapPublishStatusLooks = Object.freeze(["muted", "done", "error", "recovery"]);

// One element of the slot
function mapPublishStatusElement(id) {
  return document.getElementById(id);
}

// Write the slot (one line, full text in its title) and the map notice. look: one of mapPublishStatusLooks.
// notice: "error" or "recovery" shows the full text on the map; null empties it. announce: once, in the live region.
function mapPublishStatusShow(text, look = "muted", {notice = null, announce = false} = {}) {
  const slot = mapPublishStatusElement("map-operation-status"), board = mapPublishStatusElement("map-operation-notice");
  mapPublishStatusState.last = {text, look, notice};
  mapPublishStatusElement("map-operation-item").hidden = false;
  slot.textContent = text;
  slot.title = text;
  for (const name of mapPublishStatusLooks) slot.classList.toggle(`map-operation-${name}`, name === look && name !== "muted");
  board.textContent = notice ? text : "";
  board.classList.toggle("map-operation-recovery", notice === "recovery");
  board.classList.toggle("map-operation-success", notice === "success");
  if (announce && text) mapPublishStatusElement("map-announcement").textContent = text;
}

// Check again shows while the editor knows of a mission map recovery block; it keeps the shown recovery text
function mapPublishStatusCheckable(visible) {
  mapPublishStatusElement("map-check-again").hidden = !visible;
  mapPublishStatusState.kept = visible ? mapPublishStatusState.last : null;
}

// No operation text is owed: the slot is empty, or it shows the kept recovery text while Check again shows
function mapPublishStatusIdle() {
  const kept = mapPublishStatusState.kept;
  if (kept && !mapPublishStatusElement("map-check-again").hidden) mapPublishStatusShow(kept.text, kept.look, {notice: kept.notice});
  else mapPublishStatusShow("");
  mapPublishStatusState.kept = kept;
}

// The reading text; while Check again shows, the notice keeps the recovery text that the action names
function mapPublishStatusReading() {
  const kept = mapPublishStatusState.kept, board = mapPublishStatusElement("map-operation-notice");
  const shown = kept && !mapPublishStatusElement("map-check-again").hidden;
  mapPublishStatusShow(window.ServerManMapPublishWords.reading);
  if (shown && kept.notice) {
    board.textContent = kept.text;
    board.classList.toggle("map-operation-recovery", kept.notice === "recovery");
  }
}

// Check again: cleared hides the action and empties the notice; deferred and still blocked keep both. The focus
// stays on the action, or goes to the map when the action hides (UI-UX section 5).
async function mapPublishStatusCheck() {
  const state = mapPublishStatusState, words = window.ServerManMapPublishWords, button = mapPublishStatusElement("map-check-again");
  if (state.checking) return;
  state.checking = true;
  let answer = null, failure = "";
  try { answer = await window.ServerManMapBridge.checkRecovery(); }
  catch (error) { failure = error.message; }
  finally { state.checking = false; }
  if (failure) mapPublishStatusShow(failure, "error", {notice: "error", announce: true});
  else if (answer?.deferred === true) mapPublishStatusShow(words.deferred, "recovery", {notice: "recovery", announce: true});
  else if (answer?.blocked === true) {
    mapPublishStatusShow(words.blockReason(answer.reason), "recovery", {notice: "recovery", announce: true});
  }
  if (failure || answer?.deferred === true || answer?.blocked === true) mapPublishStatusState.kept = mapPublishStatusState.last;
  else if (answer?.blocked === false) {
    const focused = document.activeElement === button;
    mapPublishStatusShow(words.cleared, "done", {announce: true});
    mapPublishStatusCheckable(false);
    if (focused) mapPublishStatusElement("map-viewport").focus();
  } else mapPublishStatusShow(window.ServerManMapWording.invalidAnswer, "error", {notice: "error", announce: true});
}

// Set the term and the action label once, and wire Check again
function mapPublishStatusInit() {
  const words = window.ServerManMapPublishWords, button = mapPublishStatusElement("map-check-again");
  mapPublishStatusElement("map-operation-term").textContent = words.term;
  button.textContent = words.checkAgain;
  button.addEventListener("click", () => { void mapPublishStatusCheck(); });
}

// The publication flow writes the slot; Check again runs here
window.ServerManMapPublishStatus = Object.freeze({init: mapPublishStatusInit, show: mapPublishStatusShow,
  checkable: mapPublishStatusCheckable, check: mapPublishStatusCheck, idle: mapPublishStatusIdle,
  reading: mapPublishStatusReading});
