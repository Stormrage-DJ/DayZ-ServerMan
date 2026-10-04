// Presentation helpers for workshop item states and operation outcomes.
"use strict";

// Signature of the rows and check state drawn last, used to skip identical redraws.
let modsRowsSignature = "";

// Describe one row's status cell: label, tone, and detail lines with optional exact values.
function inventoryStatus(row) {
  const updates = window.ServerManUpdateStatus;
  const status = updates.current();
  const line = (text, title = "") => ({ text, title });
  // An installed item without a verified Steam answer is being checked or could not be checked.
  if (row.state === "INSTALLED" && row.remote_check
      && !["OK", "NOT_APPLICABLE"].includes(row.remote_check)) {
    if (status?.checking && row.remote_check === "NEVER") {
      return { label: "Checking…", tone: "checking", lines: [] };
    }
    const reason = updates.reason(row.remote_check);
    const success = updates.formatTime(status?.mods?.last_success_at);
    return { label: "Could not check", tone: "unchecked", lines: [
      line(reason.charAt(0).toUpperCase() + reason.slice(1)),
      success ? line(`Last checked ${success.text}`, success.exact) : line("No successful check yet"),
    ] };
  }
  // An available update shows the installed and the Steam date when they are known.
  if (row.state === "UPDATE_AVAILABLE") {
    const dated = (prefix, value) => {
      const time = updates.formatTime(value);
      return time ? [line(`${prefix} ${time.text}`, time.exact)] : [];
    };
    return { label: "Update available", tone: "update_available",
      lines: [...dated("Installed", row.time_updated), ...dated("Steam", row.remote_time_updated)] };
  }
  // Downloaded content that the server folder does not provably hold yet.
  if (row.state === "PENDING_APPLY") {
    return { label: "Downloaded - not applied", tone: "pending_apply",
      lines: row.pending_reason === "TARGET_UNPROVEN"
        ? [line("Folder exists but was never verified. Run Verify files or Update.")] : [] };
  }
  const label = ({ CURRENT: "Current", INSTALLED: "Installed", NOT_DOWNLOADED: "Not downloaded",
    LOCAL: "Local" })[row.state] || "Unavailable";
  return { label, tone: String(row.state).toLowerCase(), lines: [] };
}

// Build the status cell with the state text first and the detail lines below it.
function renderModStatus(row) {
  const status = inventoryStatus(row);
  // A running or finished operation of this page may mark the row: its own label, or a line below the state.
  const mark = window.ServerManModsProgress.mark(row);
  const cell = modsNode("td", `mods-status mods-status-${mark.tone || status.tone}`);
  cell.append(modsNode("span", "mods-status-label", mark.label || status.label));
  // Add each detail with its exact value as a tooltip, then the lines of a file verification.
  [...(mark.label ? [] : status.lines), ...mark.lines, ...window.ServerManModsVerify.rowLines(row)].forEach((entry) => {
    const detail = modsNode("small", "mods-status-detail", entry.text);
    if (entry.tone) detail.classList.add(`mods-status-detail-${entry.tone}`);
    if (entry.title) detail.title = entry.title;
    cell.append(detail);
  });
  return cell;
}

// Build the mod table, or the empty text when the profile has no configured mods.
function renderModRows() {
  if (!modsState.inventory.length) {
    return modsNode("p", "empty-copy", "This server profile has no configured mods.");
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
    const tr = modsNode("tr"); tr.dataset.state = String(row.state);
    const name = modsNode("td", "mods-name");
    name.append(modsNode("strong", "", row.name || row.directory));
    if (row.workshop_id) name.append(modsNode("small", "", `Workshop ${row.workshop_id}`));
    tr.append(modsNode("td", "mods-order", String(row.order)), name,
      modsNode("td", "mods-directory", row.directory),
      modsNode("td", "", row.launch_scope === "server" ? "Server" : "Client"),
      modsNode("td", "", row.version || "Not declared"), renderModStatus(row));
    body.append(tr);
  });
  table.append(body); scroll.append(table);
  return scroll;
}

// Capture everything the rows show, so an unchanged refresh leaves the table alone.
function captureModRowsSignature() {
  const status = window.ServerManUpdateStatus.current();
  return JSON.stringify([modsState.inventory, status?.checking, status?.mods?.check_state,
    status?.mods?.error_code, status?.mods?.last_success_at,
    window.ServerManUpdateStatus.automaticChecks(), window.ServerManModsVerify.signature(),
    window.ServerManModsProgress.signature()]);
}

// Word the count beside the panel heading.
function modsCountText() {
  return `${modsState.inventory.length} · ${selectedProfile()?.display_name || "No server selected"}`;
}

// Build the configured-mod panel for the selected profile: heading, check header, and table.
function renderModInventory() {
  const panel = modsNode("section", "panel mods-inventory-panel");
  const heading = modsNode("div", "panel-heading");
  heading.append(modsNode("h2", "", "Configured mods"), modsNode("span", "mods-count", modsCountText()));
  panel.append(heading);
  // Show the update check state and the file verification only for a selected profile.
  // The header order is: Check now, Verify files, the start action, Update all.
  if (selectedProfile()) panel.append(window.ServerManModsActions.attach(window.ServerManModsVerify.attach(
    window.ServerManUpdateStatus.renderHeader(modsState.inventory))));
  const body = modsNode("div", "mods-inventory-body");
  body.append(renderModRows()); panel.append(body);
  modsRowsSignature = captureModRowsSignature();
  return panel;
}

// Redraw the inventory panel in place; the Steam form, feedback, focus, and scroll stay untouched.
function updateModInventory() {
  const panel = document.querySelector(".mods-inventory-panel");
  const body = panel?.querySelector(".mods-inventory-body");
  if (!body) return;
  // Refresh the count and the check header without replacing their elements.
  const count = panel.querySelector(".mods-count");
  if (count.textContent !== modsCountText()) count.textContent = modsCountText();
  window.ServerManUpdateStatus.refreshHeader(modsState.inventory);
  window.ServerManModsVerify.sync();
  // Replace the table only when a visible value changed, and keep its sideways scroll.
  const signature = captureModRowsSignature();
  if (signature === modsRowsSignature) return;
  const left = body.querySelector(".mods-table-scroll")?.scrollLeft || 0;
  body.replaceChildren(renderModRows());
  const scroll = body.querySelector(".mods-table-scroll");
  if (scroll) scroll.scrollLeft = left;
  modsRowsSignature = signature;
}

// List the per-item outcomes of a finished workshop operation.
function renderModsItems(items) {
  const region = document.getElementById("mods-feedback");
  if (!region || !Array.isArray(items) || !items.length) return;
  const list = modsNode("ol", "operation-items");
  // Append one line per reported workshop item, worded by the catalogue.
  items.forEach((entry) => {
    const id = entry?.item?.workshop_id || "Unknown item";
    list.append(modsNode("li", "", `Workshop ${id}: ${window.ServerManDiagnosticLabels.modOutcome(entry)}`));
  });
  region.append(list);
}
