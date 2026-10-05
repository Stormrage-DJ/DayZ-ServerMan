// Overview player count (D18): "Players 12 / 60" after the server state while the server runs, and the names
// panel that it opens. The count comes from the status that the shell polls; the names are read only while the
// panel is open, every 10 s, on a visible Overview. Names stay in this window: never in a title of the toggle,
// the application status, an announcement, or browser storage.
"use strict";

// Refresh interval of the open names panel in milliseconds (customer decision, D18).
const OVERVIEW_PLAYERS_REFRESH_MS = 10000;
// Panel state: open or closed, the pending refresh, the generation that a read must still match, the read in
// flight, the panel view, the count form that the strip shows, and the list scroll position.
const overviewPlayersState = {
  open: false, timer: null, generation: 0, reading: null, view: null, kind: "none", scrollTop: 0,
};

// Decide what the strip shows: nothing, "Players not known", or the count with its slots.
function overviewPlayersCount(status) {
  const valid = (value) => Number.isInteger(value) && value >= 0;
  // A server outside the manager has a count only when it answered as the selected profile (D18 extension).
  if (status?.state === "RUNNING_EXTERNAL") {
    return valid(status.players) && valid(status.max_players)
      ? { kind: "count", players: status.players, max: status.max_players } : { kind: "unknown" };
  }
  if (status?.state !== "RUNNING_MANAGED" || status.readiness === "STARTING") return { kind: "none" };
  if (status.readiness === "UNRESPONSIVE" || !valid(status.players) || !valid(status.max_players)) {
    return { kind: "unknown" };
  }
  return { kind: "count", players: status.players, max: status.max_players };
}

// Write the visible count, its spoken label and the "Full" mark, each only when it changed.
function writeOverviewPlayersCount(count, toggle = document.getElementById("overview-players-toggle"),
  full = document.getElementById("overview-players-full")) {
  if (!toggle || !full) return;
  const text = `Players ${count.players} / ${count.max}`;
  const label = `Players ${count.players} of ${count.max} online`;
  if (toggle.textContent !== text) toggle.textContent = text;
  if (toggle.getAttribute("aria-label") !== label) toggle.setAttribute("aria-label", label);
  // A full server is no problem, so the mark is neutral.
  const isFull = count.max > 0 && count.players >= count.max;
  if (full.hidden === isFull) full.hidden = !isFull;
}

// Build the strip parts for a status: the nodes after the state, and the closed or open names panel.
function overviewPlayersStrip(status) {
  const count = overviewPlayersCount(status);
  overviewPlayersState.kind = count.kind;
  // Without a count there is nothing to open, so an open panel closes and forgets its names.
  if (count.kind !== "count") closeOverviewPlayers();
  if (count.kind === "none") return { row: [], panel: null };
  if (count.kind === "unknown") {
    return { row: [overviewNode("p", "overview-players-unknown", "Players not known")], panel: null };
  }
  const toggle = overviewNode("button", "link-button overview-players-toggle");
  toggle.type = "button"; toggle.id = "overview-players-toggle";
  toggle.setAttribute("aria-controls", "overview-players");
  toggle.setAttribute("aria-expanded", String(overviewPlayersState.open));
  toggle.addEventListener("click", () => setOverviewPlayersOpen(!overviewPlayersState.open));
  const full = overviewNode("span", "overview-players-full", "Full"); full.id = "overview-players-full";
  // The page region is a polite live region; the count and the names are never announced, so they opt out.
  [toggle, full].forEach((node) => node.setAttribute("aria-live", "off"));
  writeOverviewPlayersCount(count, toggle, full);
  // The panel is the last part of the strip; a redraw keeps it open with the names it had.
  const panel = overviewNode("div", "overview-players"); panel.id = "overview-players";
  panel.setAttribute("role", "region"); panel.setAttribute("aria-labelledby", "overview-players-title");
  panel.setAttribute("aria-live", "off");
  panel.hidden = !overviewPlayersState.open;
  panel.addEventListener("keydown", closeOverviewPlayersOnEscape);
  if (overviewPlayersState.open) panel.append(...window.ServerManOverviewPlayersPanel.parts(overviewPlayersState.view));
  return { row: [toggle, full], panel };
}

// Finish an open panel after the strip is on the page: the list layout needs measuring.
function settleOverviewPlayers() {
  if (overviewPlayersState.open) window.ServerManOverviewPlayersPanel.settle(overviewPlayersState.scrollTop);
}

// Open or close the panel; focus stays on the toggle.
function setOverviewPlayersOpen(open) {
  const panel = document.getElementById("overview-players");
  if (!open || !panel) { closeOverviewPlayers(); return; }
  overviewPlayersState.open = true;
  overviewPlayersState.generation += 1;
  overviewPlayersState.view = { kind: "loading" };
  overviewPlayersState.scrollTop = 0;
  panel.hidden = false;
  document.getElementById("overview-players-toggle")?.setAttribute("aria-expanded", "true");
  drawOverviewPlayers();
  void readOverviewPlayers();
}

// Close the panel, stop its reads, and drop the names that it showed.
function closeOverviewPlayers() {
  window.clearTimeout(overviewPlayersState.timer);
  Object.assign(overviewPlayersState, { open: false, timer: null, view: null, reading: null, scrollTop: 0 });
  overviewPlayersState.generation += 1;
  const panel = document.getElementById("overview-players");
  if (panel) { panel.hidden = true; panel.replaceChildren(); }
  document.getElementById("overview-players-toggle")?.setAttribute("aria-expanded", "false");
}

// Escape inside the panel closes it and gives the focus back to the toggle.
function closeOverviewPlayersOnEscape(event) {
  if (event.key !== "Escape") return;
  event.preventDefault();
  closeOverviewPlayers();
  document.getElementById("overview-players-toggle")?.focus();
}

// Replace the panel content from the view; keep the list focus and scroll position of a refresh.
function drawOverviewPlayers() {
  const panel = document.getElementById("overview-players");
  if (!panel || !overviewPlayersState.open) return;
  const listFocused = document.activeElement?.id === "overview-players-list";
  panel.replaceChildren(...window.ServerManOverviewPlayersPanel.parts(overviewPlayersState.view));
  window.ServerManOverviewPlayersPanel.settle(overviewPlayersState.scrollTop);
  if (listFocused) document.getElementById("overview-players-list")?.focus({ preventScroll: true });
}

// Report whether the names may be read now: an open panel on the visible Overview of a running server.
function overviewPlayersMayRead() {
  return overviewPlayersState.open && overviewPlayersState.kind === "count" && !document.hidden
    && shellState.section === "overview" && Boolean(document.getElementById("overview-players"));
}

// Read the names once, then plan the next read; a read of an earlier opening writes nothing.
async function readOverviewPlayers() {
  const generation = overviewPlayersState.generation;
  if (!overviewPlayersMayRead() || overviewPlayersState.reading === generation) return;
  overviewPlayersState.reading = generation;
  // A refresh keeps the list and shows "Updating…" beside the head text.
  if (overviewPlayersState.view?.kind !== "loading") {
    overviewPlayersState.view = { ...overviewPlayersState.view, refreshing: true };
    window.ServerManOverviewPlayersPanel.redrawHead(overviewPlayersState.view);
  }
  let result = null;
  try { result = await window.pywebview.api.get_online_players(); } catch (_error) { result = null; }
  if (generation !== overviewPlayersState.generation || !overviewPlayersState.open) return;
  overviewPlayersState.reading = null;
  const count = overviewPlayersCount(window.ServerManServerState.current());
  overviewPlayersState.view = window.ServerManOverviewPlayersPanel.view(result, count, new Date(),
    overviewPlayersState.view);
  drawOverviewPlayers();
  planOverviewPlayersRead();
}

// Plan the next read in 10 s.
function planOverviewPlayersRead() {
  window.clearTimeout(overviewPlayersState.timer);
  overviewPlayersState.timer = window.setTimeout(overviewPlayersTimerFired, OVERVIEW_PLAYERS_REFRESH_MS);
}

// The planned read: a page that is not Overview closes the panel; a hidden window waits until it is visible.
function overviewPlayersTimerFired() {
  overviewPlayersState.timer = null;
  if (shellState.section !== "overview") { closeOverviewPlayers(); return; }
  if (overviewPlayersMayRead()) void readOverviewPlayers();
}

// On each shell poll tick: close after leaving Overview, else write a changed count in place, without a redraw.
function refreshOverviewPlayers() {
  if (shellState.section !== "overview") {
    if (overviewPlayersState.open) closeOverviewPlayers();
    return;
  }
  const status = window.ServerManServerState.current();
  if (!status || !document.getElementById("overview-server")) return;
  const count = overviewPlayersCount(status);
  // A change between count, "not known" and nothing changes the parts of the strip, so it is redrawn.
  if (count.kind !== overviewPlayersState.kind) {
    overviewState.status = status;
    renderOverviewTop();
    return;
  }
  if (count.kind === "count") writeOverviewPlayersCount(count);
}

// A window that becomes visible again reads at once when the panel is open and no read is planned.
function resumeOverviewPlayers() {
  if (overviewPlayersState.timer === null && overviewPlayersMayRead()) void readOverviewPlayers();
}

document.addEventListener("serverman:poll-tick", refreshOverviewPlayers);
document.addEventListener("visibilitychange", resumeOverviewPlayers);

// Publish the player count of the server strip.
window.ServerManOverviewPlayers = Object.freeze({
  strip: overviewPlayersStrip,
  settle: settleOverviewPlayers,
  // A fresh Overview starts with the panel closed.
  reset: closeOverviewPlayers,
  setOpen: setOverviewPlayersOpen,
  // Remember the list position so that a redraw or a refresh keeps it.
  rememberScroll: (top) => { overviewPlayersState.scrollTop = top; },
  count: overviewPlayersCount,
});
