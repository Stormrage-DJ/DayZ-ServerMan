// Supply first-party editor symbols without changing command names or action owners.
"use strict";

// Paths share a 24-unit coordinate box and remain decorative beside visible labels.
const mapIconPaths = Object.freeze({
  select: "M5 3v17l5-5 4 7 3-2-4-7 7-1Z",
  circle: "M20 12a8 8 0 1 1-8-8 8 8 0 0 1 8 8ZM18 3v5h5",
  polygon: "M5 5 19 4 21 19 3 20ZM3 3h4v4H3ZM17 2h4v4h-4ZM19 17h4v4h-4ZM1 18h4v4H1Z",
  encounter: "M10 3h4v4h-4ZM3 10h4v4H3ZM17 10h4v4h-4ZM10 17h4v4h-4ZM10 10h4v4h-4Z",
  inspect: "M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM15 15l7 7",
  undo: "m8 4-6 6 6 6M2 10h12a7 7 0 0 1 7 7v3",
  redo: "m16 4 6 6-6 6M22 10H10a7 7 0 0 0-7 7v3",
  fit: "M3 9V3h6M15 3h6v6M21 15v6h-6M9 21H3v-6",
  fullscreen: "M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5M3 3l6 6M21 3l-6 6M21 21l-6-6M3 21l6-6",
  finish: "m4 12 5 5L21 5",
  discard: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
  visible: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  hidden: "m3 3 18 18M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12ZM9 9l6 6",
  locked: "M6 10h12v11H6ZM8 10V6a4 4 0 0 1 8 0v4M12 14v3",
  unlocked: "M6 10h12v11H6ZM8 10V6a4 4 0 0 1 8 0M12 14v3",
  coordinates: "M12 2v4M12 18v4M2 12h4M18 12h4M18 12a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z"
});

// Build a symbol with no focus target or contribution to the accessible command name.
function mapIcon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({viewBox: "0 0 24 24", class: "map-icon", fill: "none",
    stroke: "currentColor", "stroke-width": "1.75", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true", focusable: "false"})) svg.setAttribute(key, value);
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", mapIconPaths[name] || mapIconPaths.polygon); svg.append(path);
  return svg;
}

// Preserve label text and listeners while decorating a command, including labels refreshed later.
function mapIconButton(button, name) {
  if (!button || button.querySelector(":scope > .map-icon")) return;
  const label = window.ServerManMapDom.element("span", button.textContent, {class: "map-command-label"});
  button.replaceChildren(mapIcon(name), label);
  // Keep the full screen shortcut at the menu edge without changing its full label.
  if (name === "fullscreen" && label.textContent.endsWith(" (F11)")) {
    label.textContent = label.textContent.slice(0, -6);
    button.append(window.ServerManMapDom.element("span", " (F11)", {class: "map-command-shortcut"}));
  }
}

// Refresh decoration only after existing owners have finished replacing their command labels.
function mapIconToolbar() {
  const commands = {"map-tool-select": "select", "map-tool-circle": "circle", "map-tool-polygon": "polygon",
    "map-tool-encounter": "encounter", "map-tool-inspect": "inspect", "map-undo": "undo", "map-redo": "redo",
    "map-fit": "fit", "map-zoom-selection": "inspect", "map-full-screen": "fullscreen",
    "map-outline-finish": "finish", "map-outline-discard": "discard"};
  for (const [id, name] of Object.entries(commands)) mapIconButton(document.getElementById(id), name);
}

window.ServerManMapIcons = Object.freeze({icon: mapIcon, button: mapIconButton, toolbar: mapIconToolbar});
