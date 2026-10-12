// Build editor elements and accessible modal dialogs without shell dependencies.
"use strict";

// Create text safely; callers cannot inject markup through labels or backend values.
function mapElement(tag, text = "", attributes = {}) {
  const element = document.createElement(tag);
  element.textContent = text;
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  return element;
}

// Trap keyboard focus in one modal and restore the previous focus on completion.
function mapDialog(title, message, choices) {
  const previous = document.activeElement;
  const dialog = mapElement("dialog", "", {"aria-labelledby": "map-dialog-title"});
  dialog.append(mapElement("h2", title, {id: "map-dialog-title"}), mapElement("p", message));
  return new Promise((resolve) => {
    // Resolve exactly once, remove the modal, and return focus to its caller.
    const finish = (value) => {
      dialog.close(); dialog.remove(); previous?.focus(); resolve(value);
    };
    const buttons = choices.map((choice) => {
      const button = mapElement("button", choice.label, {type: "button"});
      if (choice.danger) button.className = "button-danger";
      button.addEventListener("click", () => finish(choice.value));
      dialog.append(button);
      return button;
    });
    // Escape chooses cancellation; Tab wraps within the dialog's choices.
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault(); finish("cancel");
    });
    dialog.addEventListener("keydown", (event) => {
      if (event.key !== "Tab" || !buttons.length) return;
      const index = buttons.indexOf(document.activeElement);
      if ((event.shiftKey && index <= 0) || (!event.shiftKey && index === buttons.length - 1)) {
        event.preventDefault(); buttons[event.shiftKey ? buttons.length - 1 : 0].focus();
      }
    });
    document.body.append(dialog); dialog.showModal();
    (buttons.find((button) => button.textContent === "Keep editing") || buttons[0])?.focus();
  });
}

// Editor DOM utilities have a separate global from the operator shell.
window.ServerManMapDom = Object.freeze({element: mapElement, dialog: mapDialog});
