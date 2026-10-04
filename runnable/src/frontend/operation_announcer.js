// Operation announcements for assistive technology: start, cancelling, sign-in wait, progress steps, and results.
"use strict";

// Shortest pause between two progress announcements of one operation, in milliseconds.
const OPERATION_PERCENT_PAUSE = 20000;
// Per operation: what was announced already, so nothing is spoken twice.
const operationAnnounced = {
  started: new Set(), cancelling: new Set(), finished: new Set(), byPage: new Set(),
  phases: new Map(), steps: new Map(), percentAt: new Map(),
};

// Write one text into the polite or the assertive live element.
function speakOperation(text, assertive = false) {
  const node = document.getElementById(assertive ? "operation-alert" : "operation-announcer");
  if (node) node.textContent = text;
}

// Announce what changed for an active operation: its start, its cancellation, the sign-in wait, a progress step.
function announceOperationProgress(operation, name, target, phase, clock = Date.now) {
  const id = operation.operation_id;
  if (!operationAnnounced.started.has(id)) {
    operationAnnounced.started.add(id);
    speakOperation(target ? `${name} started for ${target}.` : `${name} started.`);
    return;
  }
  if (operation.state === "CANCELLING") {
    if (!operationAnnounced.cancelling.has(id)) {
      operationAnnounced.cancelling.add(id);
      speakOperation(`Cancelling ${name}.`);
    }
    return;
  }
  // The sign-in wait needs the operator in another window, so it is the one phase that is spoken.
  const earlier = operationAnnounced.phases.get(id);
  operationAnnounced.phases.set(id, operation.progress_phase);
  if (operation.progress_phase === "interactive_authentication" && earlier !== operation.progress_phase) {
    speakOperation(phase.text);
    return;
  }
  if (operation.state !== "RUNNING" || !phase.determinate) return;
  // Speak a passed quarter step, but not more often than the pause allows.
  const step = Math.min(3, Math.floor(Number(operation.progress_percent) / 25));
  const now = clock();
  if (step > (operationAnnounced.steps.get(id) || 0)
      && now - (operationAnnounced.percentAt.get(id) || -OPERATION_PERCENT_PAUSE) >= OPERATION_PERCENT_PAUSE) {
    operationAnnounced.steps.set(id, step);
    operationAnnounced.percentAt.set(id, now);
    speakOperation(`${name}: ${Math.round(Number(operation.progress_percent))} percent`);
  }
}

// Record that the visible page shows its own live notice for the result of this operation.
function markOperationAnnouncedByPage(operationId) {
  operationAnnounced.byPage.add(operationId);
}

// Announce the result of a finished operation once, unless the visible page announced it.
function announceOperationResult(operation, result) {
  const id = operation.operation_id;
  if (operationAnnounced.finished.has(id)) return;
  operationAnnounced.finished.add(id);
  // Drop the progress bookkeeping of the finished operation.
  ["phases", "steps", "percentAt"].forEach((key) => operationAnnounced[key].delete(id));
  if (operationAnnounced.byPage.has(id)) return;
  speakOperation(result.text, ["failed", "recovery"].includes(result.look));
}
