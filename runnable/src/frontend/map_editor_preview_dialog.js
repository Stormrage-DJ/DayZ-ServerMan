// The frame of the Apply, Restore and Adopt preview dialog (8.10, 10.16): sections, collapsed lists, focus, the choice gate and the
// stale state (UI-UX 2026-10-15 15:58:12, sections 3, 4 and 6). It submits nothing; it resolves a value or null.
"use strict";

// active: the open preview ({stale}); one preview is open at a time. ids: the counter of element IDs.
const mapPreviewDialogState = {active: null, ids: 0};
// A list with more rows than this is collapsed behind a toggle with its count
const mapPreviewDialogOpenRows = 5;

// A new element ID of this document
function mapPreviewDialogId(prefix) {
  mapPreviewDialogState.ids += 1;
  return `${prefix}-${mapPreviewDialogState.ids}`;
}

// A section with its heading; an empty section is not drawn (null). The toggle of a list without a label or a
// text before it is named by the heading (UI-UX 2026-10-15 17:59:02, change 5).
function mapPreviewDialogSection(heading, nodes) {
  const parts = nodes.flat().filter(Boolean);
  if (!parts.length) return null;
  const id = mapPreviewDialogId("map-preview-heading"), dom = window.ServerManMapDom;
  const section = dom.element("section", "", {"aria-labelledby": id});
  section.append(dom.element("h3", heading, {id}), ...parts);
  section.querySelectorAll(".link-button[aria-controls]:not([aria-labelledby])").forEach((toggle) =>
    toggle.setAttribute("aria-labelledby", `${id} ${toggle.id}`));
  return section;
}

// A note: "error" and "warning" add their border; a neutral note has the default border. Null text draws nothing.
function mapPreviewDialogNotice(text, kind = null) {
  if (typeof text !== "string" || !text) return null;
  return window.ServerManMapDom.element("p", text, {class: kind ? `notice notice-${kind}` : "notice"});
}

// A list with an optional label. Above 5 rows, or when collapsed is true, a toggle with the count opens it. The
// toggle is named by the label, else by the element of namedBy (the text before the list), then by its count.
function mapPreviewDialogList(label, rows, collapsed = false, namedBy = null) {
  const items = rows.filter((row) => row !== null && row !== undefined && row !== "");
  if (!items.length) return null;
  const dom = window.ServerManMapDom, box = dom.element("div", "", {class: "map-preview-list"});
  const list = dom.element("ul", "", {id: mapPreviewDialogId("map-preview-list")});
  list.append(...items.map((row) => row instanceof Node ? row : dom.element("li", String(row))));
  const labelId = label ? mapPreviewDialogId("map-preview-label") : namedBy;
  if (label) box.append(dom.element("p", label, {id: labelId}));
  if (collapsed || items.length > mapPreviewDialogOpenRows) {
    const toggleId = mapPreviewDialogId("map-preview-toggle");
    const toggle = dom.element("button", window.ServerManMapPreviewWords.entries(items.length), {type: "button",
      id: toggleId, class: "link-button", "aria-expanded": "false", "aria-controls": list.id,
      ...(labelId ? {"aria-labelledby": `${labelId} ${toggleId}`} : {})});
    toggle.addEventListener("click", () => {
      list.hidden = !list.hidden;
      toggle.setAttribute("aria-expanded", String(!list.hidden));
    });
    list.hidden = true;
    box.append(toggle);
  }
  box.append(list);
  return box;
}

// The elements that Tab reaches in the dialog, in DOM order; a radio group is one stop for the browser
function mapPreviewDialogStops(dialog) {
  return [...dialog.querySelectorAll('button, input, select, textarea, [tabindex="0"]')]
    .filter((element) => !element.disabled && !element.closest("[hidden]") && element.tabIndex >= 0);
}

// Open one preview. spec: {kind: "apply" | "restore" | "adopt", title, sections, refused, confirm (label), count
// (missing choices), first (the first control without a choice), value (the resolved value), opener}. A refused
// answer can also have `second`: the label of a button before Close that resolves {adopt: true}.
function mapPreviewDialogOpen(spec) {
  const dom = window.ServerManMapDom, words = window.ServerManMapPreviewWords;
  const opener = spec.opener ?? document.activeElement, headingId = mapPreviewDialogId("map-preview-title");
  const dialog = dom.element("dialog", "", {class: "map-preview", "aria-labelledby": headingId});
  const heading = dom.element("h2", spec.title, {id: headingId, tabindex: "-1"});
  // The body scrolls; the keyboard can scroll it also when it holds no control
  const body = dom.element("div", "", {class: "map-preview-body", tabindex: "0", role: "region",
    "aria-labelledby": headingId});
  body.append(...spec.sections.filter(Boolean));
  const footer = dom.element("div", "", {class: "map-preview-footer"});
  const remaining = spec.kind === "apply" && !spec.refused ?
    dom.element("p", "", {id: mapPreviewDialogId("map-preview-remaining")}) : null;
  const status = dom.element("p", "", {role: "status", class: "map-preview-status"});
  const actions = dom.element("div", "", {class: "action-row"});
  // The stop sentence shows only when its text is not empty (adoption needs no stopped server, ADOPT-19)
  footer.append(...(words.stop[spec.kind] ? [dom.element("p", words.stop[spec.kind])] : []),
    ...(remaining ? [remaining] : []), status, actions);
  dialog.append(heading, body, footer);
  return new Promise((resolve) => {
    const active = {stale: null, marked: false};
    // Resolve once, remove the modal and return the focus to the opener, or to the map when the opener is gone
    const finish = (value) => {
      if (mapPreviewDialogState.active === active) mapPreviewDialogState.active = null;
      document.removeEventListener("serverman:operation-change", gate);
      dialog.close(); dialog.remove();
      (opener?.isConnected ? opener : document.getElementById("map-viewport"))?.focus();
      resolve(value);
    };
    let confirm = null;
    if (spec.refused) {
      if (spec.second) actions.append(mapPreviewDialogButton(spec.second, "button", () => finish({adopt: true})));
      actions.append(mapPreviewDialogButton(words.close, "button", () => finish(null)));
    } else {
      confirm = mapPreviewDialogButton(spec.confirm, "button button-primary", () => {
        // A missing choice moves the focus to it; a confirm that is aria-disabled for another reason does nothing
        const first = spec.count() ? spec.first() : null;
        if (first) first.focus();
        else if (confirm.getAttribute("aria-disabled") !== "true") finish(spec.value());
      });
      if (remaining) confirm.setAttribute("aria-describedby", remaining.id);
      actions.append(mapPreviewDialogButton(words.cancel, "button", () => finish(null)), confirm);
      // The busy lock of the lane also locks the confirm button, with the wait line above the buttons (8.18)
      window.ServerManBusy?.mark(confirm, true);
    }
    // The choice gate: the remaining line and aria-disabled, never disabled, so the keyboard reaches the reason.
    // While the busy lock holds the button, the gate does not clear aria-disabled. The lock stops every click
    // (UI-UX section 4; Architect 2026-10-16 16:50:12 in evidence 8.19).
    function gate() {
      const count = spec.count();
      if (remaining) remaining.textContent = count ? words.remaining(count) : "";
      if (!confirm) return;
      if (count) confirm.setAttribute("aria-disabled", "true");
      else if (!("busyLocked" in confirm.dataset)) confirm.removeAttribute("aria-disabled");
    }
    body.addEventListener("change", gate);
    // After the busy helper, which listens first: an end of the lock gives the choice gate back its state
    document.addEventListener("serverman:operation-change", gate);
    // Stale: the choices lock, and Preview again takes the place of the confirm button (UI-UX section 6)
    active.stale = () => {
      if (active.marked) return;
      active.marked = true;
      status.textContent = words.stale[spec.kind];
      status.classList.add("map-preview-stale");
      if (remaining) remaining.textContent = "";
      const locked = body.contains(document.activeElement) && document.activeElement.matches("input");
      body.querySelectorAll("fieldset, input").forEach((element) => { element.disabled = true; });
      // A choice that had the focus is now disabled; the focus stays in its place, on the scroll region
      if (locked) body.focus();
      const again = mapPreviewDialogButton(words.again, "button button-primary", () => finish({again: true}));
      // A refused answer has no confirm button: Preview again follows Close, and the focus does not move (change 3)
      if (!confirm) { actions.append(again); return; }
      const focused = document.activeElement === confirm;
      confirm.replaceWith(again);
      window.ServerManBusy?.unmark(confirm);
      footer.querySelector(".busy-wait-line")?.remove();
      confirm = null;
      if (focused) again.focus();
    };
    // Escape is Cancel; Tab and Shift+Tab wrap over every element that Tab reaches
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(null); });
    dialog.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const stops = mapPreviewDialogStops(dialog), first = stops[0], last = stops.at(-1);
      const current = document.activeElement;
      if (!event.shiftKey && current === last) { event.preventDefault(); first.focus(); }
      else if (event.shiftKey && (current === first || current === heading)) { event.preventDefault(); last.focus(); }
    });
    mapPreviewDialogState.active = active;
    gate();
    document.body.append(dialog);
    dialog.showModal();
    heading.focus();
  });
}

// One action button of the footer
function mapPreviewDialogButton(label, className, action) {
  const button = window.ServerManMapDom.element("button", label, {type: "button", class: className});
  button.addEventListener("click", action);
  return button;
}

// Mark the open preview stale; 8.18 calls this when a map-affecting operation ends while the preview is open
function mapPreviewDialogStale() {
  mapPreviewDialogState.active?.stale?.();
}

// The Apply and Restore builders use these parts; the editor uses open() through them and stale() directly
window.ServerManMapPreviewDialog = Object.freeze({open: mapPreviewDialogOpen, stale: mapPreviewDialogStale,
  section: mapPreviewDialogSection, notice: mapPreviewDialogNotice, list: mapPreviewDialogList, id: mapPreviewDialogId,
  isOpen: () => mapPreviewDialogState.active !== null});
