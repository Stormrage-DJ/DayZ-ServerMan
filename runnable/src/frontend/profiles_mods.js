// Mod row editor for the profile form.
"use strict";

// Read one mod row into its stored entry form.
function modValue(row) {
  const kind = row.querySelector("[data-mod-source]").value;
  const source = { kind };
  // Read the workshop identifier only for workshop-managed entries.
  if (kind === "workshop") source.workshop_id = row.querySelector("[data-mod-id]").value;
  return { directory: row.querySelector("[data-mod-directory]").value,
    launch_scope: row.querySelector("[data-mod-scope]").value, source };
}

// Match the identifier field to the selected source kind.
function updateModSource(row) {
  const input = row.querySelector("[data-mod-id]");
  const workshop = row.querySelector("[data-mod-source]").value === "workshop";
  // Keep the identifier editable only for workshop entries.
  input.disabled = !workshop; input.required = workshop;
  if (!workshop) input.value = "";
}

// Renumber rows and clamp the move buttons at the ends.
function refreshModRows(container) {
  const rows = [...container.querySelectorAll("[data-mod-row]")];
  rows.forEach((row, index) => {
    // Number rows in order and disable moves that would leave the list.
    row.querySelector("[data-mod-order]").textContent = String(index + 1);
    row.querySelector('[data-mod-move="up"]').disabled = index === 0;
    row.querySelector('[data-mod-move="down"]').disabled = index === rows.length - 1;
  });
}

// Move one mod row up or down and keep the numbering current.
function moveMod(row, direction) {
  // Stop when the row has no neighbour in that direction.
  const sibling = direction < 0 ? row.previousElementSibling : row.nextElementSibling;
  if (!sibling || !sibling.matches("[data-mod-row]")) return;
  if (direction < 0) row.parentElement.insertBefore(row, sibling);
  else row.parentElement.insertBefore(sibling, row);
  refreshModRows(row.parentElement);
  setProfileDirty(); row.querySelector("[data-mod-directory]").focus();
}

// Wrap one control in a labeled table cell.
function profileModCell(labelText, control) {
  const cell = profileNode("label", "profile-mod-cell"); cell.setAttribute("role", "cell");
  cell.append(profileNode("span", "sr-only", labelText), control); return cell;
}

// Build an icon button with an accessible label and glyph.
function profileModAction(label, path, className = "") {
  const button = profileNode("button", `button profile-mod-action ${className}`.trim());
  button.type = "button"; button.title = label; button.setAttribute("aria-label", label);
  // Draw the action glyph from the provided path data.
  const svg = document.createElementNS("http:" + "//www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
  const shape = document.createElementNS("http:" + "//www.w3.org/2000/svg", "path");
  shape.setAttribute("d", path); svg.append(shape); button.append(svg); return button;
}

// Build one editable mod row, defaulting to a blank entry.
function renderMod(mod = null) {
  const row = profileNode("div", "profile-mod"); row.dataset.modRow = "true";
  row.setAttribute("role", "row");
  const order = profileNode("span", "profile-mod-order"); order.dataset.modOrder = "true";
  order.setAttribute("role", "cell"); order.setAttribute("aria-label", "Mod order");
  // Require a DayZ-relative directory for the entry.
  const directory = document.createElement("input"); directory.type = "text";
  directory.value = mod?.directory || ""; directory.required = true;
  directory.dataset.modDirectory = "true";
  // Offer client and server launch scopes.
  const scope = document.createElement("select"); scope.dataset.modScope = "true";
  [["client", "Client (-mod)"], ["server", "Server (-serverMod)"]].forEach(([value, text]) => {
    const option = profileNode("option", "", text); option.value = value;
    option.selected = value === (mod?.launch_scope || "client"); scope.append(option);
  });
  // Offer workshop-managed and local ownership.
  const source = document.createElement("select"); source.dataset.modSource = "true";
  [["workshop", "Workshop-managed"], ["external", "Local / unmanaged"]].forEach(([value, text]) => {
    const option = profileNode("option", "", text); option.value = value;
    option.selected = value === (mod?.source?.kind || "workshop"); source.append(option);
  });
  const id = document.createElement("input"); id.type = "text";
  id.value = mod?.source?.workshop_id || ""; id.dataset.modId = "true";
  // Provide reorder and remove actions for the row.
  const actions = profileNode("div", "profile-mod-actions"); actions.setAttribute("role", "cell");
  const up = profileModAction("Move up", "M12 19V5M5 12l7-7 7 7"); up.dataset.modMove = "up";
  up.addEventListener("click", () => moveMod(row, -1));
  const down = profileModAction("Move down", "M12 5v14M19 12l-7 7-7-7");
  down.dataset.modMove = "down"; down.addEventListener("click", () => moveMod(row, 1));
  const remove = profileModAction("Remove", "M4 7h16M9 7V4h6v3m-9 0 1 13h10l1-13M10 11v5m4-5v5", "button-danger");
  remove.addEventListener("click", () => {
    const container = row.parentElement; row.remove(); refreshModRows(container); setProfileDirty();
  });
  actions.append(up, down, remove);
  row.append(order, profileModCell("DayZ-relative directory", directory),
    profileModCell("Launch scope", scope), profileModCell("Source ownership", source),
    profileModCell("Workshop ID", id), actions);
  // Keep the identifier rule in step with the source choice.
  source.addEventListener("change", () => { updateModSource(row); setProfileDirty(); });
  updateModSource(row); return row;
}
