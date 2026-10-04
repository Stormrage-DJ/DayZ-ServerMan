// Tweaks confirmation dialog for the conversion of a legacy starter loadout.
"use strict";

// Ask the operator to confirm legacy starter conversion.
function starterConversionDialog(snapshot) {
  return new Promise((resolve) => {
    const returnFocus = document.activeElement;
    // Build a modal dialog that resolves with the operator's choice.
    const dialog = window.ServerManUi.element("section", "panel starter-conversion-dialog");
    dialog.setAttribute("role", "alertdialog"); dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "starter-conversion-title");
    const title = window.ServerManUi.element("h2", "", "Convert legacy starter loadout?");
    title.id = "starter-conversion-title";
    const range = snapshot.conversion;
    dialog.append(title,
      window.ServerManUi.element("p", "", `The recognized block spans lines ${range.start_line}-${range.end_line} and contains ${range.items.length} supported item(s).`),
      window.ServerManUi.element("p", "", "Conversion adds DayZ-ServerMan ownership comments around that block. It does not replace its statements or reorder its items."));
    // Offer cancel and the conversion action.
    const actions = window.ServerManUi.element("div", "action-row");
    const cancel = window.ServerManUi.element("button", "button", "Cancel");
    const convert = window.ServerManUi.element("button", "button button-primary", "Convert legacy loadout");
    const inerted = [...document.body.children]
      .filter((item) => item !== dialog && !item.hasAttribute("data-announcer"))
      .map((item) => ({ item, inert: item.inert }));
    // The convert button submits an operation: lock it when one starts while the dialog is open.
    window.ServerManBusy?.mark(convert, true);
    // Restore the page and resolve with the chosen answer on close.
    const finish = (accepted) => {
      inerted.forEach(({ item, inert }) => { item.inert = inert; });
      dialog.remove(); if (returnFocus?.isConnected) returnFocus.focus(); resolve(accepted);
    };
    cancel.addEventListener("click", () => finish(false));
    convert.addEventListener("click", () => finish(true));
    // Keep focus inside the dialog and close on Escape.
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); finish(false); }
      if (event.key !== "Tab") return;
      if (event.shiftKey && document.activeElement === cancel) { event.preventDefault(); convert.focus(); }
      else if (!event.shiftKey && document.activeElement === convert) { event.preventDefault(); cancel.focus(); }
    });
    actions.append(cancel, convert); dialog.append(actions); document.body.append(dialog);
    inerted.forEach(({ item }) => { item.inert = true; }); cancel.focus();
  });
}
