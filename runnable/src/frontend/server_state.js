// Shared server state: one status read for every section, a change event, and the sidebar state line.
"use strict";

// Sentence that every state tooltip ends with: the state belongs to the installation, not to a profile.
const SERVER_STATE_SCOPE = "One DayZ server runs in this installation, whichever profile is selected.";
// Status fields whose change is announced to the listening pages.
const serverStateFields = Object.freeze([
  "state", "readiness", "process_id", "query_port", "diagnostic_code", "started_at", "profile_id",
]);
// Last status read from the host, whether the last read confirmed it, the read in flight, and the label
// that was spoken last.
const serverStateStore = { status: null, confirmed: false, reading: null, label: null };

// Report whether two status values differ in a field that the shell shows.
function serverStateChanged(before, after) {
  if (!before || !after) return before !== after;
  return serverStateFields.some((field) => before[field] !== after[field]);
}

// Return the profile that the running server was started with when it is not the selected one, else null.
function otherRunningProfile() {
  const status = serverStateStore.status;
  if (!status || !status.profile_id || status.state === "STOPPED") return null;
  if (status.profile_id === window.ServerManProfileContext.selectedId()) return null;
  const known = window.ServerManProfileContext.profiles()
    .find((profile) => profile.profile_id === status.profile_id);
  // A profile that the list no longer holds is named by its identifier.
  return Object.freeze({ profile_id: status.profile_id,
    display_name: known ? known.display_name : status.profile_id });
}

// Set a text only when it differs, so assistive technology meets real changes only.
function setServerStateText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

// Draw the state line under the sidebar selector and the line that names another running profile.
function renderSidebarServerState() {
  const line = document.getElementById("sidebar-server-state");
  if (!line) return;
  const status = serverStateStore.status;
  // Before the first read the line says that the state is being read.
  const [label, tone, detail] = status
    ? window.ServerManOverviewReadiness.presentation(status)
    : ["Checking…", "status-neutral", "The server state is being read."];
  setServerStateText(line, label);
  const className = `status-label sidebar-server-state ${tone}`;
  if (line.className !== className) line.className = className;
  line.title = `${detail} ${SERVER_STATE_SCOPE}`;
  // Name the running profile only while another one is selected.
  const running = document.getElementById("sidebar-running");
  const other = otherRunningProfile();
  setServerStateText(running, other ? `Running: ${other.display_name}` : "");
  running.hidden = !other;
}

// Speak a changed state label once; the first read and a change during an operation stay silent.
function announceServerState(first) {
  const label = window.ServerManOverviewReadiness.presentation(serverStateStore.status)[0];
  const changed = serverStateStore.label !== null && serverStateStore.label !== label;
  serverStateStore.label = label;
  if (first || !changed || window.ServerManOperationBar?.isBusy()) return;
  speakOperation(`Server state: ${label}.`);
}

// Tell the listening pages the state and whether the last read confirmed it.
function publishServerStatus() {
  document.dispatchEvent(new CustomEvent("serverman:server-status", {
    detail: { status: serverStateStore.status, confirmed: serverStateStore.confirmed },
  }));
}

// Take one status value; redraw and tell the listening pages only when a shown field changed.
function adoptServerStatus(value) {
  const first = serverStateStore.status === null;
  const reconfirmed = !serverStateStore.confirmed;
  serverStateStore.confirmed = true;
  if (!serverStateChanged(serverStateStore.status, value)) {
    // A new player count is kept for the Overview, which writes it in place on the poll tick; it redraws
    // nothing and tells no page, so a player who joins does not move the focus (D18).
    if (serverStateStore.status && (serverStateStore.status.players !== value.players
        || serverStateStore.status.max_players !== value.max_players)) {
      serverStateStore.status = Object.freeze({ ...value });
    }
    // The state is the same, but a page that saw the failed read must learn that it is confirmed again.
    if (reconfirmed) publishServerStatus();
    return false;
  }
  serverStateStore.status = Object.freeze({ ...value });
  renderSidebarServerState();
  announceServerState(first);
  publishServerStatus();
  return true;
}

// Read the status once and return the host answer; a failed read keeps the last state and sets a warning.
async function readServerStatus() {
  let result = null;
  try { result = await window.pywebview.api.get_server_status(); } catch (_error) { result = null; }
  if (!result || !result.success) {
    window.ServerManUi.setHostStatus("Server status could not be refreshed", "is-warning", "lifecycle");
    // The sidebar keeps its last line; a page that must not act on an old state is told once.
    if (serverStateStore.confirmed) { serverStateStore.confirmed = false; publishServerStatus(); }
    return result || { success: false, error: null };
  }
  window.ServerManUi.clearHostStatus("lifecycle");
  adoptServerStatus(result.value);
  return result;
}

// Refresh on the shell poll tick, in every section; overlapping ticks share one read.
function refreshServerStatus() {
  if (!serverStateStore.reading) {
    serverStateStore.reading = readServerStatus().finally(() => { serverStateStore.reading = null; });
  }
  return serverStateStore.reading;
}

// The running line depends on the selection, so a profile change redraws the sidebar block.
document.addEventListener("serverman:profile-change", renderSidebarServerState);

// Publish the shared server state for the shell loop, the sidebar, and the pages.
window.ServerManServerState = Object.freeze({
  refresh: refreshServerStatus,
  // A page that needs a fresh answer after its own operation reads at once.
  read: readServerStatus,
  current: () => serverStateStore.status,
  // False after a failed read, until the next read succeeds.
  confirmed: () => serverStateStore.confirmed,
  otherRunningProfile,
  // Reason shown on a control that acts on the running server while another profile is selected.
  lockReason: () => {
    const other = otherRunningProfile();
    return other ? `${other.display_name} is running. Select it to stop or restart it.` : "";
  },
});
