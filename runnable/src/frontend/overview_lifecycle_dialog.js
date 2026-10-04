// Owns the accessible confirmation dialog for Overview lifecycle actions.
"use strict";

// Ask for confirmation of a lifecycle action and resolve to the choice.
function confirmOverviewLifecycle(action, backupAfterStop) {
  const labels = {
    start: ["Start DayZ server?", "Start the selected profile now.", "Start server"],
    stop: ["Save and stop DayZ?", "Request a graceful save and wait for DayZ to close.", "Save & Stop"],
    restart: ["Save and restart DayZ?", "Save and stop the managed process, then start the selected profile.", "Save & Restart"],
  };
  return new Promise((resolve) => {
    const returnFocus = document.activeElement;
    // Describe the action, its consequence, and the confirm label.
    const [title, baseMessage, confirmText] = labels[action];
    const message = backupAfterStop && action !== "start"
      ? `${baseMessage} A verified backup will be created after DayZ stops.` : baseMessage;
    // Build the dialog and its focus-trapping controls.
    const dialog = window.ServerManUi.element("section", "panel lifecycle-confirmation");
    dialog.id = "lifecycle-confirmation";
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "lifecycle-confirmation-title");
    const heading = window.ServerManUi.element("h2", "", title);
    heading.id = "lifecycle-confirmation-title";
    const actions = window.ServerManUi.element("div", "action-row");
    const cancel = window.ServerManUi.element("button", "button", "Cancel");
    const confirm = window.ServerManUi.element("button", "button button-primary", confirmText);
    // Make the rest of the page inert while the dialog is open.
    const inerted = [...document.body.children]
      .filter((element) => element !== dialog && !element.hasAttribute("data-announcer"))
      .map((element) => ({ element, inert: element.inert }));
    // Restore the page and resolve the promise when the dialog closes.
    const finish = (accepted) => {
      inerted.forEach(({ element, inert }) => { element.inert = inert; });
      dialog.remove();
      if (returnFocus?.isConnected) returnFocus.focus();
      resolve(accepted);
    };
    cancel.type = "button"; confirm.type = "button";
    // The confirm button submits an operation: lock it when one starts while the dialog is open.
    window.ServerManBusy?.mark(confirm, true);
    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); finish(false); }
      if (event.key !== "Tab") return;
      if (event.shiftKey && document.activeElement === cancel) {
        event.preventDefault(); confirm.focus();
      } else if (!event.shiftKey && document.activeElement === confirm) {
        event.preventDefault(); cancel.focus();
      }
    });
    actions.append(cancel, confirm);
    dialog.append(
      heading,
      window.ServerManUi.element("p", "", message),
      actions,
    );
    document.body.append(dialog);
    inerted.forEach(({ element }) => { element.inert = true; });
    cancel.focus();
  });
}

// Publish the confirmation surface used by Overview controls.
window.ServerManLifecycleDialog = Object.freeze({ confirm: confirmOverviewLifecycle });
