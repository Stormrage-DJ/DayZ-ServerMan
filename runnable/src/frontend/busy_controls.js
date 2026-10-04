// Busy rule for page controls: while an operation runs or waits, marked controls are locked with an explanation.
// Pages mark a control once; this helper follows the shell's operation-change event. The host stays authoritative.
"use strict";

// Identifier of the hidden element that holds the current reason text.
const BUSY_REASON_ID = "busy-reason";
// Name of the operation in the last operation-change event, for controls that are marked later.
let busyOperationName = "";

// Read the current reason text that the shell wrote for locked controls.
function busyReasonText() {
  return document.getElementById(BUSY_REASON_ID)?.textContent || "";
}

// Lock one marked control: state, description, and tooltip; a natively disabled control keeps its own tooltip.
function lockBusyControl(control) {
  if (control.getAttribute("aria-disabled") !== "true") {
    control.setAttribute("aria-disabled", "true");
    // Keep the control's own description and add the shared reason behind it.
    const described = control.getAttribute("aria-describedby");
    control.dataset.busyDescribedby = described ?? "";
    control.setAttribute("aria-describedby", described ? `${described} ${BUSY_REASON_ID}` : BUSY_REASON_ID);
    control.dataset.busyTitle = control.getAttribute("title") ?? "";
  }
  if (!control.disabled) control.setAttribute("title", busyReasonText());
}

// Unlock one marked control and give it back its own description and tooltip.
function unlockBusyControl(control) {
  if (control.getAttribute("aria-disabled") !== "true" || !("busyTitle" in control.dataset)) return;
  control.removeAttribute("aria-disabled");
  [["aria-describedby", "busyDescribedby"], ["title", "busyTitle"]].forEach(([attribute, key]) => {
    if (control.dataset[key]) control.setAttribute(attribute, control.dataset[key]);
    else control.removeAttribute(attribute);
    delete control.dataset[key];
  });
}

// Show or remove the visible wait line above the buttons of an open dialog.
function syncBusyDialogLine(control, busy, name) {
  const row = control.closest(".action-row") || control.parentElement;
  const existing = row?.previousElementSibling?.classList.contains("busy-wait-line")
    ? row.previousElementSibling : null;
  if (!busy) { existing?.remove(); return; }
  const text = name ? `Wait: \u201C${name}\u201D is in progress.` : "Wait: DayZ-ServerMan is closing.";
  if (existing) { if (existing.textContent !== text) existing.textContent = text; return; }
  const line = window.ServerManUi.element("p", "busy-wait-line", text);
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
  const busy = detail ? detail.busy === true : window.ServerManOperationBar?.isBusy() === true;
  if (detail) busyOperationName = detail.name || "";
  const name = busyOperationName;
  document.querySelectorAll("[data-busy-lock]").forEach((control) => applyBusyControl(control, busy, name));
}

// Mark a control as mutating; a dialog confirm button also gets the visible wait line. Returns the control.
function markBusyControl(control, dialog = false) {
  if (!control) return control;
  control.dataset.busyLock = dialog ? "dialog" : "control";
  // A control that is not in the document yet is locked now and gets its dialog line once it is attached.
  const busy = window.ServerManOperationBar?.isBusy() === true;
  if (busy) lockBusyControl(control); else unlockBusyControl(control);
  if (dialog) queueMicrotask(() => { if (control.isConnected) applyBusyState(); });
  return control;
}

// Remove the mark from a control whose activation no longer submits an operation.
function unmarkBusyControl(control) {
  if (!control || !("busyLock" in control.dataset)) return;
  unlockBusyControl(control);
  delete control.dataset.busyLock;
}

// Stop the activation of a locked control before any page handler sees it.
function guardLockedActivation(event) {
  const locked = event.target instanceof Element ? event.target.closest('[aria-disabled="true"]') : null;
  if (!locked) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}

// Stop a form submission while the submit button of the form is locked.
function guardLockedSubmit(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (!form.querySelector('[type="submit"][aria-disabled="true"]')) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}

// Follow the shell's operation-change event and guard every activation in the capture phase.
document.addEventListener("serverman:operation-change", (event) => applyBusyState(event.detail));
document.addEventListener("click", guardLockedActivation, true);
document.addEventListener("submit", guardLockedSubmit, true);

// Publish the busy helper used by every page that owns a mutating control.
window.ServerManBusy = Object.freeze({
  mark: markBusyControl,
  unmark: unmarkBusyControl,
  refresh: () => applyBusyState(),
});
