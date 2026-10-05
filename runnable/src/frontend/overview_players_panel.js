// Content of the Overview names panel (D18): the head with the read time, the names in a grid that scrolls inside
// with a visible cue that more names follow, the states without a list, and the privacy foot. The panel state and
// the reads live in overview_players.js.
"use strict";

// Sentences of the panel states without a list.
const overviewPlayersTexts = Object.freeze({
  unavailable: "Player names are not available from this server. The count comes from its status answer.",
  failed: "Player names could not be read: the server did not answer. The next try is in 10 s.",
  empty: "No players are online.",
});
// Sorts names as people read them: case and accents aside, numbers by value (customer decision: by name).
const overviewPlayersCollator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

// Return a local clock time as hh:mm:ss.
function overviewPlayersClock(moment) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(moment.getHours())}:${pad(moment.getMinutes())}:${pad(moment.getSeconds())}`;
}

// Turn one read answer into the panel view; a failed read keeps the time of the last good read.
function overviewPlayersView(result, count, now, previous = null) {
  const value = result?.success ? result.value : null;
  if (!value || value.state !== "OK" || !Array.isArray(value.players)) {
    return { kind: "failed", updated: previous?.updated || "" };
  }
  const updated = overviewPlayersClock(now);
  // Keep a name and a whole number of seconds; a player without a name is still connecting and sorts last.
  const players = value.players.map((player) => ({
    name: typeof player?.name === "string" ? player.name.trim() : "",
    seconds: Number.isFinite(player?.duration_seconds) && player.duration_seconds >= 0 ? player.duration_seconds : null,
  })).sort((first, second) => Number(first.name === "") - Number(second.name === "")
    || overviewPlayersCollator.compare(first.name, second.name));
  if (players.length) return { kind: "list", players, updated };
  // An empty list while the status counts players means that the server does not report names.
  if (count?.kind === "count" && count.players > 0) return { kind: "unavailable", updated };
  return { kind: "empty", updated };
}

// Build the head: the title, the time of the last read, and the busy line of a first read or a refresh.
function overviewPlayersHead(view) {
  const head = overviewNode("div", "overview-players-head"); head.id = "overview-players-head";
  const title = overviewNode("h3", "overview-eyebrow", "Players online"); title.id = "overview-players-title";
  head.append(title);
  if (view?.updated) {
    head.append(overviewNode("p", "overview-meta overview-meta-subtle",
      `Updated ${view.updated} · refreshes every 10 s while open`));
  }
  const busy = view?.kind === "loading" ? "Loading player names…" : view?.refreshing ? "Updating…" : "";
  if (busy) head.append(overviewNode("p", "overview-meta mods-update-busy", busy));
  return head;
}

// Build the list of names with the time connected; an empty name reads "Connecting player".
function overviewPlayersList(players) {
  const list = overviewNode("ul", "overview-players-list"); list.id = "overview-players-list";
  list.setAttribute("aria-label",
    `${players.length} ${players.length === 1 ? "player" : "players"}, name and time connected`);
  players.forEach((player) => {
    const item = overviewNode("li");
    item.append(overviewNode("span", player.name ? "overview-player-name" : "overview-player-name is-unnamed",
      player.name || "Connecting player"));
    if (player.seconds !== null) {
      const time = overviewNode("span", "overview-player-time", overviewDuration(player.seconds * 1000));
      time.setAttribute("aria-label", `connected ${time.textContent}`);
      item.append(time);
    }
    list.append(item);
  });
  // Scrolling moves the cue and is remembered for the next refresh or redraw.
  list.addEventListener("scroll", () => {
    window.ServerManOverviewPlayers.rememberScroll(list.scrollTop);
    updateOverviewPlayersCue();
  });
  return list;
}

// Build the panel parts for a view: head, list or state sentence, and the foot.
function overviewPlayersParts(view) {
  const parts = [overviewPlayersHead(view)];
  if (view?.kind === "list") {
    const scroll = overviewNode("div", "overview-players-scroll");
    scroll.append(overviewPlayersList(view.players));
    // The cue is for the eye; a screen reader reaches every name in the list itself.
    const cue = overviewNode("p", "overview-meta overview-players-more"); cue.id = "overview-players-more";
    cue.setAttribute("aria-hidden", "true"); cue.hidden = true;
    parts.push(scroll, cue);
  } else if (Object.hasOwn(overviewPlayersTexts, view?.kind || "")) {
    const reason = view.kind === "empty" ? "overview-meta" : "overview-meta overview-reason";
    parts.push(overviewNode("p", reason, overviewPlayersTexts[view.kind]));
  }
  parts.push(overviewNode("p", "overview-meta overview-meta-subtle",
    "Names are shown in this window only. DayZ-ServerMan does not log or save them."));
  return parts;
}

// Show how many names lie below the visible part of the list, and fade its lower edge while some do.
function updateOverviewPlayersCue() {
  const list = document.getElementById("overview-players-list");
  const cue = document.getElementById("overview-players-more");
  if (!list || !cue) return;
  const scrolls = list.scrollHeight > list.clientHeight + 1;
  const bottom = list.scrollTop + list.clientHeight;
  // A name counts as below when less than half of its row is visible.
  const below = scrolls
    ? [...list.children].filter((item) => item.offsetTop + item.offsetHeight / 2 > bottom).length : 0;
  const text = below ? `${below} more ${below === 1 ? "name" : "names"} below · scroll the list` : "End of the list";
  if (cue.textContent !== text) cue.textContent = text;
  // The line stays while the list scrolls, so reaching the end does not move the page.
  cue.hidden = !scrolls;
  list.parentElement.classList.toggle("has-more", below > 0);
}

// Finish a drawn list: full text of cut names, a tab stop only for a list that scrolls, position, and the cue.
function settleOverviewPlayersPanel(scrollTop) {
  const list = document.getElementById("overview-players-list");
  if (!list) return;
  list.querySelectorAll(".overview-player-name").forEach((name) => {
    if (name.scrollWidth > name.clientWidth) name.title = name.textContent; else name.removeAttribute("title");
  });
  if (list.scrollHeight > list.clientHeight + 1) list.tabIndex = 0; else list.removeAttribute("tabindex");
  list.scrollTop = scrollTop;
  updateOverviewPlayersCue();
}

// Replace only the head, so that a refresh keeps the list in place while it reads.
function redrawOverviewPlayersHead(view) {
  document.getElementById("overview-players-head")?.replaceWith(overviewPlayersHead(view));
}

// Publish the panel content for the player count module.
window.ServerManOverviewPlayersPanel = Object.freeze({
  view: overviewPlayersView,
  parts: overviewPlayersParts,
  settle: settleOverviewPlayersPanel,
  redrawHead: redrawOverviewPlayersHead,
  cue: updateOverviewPlayersCue,
});
