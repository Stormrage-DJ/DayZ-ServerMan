// Busy rule for page controls: while an operation runs or waits, marked controls are locked with an explanation.
// Pages mark a control once; this helper follows the operation-change event that publish() sends. The main-window
// operation bar and the map editor follower call publish() (8.18). The host stays authoritative.
"use strict";

// Identifier of the hidden element that holds the current reason text.
const BUSY_REASON_ID = "busy-reason";
// Name of the operation in the last operation-change event, for controls that are marked later.
let busyOperationName = "";
// The last published busy state; a document without the operation bar (the map editor) uses it.
let busyPublished = false;

// Read the current reason text that the shell wrote for locked controls.
function busyReasonText() {
  return document.getElementById(BUSY_REASON_ID)?.textContent || "";
}

// The current busy state: the operation bar when the document has one, else the last published state.
function busyNow() {
  return window.ServerManOperationBar ? window.ServerManOperationBar.isBusy() === true : busyPublished;
}

// The description IDs of a control
function busyDescriptionIds(control) {
  return (control.getAttribute("aria-describedby") || "").split(" ").filter(Boolean);
}

// Lock one marked control: its own flag, state, description and tooltip; a natively disabled control keeps its
// own tooltip. The flag holds whatever the page does with aria-disabled. The rule records each attribute that it
// adds, so it never removes a reason of the page (Architect 2026-10-16 16:50:12 in evidence 8.19).
function lockBusyControl(control) {
  const data = control.dataset;
  if (!("busyLocked" in data)) {
    data.busyLocked = "";
    data.busyTitle = control.getAttribute("title") ?? "";
    if (control.getAttribute("aria-disabled") !== "true") {
      control.setAttribute("aria-disabled", "true");
      data.busyAria = "";
    }
  }
  // The control's own description stays in front of the shared reason.
  const described = busyDescriptionIds(control);
  if (!described.includes(BUSY_REASON_ID)) {
    control.setAttribute("aria-describedby", [...described, BUSY_REASON_ID].join(" "));
    data.busyDescribed = "";
  }
  if (!control.disabled) control.setAttribute("title", busyReasonText());
}

// Unlock one marked control: remove only what the lock added, and give back the tooltip.
function unlockBusyControl(control) {
  const data = control.dataset;
  if (!("busyLocked" in data)) return;
  if ("busyAria" in data) control.removeAttribute("aria-disabled");
  if ("busyDescribed" in data) {
    const rest = busyDescriptionIds(control).filter((id) => id !== BUSY_REASON_ID);
    if (rest.length) control.setAttribute("aria-describedby", rest.join(" "));
    else control.removeAttribute("aria-describedby");
  }
  if (data.busyTitle) control.setAttribute("title", data.busyTitle);
  else control.removeAttribute("title");
  for (const key of ["busyLocked", "busyAria", "busyDescribed", "busyTitle"]) delete data[key];
}

// Show or remove the visible wait line above the buttons of an open dialog.
function syncBusyDialogLine(control, busy, name) {
  const row = control.closest(".action-row") || control.parentElement;
  const existing = row?.previousElementSibling?.classList.contains("busy-wait-line")
    ? row.previousElementSibling : null;
  if (!busy) { existing?.remove(); return; }
  const text = name ? `Wait: “${name}” is in progress.` : "Wait: DayZ-ServerMan is closing.";
  if (existing) { if (existing.textContent !== text) existing.textContent = text; return; }
  // A plain element, so the map editor document needs no shell helper (8.18)
  const line = document.createElement("p");
  line.className = "busy-wait-line";
  line.textContent = text;
  line.setAttribute("role", "status");
  row?.before(line);
}

// Apply the current busy state to one marked control.
function applyBusyControl(control, busy, name) {
  if (busy) lockBusyControl(control); else unlockBusyControl(control);
  if (control.dataset.busyLock === "dialog") syncBusyDialogLine(control, busy, name);
}

// Apply the current busy state to every marked control in the document.
function applyBusyState(detail = null) {
  const busy = detail ? detail.busy === true : busyNow();
  if (detail) busyOperationName = detail.name || "";
  const name = busyOperationName;
  document.querySelectorAll("[data-busy-lock]").forEach((control) => applyBusyControl(control, busy, name));
}

// Publish the busy state: the reason text, then the one event that locks marked controls (Architect 2026-10-15
// 15:46:42, ruling 6). name: the label of the operation that holds the lane; empty while the application closes.
function publishBusyState(busy, name = "") {
  busyPublished = busy === true;
  const reason = document.getElementById(BUSY_REASON_ID);
  if (reason) {
    reason.textContent = !busyPublished ? "" : name ? `Not available while “${name}” is in progress.`
      : "Not available while DayZ-ServerMan is closing.";
  }
  document.dispatchEvent(new CustomEvent("serverman:operation-change", { detail: { busy: busyPublished, name } }));
}

// Mark a control as mutating; a dialog confirm button also gets the visible wait line. Returns the control.
function markBusyControl(control, dialog = false) {
  if (!control) return control;
  control.dataset.busyLock = dialog ? "dialog" : "control";
  // A control that is not in the document yet is locked now and gets its dialog line once it is attached.
  if (busyNow()) lockBusyControl(control); else unlockBusyControl(control);
  if (dialog) queueMicrotask(() => { if (control.isConnected) applyBusyState(); });
  return control;
}

// Remove the mark from a control whose activation no longer submits an operation.
function unmarkBusyControl(control) {
  if (!control || !("busyLock" in control.dataset)) return;
  unlockBusyControl(control);
  delete control.dataset.busyLock;
}

// Stop the activation of a busy-locked control before any page handler sees it, also when its page holds it
// aria-disabled for its own reason. Without the busy lock, such a control gets the activation, so the page can
// explain its reason (8.18).
function guardLockedActivation(event) {
  const locked = event.target instanceof Element ? event.target.closest("[data-busy-locked]") : null;
  if (!locked) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}

// Stop a form submission while the submit button of the form is busy-locked.
function guardLockedSubmit(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (!form.querySelector('[type="submit"][data-busy-locked]')) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}

// Follow the operation-change event and guard every activation in the capture phase.
document.addEventListener("serverman:operation-change", (event) => applyBusyState(event.detail));
document.addEventListener("click", guardLockedActivation, true);
document.addEventListener("submit", guardLockedSubmit, true);

// Publish the busy helper used by every page that owns a mutating control.
window.ServerManBusy = Object.freeze({
  mark: markBusyControl,
  unmark: unmarkBusyControl,
  refresh: () => applyBusyState(),
  publish: publishBusyState,
});
