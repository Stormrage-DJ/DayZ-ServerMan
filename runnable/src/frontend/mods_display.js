// Presentation helpers for workshop item states and operation outcomes.
"use strict";

// Translate one inventory state code into a short display label.
function inventoryStatus(state) {
  return ({ CURRENT: "Current", INSTALLED: "Installed", UPDATE_AVAILABLE: "Update available",
    NOT_DOWNLOADED: "Not downloaded", LOCAL: "Local" })[state] || "Unavailable";
}

// Build the configured-mod table for the selected profile.
function renderModInventory() {
  const panel = modsNode("section", "panel mods-inventory-panel");
  const heading = modsNode("div", "panel-heading");
  const profile = selectedProfile();
  heading.append(modsNode("h2", "", "Configured mods"), modsNode("span", "mods-count",
    `${modsState.inventory.length} · ${profile?.display_name || "No server selected"}`));
  panel.append(heading);
  // Use an empty state when the profile has no configured mods.
  if (!modsState.inventory.length) {
    panel.append(modsNode("p", "empty-copy", "This server profile has no configured mods."));
    return panel;
  }
  const scroll = modsNode("div", "mods-table-scroll");
  const table = modsNode("table", "mods-table");
  // Build the header row for the mod table.
  const head = modsNode("thead");
  const headRow = modsNode("tr");
  ["#", "Mod", "Directory", "Scope", "Version", "Status"].forEach((label) =>
    headRow.append(modsNode("th", "", label)));
  head.append(headRow); table.append(head);
  const body = modsNode("tbody");
  // Describe each mod with its order, directory, scope, version, and status.
  modsState.inventory.forEach((row) => {
    const tr = modsNode("tr");
    const name = modsNode("td", "mods-name");
    name.append(modsNode("strong", "", row.name || row.directory));
    if (row.workshop_id) name.append(modsNode("small", "", `Workshop ${row.workshop_id}`));
    tr.append(modsNode("td", "mods-order", String(row.order)), name,
      modsNode("td", "mods-directory", row.directory),
      modsNode("td", "", row.launch_scope === "server" ? "Server" : "Client"),
      modsNode("td", "", row.version || "Not declared"),
      modsNode("td", `mods-status mods-status-${String(row.state).toLowerCase()}`,
        inventoryStatus(row.state)));
    body.append(tr);
  });
  table.append(body); scroll.append(table); panel.append(scroll);
  return panel;
}

// List the per-item outcomes of a finished workshop operation.
function renderModsItems(items) {
  const region = document.getElementById("mods-feedback");
  if (!region || !Array.isArray(items) || !items.length) return;
  const outcomes = { VERIFIED_CURRENT: "Already current", DOWNLOADED_VERIFIED: "Downloaded",
    UPDATED_VERIFIED: "Updated", UNKNOWN_FAILED: "Could not verify" };
  const list = modsNode("ol", "operation-items");
  // Append one line per reported workshop item.
  items.forEach((entry) => {
    const id = entry?.item?.workshop_id || "Unknown item";
    const outcome = outcomes[entry?.outcome] || entry?.outcome || "Failed";
    const error = entry?.error_code ? ` (${entry.error_code})` : "";
    list.append(modsNode("li", "", `Workshop ${id}: ${outcome}${error}`));
  });
  region.append(list);
}
