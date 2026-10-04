// Operation bar: the one global indicator of manager operations. It keeps the page visible, holds results until
// they are dismissed, offers Cancel, and publishes the busy state that pages use to lock their mutating controls.
"use strict";

// States in which an operation still occupies or waits for the lane.
const OPERATION_ACTIVE_STATES = Object.freeze(["ACCEPTED", "QUEUED", "RUNNING", "CANCELLING"]);
// Bar state: active records, the two result slots, dismissals of this window session, and view flags.
const operationBarState = {
  active: new Map(), problem: null, success: null, settled: new Set(), dismissed: new Set(),
  seenPhases: new Map(), cancelling: new Set(), cancelErrors: new Map(),
  queueOpen: false, shutdown: false, stateUnknown: false,
  drawn: "", published: "", returnFocus: null,
};

// Name the profile that an operation belongs to, or return null when it is the selected one or unknown.
function operationTargetName(operation, always = false) {
  const id = operation.target_profile_id || operation.result?.profile_id
    || operation.result?.profile?.profile_id || null;
  const context = window.ServerManProfileContext;
  if (typeof id !== "string" || !id || (!always && id === context?.selectedId?.())) return null;
  const known = (context?.profiles?.() || []).find((profile) => profile.profile_id === id);
  return known ? known.display_name : id;
}

// Word the result of a finished operation with the phase that the bar saw last as a fallback.
function operationBarResult(operation) {
  return window.ServerManOperationMessages.result(
    operation, operationBarState.seenPhases.get(operation.operation_id) || null,
  );
}

// Return the operation of the active row: the running or cancelling one, else the one that waits longest.
function operationBarLead() {
  const records = [...operationBarState.active.values()];
  return records.find((record) => ["RUNNING", "CANCELLING"].includes(record.state))
    || records.sort((left, right) => String(left.accepted_at).localeCompare(String(right.accepted_at)))[0]
    || null;
}

// Apply one operation record to the bar state; report whether it changed anything.
function applyOperationRecord(operation) {
  const id = operation?.operation_id;
  if (typeof id !== "string" || operationBarState.settled.has(id)) return false;
  // An older copy of a known record never replaces a newer one.
  const known = operationBarState.active.get(id);
  if (known && Number(known.revision) > Number(operation.revision)) return false;
  operationBarState.stateUnknown = false;
  if (OPERATION_ACTIVE_STATES.includes(operation.state)) {
    // A start replaces the success of an earlier operation.
    operationBarState.active.set(id, operation);
    operationBarState.success = null;
    if (window.ServerManOperationLabels.isWorkingPhase(operation.progress_phase)) {
      operationBarState.seenPhases.set(id, operation.progress_phase);
    }
    return true;
  }
  // A finished operation leaves the lane and fills a result slot unless it was dismissed.
  operationBarState.settled.add(id);
  operationBarState.active.delete(id);
  operationBarState.cancelling.delete(id);
  operationBarState.cancelErrors.delete(id);
  if (operationBarState.dismissed.has(id)) return true;
  const look = operationBarResult(operation).look;
  // A cancelled or warning result never replaces a failed or recovery row that was not dismissed.
  const kept = operationBarState.problem && ["cancelled", "warning"].includes(look)
    && ["failed", "recovery"].includes(operationBarResult(operationBarState.problem).look);
  if (look === "success") operationBarState.success = operationBarState.active.size ? null : operation;
  else if (!kept) operationBarState.problem = operation;
  return true;
}

// Describe how the Cancel button of an operation is offered.
function operationCancelMode(operation) {
  if (operationBarState.shutdown || operation.cancellable !== true) return "none";
  return operation.state === "CANCELLING" || operationBarState.cancelling.has(operation.operation_id)
    ? "busy" : "ready";
}

// Build the plain model that the view draws.
function operationBarModel() {
  const labels = window.ServerManOperationLabels;
  const lead = operationBarState.stateUnknown ? null : operationBarLead();
  const others = lead ? [...operationBarState.active.values()].filter((record) => record !== lead) : [];
  const result = (operation) => {
    const value = operationBarResult(operation);
    return { id: operation.operation_id, look: value.look, sentence: value.sentence, message: value.message,
      target: operationTargetName(operation) };
  };
  let active = null;
  if (lead) {
    const phase = lead.state === "CANCELLING" ? { text: "Stopping at the next safe moment", determinate: false }
      : labels.phase(lead.kind, lead.progress_phase);
    const percent = Math.max(0, Math.min(100, Math.round(Number(lead.progress_percent) || 0)));
    active = {
      id: lead.operation_id, name: labels.kind(lead.kind).name, target: operationTargetName(lead),
      look: lead.state === "RUNNING" ? "running" : lead.state === "CANCELLING" ? "cancelling" : "queued",
      phase: phase.text, determinate: lead.state === "RUNNING" && phase.determinate, percent,
      waiting: operationBarState.shutdown ? 0 : others.length, cancel: operationCancelMode(lead),
      note: operationBarState.cancelErrors.get(lead.operation_id) || "",
    };
  }
  return {
    problem: operationBarState.problem ? result(operationBarState.problem) : null,
    active,
    success: operationBarState.success && !operationBarState.active.size
      ? result(operationBarState.success) : null,
    queueOpen: operationBarState.queueOpen && Boolean(active) && active.waiting > 0,
    queue: others.map((record) => ({ id: record.operation_id, name: labels.kind(record.kind).name,
      target: operationTargetName(record), state: labels.state(record.state),
      cancel: operationCancelMode(record) })),
  };
}

// Mirror the bar into the application status: the working operation, and a problem until it is dismissed.
// A cancelled result sets nothing. The two sources let the more severe one win while both exist.
function syncOperationBarStatus(model) {
  const lead = operationBarLead();
  if (lead) {
    window.ServerManUi.setHostStatus(
      `Working: ${window.ServerManOperationLabels.kind(lead.kind).name}`, "is-busy", "operation");
  } else {
    window.ServerManUi.clearHostStatus("operation");
  }
  const look = model.problem?.look;
  if (look === "recovery") {
    window.ServerManUi.setHostStatus("Recovery required", "is-recovery", "operation-result");
  } else if (look === "failed") {
    window.ServerManUi.setHostStatus("Last operation failed", "is-error", "operation-result");
  } else {
    window.ServerManUi.clearHostStatus("operation-result");
  }
}

// Publish the busy state: body marker, reason text, and the one event that locks page controls.
function publishOperationBusy() {
  const lead = operationBarLead();
  const busy = Boolean(lead) || operationBarState.shutdown;
  const name = lead ? window.ServerManOperationLabels.kind(lead.kind).name : "";
  const signature = `${busy}|${name}`;
  if (signature === operationBarState.published) return;
  operationBarState.published = signature;
  if (busy) document.body.dataset.operationBusy = "true";
  else delete document.body.dataset.operationBusy;
  const reason = document.getElementById("busy-reason");
  if (reason) {
    reason.textContent = !busy ? "" : name ? `Not available while \u201C${name}\u201D is in progress.`
      : "Not available while DayZ-ServerMan is closing.";
  }
  document.dispatchEvent(new CustomEvent("serverman:operation-change", { detail: { busy, name } }));
}

// Redraw the bar when a drawn value changed, then refresh the status and the busy state.
function refreshOperationBar() {
  const model = operationBarModel();
  const drawn = JSON.stringify(model);
  if (drawn !== operationBarState.drawn) {
    operationBarState.drawn = drawn;
    drawOperationBar(model);
    syncOperationBarStatus(model);
  }
  publishOperationBusy();
}

// Take one operation record from an event or a submit acknowledgement and announce what changed.
function syncOperationBar(operation) {
  if (!applyOperationRecord(operation)) return;
  if (OPERATION_ACTIVE_STATES.includes(operation.state)) {
    announceOperationProgress(operation, window.ServerManOperationLabels.kind(operation.kind).name,
      operationTargetName(operation, true),
      window.ServerManOperationLabels.phase(operation.kind, operation.progress_phase));
  }
  refreshOperationBar();
}

// Rebuild the bar from the operations of a snapshot, without announcing anything.
function resetOperationBar(operations) {
  operationBarState.active.clear(); operationBarState.settled.clear();
  operationBarState.problem = null; operationBarState.success = null;
  const records = Array.isArray(operations) ? operations : [];
  // Replay finished operations in the order in which they ended, then the active ones.
  records.filter((record) => !OPERATION_ACTIVE_STATES.includes(record.state))
    .sort((left, right) => String(left.finished_at).localeCompare(String(right.finished_at)))
    .forEach((record) => {
      applyOperationRecord(record);
      operationAnnounced.started.add(record.operation_id); operationAnnounced.finished.add(record.operation_id);
    });
  records.filter((record) => OPERATION_ACTIVE_STATES.includes(record.state)).forEach((record) => {
    applyOperationRecord(record);
    operationAnnounced.started.add(record.operation_id);
  });
  operationBarState.stateUnknown = false;
  refreshOperationBar();
}

// Announce the result of a finished operation after the visible page had its turn.
function settleOperationBar(operation) {
  if (!operation || OPERATION_ACTIVE_STATES.includes(operation.state)) return;
  announceOperationResult(operation, operationBarResult(operation));
}

// Read a just-submitted operation at once, so the bar and the busy lock do not wait for the next poll.
async function adoptOperation(operationId) {
  try {
    const result = await window.pywebview.api.get_operation(operationId);
    if (result?.success) syncOperationBar(result.value);
  } catch (_error) {
    // The next event poll delivers the record.
  }
}

// Give a page the result wording of the bar and note that the page announces this result itself.
function operationPageResult(operation) {
  markOperationAnnouncedByPage(operation.operation_id);
  return operationBarResult(operation);
}

// Ask the host to cancel one operation; a refused request is explained in the row.
async function cancelOperationFromBar(operationId) {
  if (operationBarState.cancelling.has(operationId)) return;
  operationBarState.cancelling.add(operationId);
  operationBarState.cancelErrors.delete(operationId);
  refreshOperationBar();
  let result = null;
  try { result = await window.pywebview.api.request_operation_cancellation(operationId); }
  catch (_error) { result = null; }
  operationBarState.cancelling.delete(operationId);
  if (result?.success) { syncOperationBar(result.value); }
  else if (operationBarState.active.has(operationId)) {
    operationBarState.cancelErrors.set(operationId,
      window.ServerManOperationMessages.bridgeError(result, "The operation could not be cancelled."));
  }
  refreshOperationBar();
}

// Remove a result row for this window session.
function dismissOperationResult(operationId) {
  operationBarState.dismissed.add(operationId);
  if (operationBarState.problem?.operation_id === operationId) operationBarState.problem = null;
  if (operationBarState.success?.operation_id === operationId) operationBarState.success = null;
  refreshOperationBar();
}

// Route a press on a bar button to its action.
function operationBarClicked(event) {
  const button = event.target.closest("[data-bar-control]");
  if (!button || button.getAttribute("aria-disabled") === "true") return;
  const [action, id] = button.dataset.barControl.split(":");
  if (action === "queue") { operationBarState.queueOpen = !operationBarState.queueOpen; refreshOperationBar(); }
  else if (action === "cancel") void cancelOperationFromBar(id);
  else if (action === "dismiss") dismissOperationResult(id);
  else if (action === "logs") window.ServerManLogs.showSource("manager");
}

// Handle the bar keys: F6 moves the focus into the bar and back; Escape closes the queue list or dismisses.
function operationBarKeys(event) {
  const bar = document.getElementById("operation-bar");
  if (!bar || bar.hidden || bar.closest("[inert]")) return;
  const inside = bar.contains(document.activeElement);
  if (event.key === "F6") {
    const first = bar.querySelector("button");
    if (!first) return;
    event.preventDefault();
    if (!inside) { operationBarState.returnFocus = document.activeElement; first.focus(); return; }
    // Return to the element that had the focus, or to the page when it is gone.
    const back = operationBarState.returnFocus;
    (back?.isConnected ? back : document.getElementById("main-content")).focus();
  } else if (event.key === "Escape" && inside) {
    event.preventDefault();
    if (operationBarState.queueOpen) { operationBarState.queueOpen = false; refreshOperationBar(); return; }
    // Dismiss the result row that holds the focus, or the only result row.
    const rows = [...bar.querySelectorAll(".operation-result")];
    const row = document.activeElement.closest(".operation-result") || (rows.length === 1 ? rows[0] : null);
    const dismiss = row?.querySelector('[data-bar-control^="dismiss:"]');
    if (dismiss) dismissOperationResult(dismiss.dataset.barControl.slice("dismiss:".length));
  }
}

// Wire the bar once: its buttons, its keys, and a redraw when the selected profile changes the target chip.
document.getElementById("operation-bar")?.addEventListener("click", operationBarClicked);
document.addEventListener("keydown", operationBarKeys);
document.addEventListener("serverman:profile-change", refreshOperationBar);

// Publish the bar to the shell loop, the shell panels, and the pages.
window.ServerManOperationBar = Object.freeze({
  sync: syncOperationBar,
  reset: resetOperationBar,
  settle: settleOperationBar,
  adopt: adoptOperation,
  pageResult: operationPageResult,
  isBusy: () => operationBarState.active.size > 0 || operationBarState.shutdown,
  // The application is closing: Cancel and the queue are hidden and every busy lock stays.
  shutdown: () => { operationBarState.shutdown = true; refreshOperationBar(); },
  // The host stopped answering: hide the rows whose state is no longer known.
  hostError: () => { operationBarState.stateUnknown = true; refreshOperationBar(); },
});
