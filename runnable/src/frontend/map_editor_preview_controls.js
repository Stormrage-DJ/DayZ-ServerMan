// The choice controls of the preview dialogs: a radio and a checkbox, each with its label (UI-UX 2026-10-17 17:08:16,
// P7). The Apply and Import previews build their choices with them, so the dialog frame holds no choice builder.
"use strict";

// One radio with its label; stacked, never checked at the start (R17: an explicit choice)
function mapPreviewControlsRadio(name, value, label) {
  const dom = window.ServerManMapDom, row = dom.element("label", "", {class: "check-row"});
  const input = dom.element("input", "", {type: "radio", name, value});
  row.append(input, dom.element("span", label));
  return {row, input};
}

// One checkbox with its label, never checked at the start. describedBy: the ID of the text that the box
// confirms, or null.
function mapPreviewControlsCheckbox(label, describedBy = null) {
  const dom = window.ServerManMapDom, row = dom.element("label", "", {class: "check-row"});
  const input = dom.element("input", "", {type: "checkbox", ...(describedBy ? {"aria-describedby": describedBy} : {})});
  row.append(input, dom.element("span", label));
  return {row, input};
}

// The preview builders take their choice controls here
window.ServerManMapPreviewControls = Object.freeze({radio: mapPreviewControlsRadio, checkbox: mapPreviewControlsCheckbox});
