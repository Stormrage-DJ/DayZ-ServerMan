"""Headless Edge checks of the D18 player count in the Overview server strip and of its names panel: count states,
in-place updates, open and close, keyboard, the 10 s refresh with fake timers, the panel states, the scroll cue,
and that names stay in the window."""
from __future__ import annotations

import unittest

try:
    from tests.overview_harness import OVERVIEW_HEAD
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from overview_harness import OVERVIEW_HEAD
    from ui_harness_support import EDGE, run_shell_harness


# Fake names read, fake 10 s timers, and a roster that arrives unsorted with one player still connecting
PLAYERS_HEAD = OVERVIEW_HEAD + r"""
const realSetTimeout = window.setTimeout.bind(window);
const realClearTimeout = window.clearTimeout.bind(window);
const timers = new Map(); let timerId = 1000000;
window.setTimeout = (callback, delay, ...rest) => {
  if (delay !== 10000) return realSetTimeout(callback, delay, ...rest);
  timerId += 1; timers.set(timerId, callback); return timerId;
};
window.clearTimeout = (id) => { if (timers.has(id)) timers.delete(id); else realClearTimeout(id); };
// Run every planned 10 s callback once and report how many there were.
const fire = async () => { const due = [...timers.values()]; timers.clear(); due.forEach((callback) => callback());
  await wait(40); return due.length; };
const reads = []; const answers = [];
window.pywebview.api.get_online_players = () => { reads.push(shellState.section);
  const next = answers.length ? answers.shift() : ok({state: "OK", players: []});
  if (next === "pending") return new Promise(() => {});
  return next instanceof Error ? Promise.reject(next) : Promise.resolve(next); };
const LONG = "Chernarus_Survivor_With_A_Really_Long_Name_That_Does_Not_Fit";
const roster = (count) => Array.from({length: count}, (_, index) => ({
  name: `Survivor_${String(index).padStart(2, "0")}`,
  duration_seconds: index * 600}));
const named = [{name: "zelenoScout", duration_seconds: 40}, {name: "", duration_seconds: 3},
  {name: "ash_Walker", duration_seconds: 11527}, {name: "Ärzte_Bob", duration_seconds: 3840},
  {name: LONG, duration_seconds: 183600}, {name: "Bravo", duration_seconds: null}];
const toggleButton = () => byId("overview-players-toggle");
const panel = () => byId("overview-players");
const names = () => [...panel().querySelectorAll(".overview-player-name")].map((node) => node.textContent);
const openPanel = async () => { toggleButton().click(); await wait(60); };
// One start time for every status of a test, so that a new count is the only change
const runningBase = running();
const running2 = (extra = {}) => ({...runningBase, ...extra});
host.status = running2({players: 12, max_players: 60});
"""

# Count states in the strip, in-place updates without a redraw, and no announcement
COUNT = PLAYERS_HEAD + r"""
await start(); await wait(80);
const countButton = toggleButton();
check(countButton.tagName === "BUTTON" && countButton.type === "button"
  && countButton.classList.contains("link-button"), "toggle kind");
same(countButton.textContent, "Players 12 / 60", "count text");
same(countButton.getAttribute("aria-label"), "Players 12 of 60 online", "spoken name");
same(countButton.getAttribute("aria-expanded"), "false", "closed");
same(countButton.getAttribute("aria-controls"), "overview-players", "controls");
same(countButton.previousElementSibling.classList.contains("overview-state"), true, "after the state");
same(getComputedStyle(countButton).fontVariantNumeric, "tabular-nums", "tabular numbers");
check(byId("overview-players-full").hidden && panel().hidden && !panel().children.length, "full mark or panel shown");
check(!countButton.hasAttribute("title") && !document.querySelector("[style]"), "title or style attribute");
// A joining player changes the text in place: same element, focus kept, no redraw, nothing announced.
countButton.focus();
const strip = byId("overview-server"); const announced = byId("operation-announcer").textContent;
host.status = running2({players: 13, max_players: 60}); await tick();
check(toggleButton() === countButton && byId("overview-server") === strip
  && document.activeElement === countButton, "redrawn");
same(countButton.textContent, "Players 13 / 60", "updated count");
same(countButton.getAttribute("aria-label"), "Players 13 of 60 online", "updated name");
same(byId("operation-announcer").textContent, announced, "announcement");
// The page region is a polite live region; the count opts out of it, so a change is never spoken.
same(countButton.closest("[aria-live]").getAttribute("aria-live"), "off", "count in a live region");
same(byId("overview-players-full").getAttribute("aria-live"), "off", "full mark in a live region");
// Full: a neutral mark beside the count; several digits stay on one row.
host.status = running2({players: 128, max_players: 128}); await tick();
check(!byId("overview-players-full").hidden && byId("overview-players-full").textContent === "Full", "full mark");
check(!byId("overview-players-full").className.includes("warning"), "full in warning colour");
check(Math.abs(countButton.getBoundingClientRect().top - byId("overview-players-full").getBoundingClientRect().top) < 6,
  "count and full mark on one row");
host.status = running2({players: 0, max_players: 60}); await tick();
check(countButton.textContent === "Players 0 / 60" && byId("overview-players-full").hidden, "zero players");
// No count while running: "Players not known", as plain text, in place of the button.
const known = (status) => { host.status = status; };
for (const [status, label] of [[running2({players: null, max_players: null}), "no count"],
    [running2({readiness: "UNRESPONSIVE", players: 3, max_players: 60}), "not responding"],
    [{...running2(), state: "RUNNING_EXTERNAL", profile_id: null, started_at: null}, "external"]]) {
  known(status); await tick();
  const unknown = region().querySelector(".overview-players-unknown");
  check(unknown && unknown.tagName === "P" && unknown.textContent === "Players not known" && !toggleButton(), label);
}
// Starting, stopping, stopped, unknown and several servers: nothing, and "Process details" stays.
for (const status of [running2({readiness: "STARTING", players: 0, max_players: 60}),
    {...running2(), state: "STOPPING"}, {...running2(), state: "STOPPED", readiness: null, profile_id: null},
    {...running2(), state: "UNKNOWN", readiness: null}, {...running2(), state: "AMBIGUOUS", readiness: null}]) {
  host.status = status; await tick();
  check(!toggleButton() && !region().querySelector(".overview-players-unknown") && !panel(),
    `${status.state} shows players`);
}
check(byId("overview-process-toggle"), "process details");
"""

# Open and close, keyboard, the list, privacy of the names, and the panel states
PANEL = PLAYERS_HEAD + r"""
await start(); await wait(80);
answers.push("pending");
toggleButton().focus(); await openPanel();
// First read: the panel opens with its title and "Loading player names…", focus stays on the toggle.
same(document.activeElement, toggleButton(), "focus after opening");
same(toggleButton().getAttribute("aria-expanded"), "true", "expanded");
check(!panel().hidden && panel().getAttribute("role") === "region"
  && panel().getAttribute("aria-labelledby") === "overview-players-title", "region");
// A refresh of the names is never spoken: the panel opts out of the polite page region.
same(panel().getAttribute("aria-live"), "off", "names in a live region");
const heading = byId("overview-players-title");
same(`${heading.tagName} ${heading.textContent}`, "H3 Players online", "title");
check(panel().textContent.includes("Loading player names…") && !panel().querySelector("ul"), "loading");
check(panel().lastElementChild.textContent
  === "Names are shown in this window only. DayZ-ServerMan does not log or save them.", "privacy foot");
same(reads.length, 1, "one read on opening");
// Closing drops the panel content; opening again reads the names and lists them sorted by name.
toggleButton().click(); await wait(20);
check(panel().hidden && !panel().children.length && toggleButton().getAttribute("aria-expanded") === "false", "closed");
answers.push(ok({state: "OK", players: named}));
await openPanel();
// By name, case and accents aside; the player still connecting comes last.
same(names().join("|"), `Ärzte_Bob|ash_Walker|Bravo|${LONG}|zelenoScout|Connecting player`, "sorted names");
check(panel().querySelector(".is-unnamed").textContent === "Connecting player", "connecting player style");
same([...panel().querySelectorAll(".overview-player-time")].map((node) => node.textContent).join("|"),
  "1 h 4 min|3 h 12 min|2 d 3 h|less than a minute|less than a minute", "durations");
same(panel().querySelector(".overview-player-time").getAttribute("aria-label"), "connected 1 h 4 min", "time name");
check(!panel().querySelectorAll("li")[2].querySelector(".overview-player-time"), "unknown time shown");
same(panel().querySelector("ul").getAttribute("aria-label"), "6 players, name and time connected", "list name");
const cut = [...panel().querySelectorAll(".overview-player-name")].find((node) => node.textContent === LONG);
check(cut.title === LONG && !panel().querySelector(".overview-player-name:not([title])").title, "cut name title");
check(/^Updated \d\d:\d\d:\d\d · refreshes every 10 s while open$/
  .test(panel().querySelector(".overview-meta-subtle").textContent),
  "updated line");
check(!panel().querySelector("ul").hasAttribute("tabindex") && byId("overview-players-more").hidden,
  "short list scrolls");
// Names stay in the window: not in the toggle, the application status, the announcers, or browser storage.
const elsewhere = [toggleButton().title, toggleButton().getAttribute("aria-label"),
  byId("application-status").textContent,
  byId("operation-announcer").textContent, byId("operation-alert").textContent, JSON.stringify(localStorage),
  JSON.stringify(sessionStorage), document.title].join(" ");
check(!["ash_Walker", "zelenoScout", "Bravo"].some((name) => elsewhere.includes(name)), "a name left the panel");
// A redraw of the strip keeps the panel open with its names and reads nothing more.
window.ServerManOverview.refreshServer(); await wait(20);
check(!panel().hidden && names().length === 6 && reads.length === 2, "redraw closed the panel or read");
// Escape inside the panel closes it and gives the focus back to the toggle.
panel().querySelector("ul").dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
check(panel().hidden && document.activeElement === toggleButton(), "escape");
// States without a list: names not reported while players are counted, no players, failed reads.
const opened = async (answer, count = 12) => { host.status = running2({players: count, max_players: 60}); await tick();
  answers.push(answer); await openPanel(); const text = panel().textContent; toggleButton().click(); await wait(20);
  return text; };
check((await opened(ok({state: "OK", players: []})))
  .includes("Player names are not available from this server. The count comes from its status answer."), "unavailable");
check((await opened(ok({state: "OK", players: []}), 0)).includes("No players are online."), "no players");
const failed = "Player names could not be read: the server did not answer. The next try is in 10 s.";
check((await opened(ok({state: "NO_ANSWER", players: []}))).includes(failed), "no answer");
check((await opened(ok({state: "NOT_RUNNING", players: []}))).includes(failed), "not running");
check((await opened(fail("INTERNAL_FAILURE", "x"))).includes(failed), "bridge failure");
check((await opened(new Error("bridge"))).includes(failed), "thrown read");
// A failed refresh removes the old list and keeps the time of the last good read; the count stays.
answers.push(ok({state: "OK", players: named}), ok({state: "NO_ANSWER", players: []}));
await openPanel();
const updated = panel().querySelector(".overview-meta-subtle").textContent;
await fire();
check(!panel().querySelector("ul") && panel().textContent.includes(failed), "old list kept after failure");
same(panel().querySelector(".overview-meta-subtle").textContent, updated, "time of the last good read");
same(toggleButton().textContent, "Players 12 / 60", "count after failure");
"""

# The 10 s refresh: only while open, on a visible Overview, with a running server
CADENCE = PLAYERS_HEAD + r"""
await start(); await wait(80);
same(timers.size, 0, "a timer before opening");
answers.push(ok({state: "OK", players: roster(4)}));
await openPanel();
same(reads.length, 1, "read on opening"); same(timers.size, 1, "one planned refresh");
// Each planned refresh reads once and plans the next; the list stays while "Updating…" shows.
answers.push("pending");
await fire();
same(reads.length, 2, "second read");
check(panel().querySelectorAll("li").length === 4 && panel().textContent.includes("Updating…"), "refresh state");
same(timers.size, 0, "a refresh planned while a read runs");
// Closing clears the planned refresh; a fired old timer reads nothing.
toggleButton().click(); await wait(20);
same(timers.size, 0, "timer left after closing");
// QF-065: a closed panel never reads, not even when the window becomes visible again or a poll tick comes.
for (const hidden of [true, false]) {
  Object.defineProperty(document, "hidden", {configurable: true, get: () => hidden});
  document.dispatchEvent(new Event("visibilitychange")); await wait(40);
}
await tick(); await fire();
same(reads.length, 2, "names read while the panel is closed");
answers.length = 0; answers.push(ok({state: "OK", players: roster(4)}));
await openPanel(); same(reads.length, 3, "read on reopening");
// A hidden window waits; it reads at once when it is visible again.
Object.defineProperty(document, "hidden", {configurable: true, get: () => true});
await fire(); same(reads.length, 3, "read while hidden"); same(timers.size, 0, "plan while hidden");
Object.defineProperty(document, "hidden", {configurable: true, get: () => false});
document.dispatchEvent(new Event("visibilitychange")); await wait(40);
same(reads.length, 4, "read after the window is visible");
same(timers.size, 1, "plan after the window is visible");
// Another page: the panel closes and no read follows; back on Overview it starts closed.
commitSection("logs"); await wait(60);
await fire(); await tick();
same(reads.length, 4, "read on another page");
commitSection("overview"); await wait(120);
check(panel().hidden && toggleButton().getAttribute("aria-expanded") === "false", "panel open after returning");
same(timers.size, 0, "timer after returning");
// A server that stops closes the panel and stops the reads.
await openPanel(); same(reads.length, 5, "read on opening again");
host.status = {...running2(), state: "STOPPED", readiness: null, profile_id: null, players: null, max_players: null};
await tick();
check(!panel() && !toggleButton(), "count while stopped");
await fire(); same(reads.length, 5, "read after the stop");
"""

# The scroll cue: 25 names scroll inside the list, with a visible cue and a keyboard stop
SCROLL = PLAYERS_HEAD + r"""
host.status = running2({players: 25, max_players: 60});
await start(); await wait(80);
const players = roster(25); players[24].name = "";
answers.push(ok({state: "OK", players}), ok({state: "OK", players}));
await openPanel();
const list = panel().querySelector("ul"); const cue = byId("overview-players-more");
check(list.scrollHeight > list.clientHeight && list.getBoundingClientRect().height <= 182.5, "list height");
same(list.tabIndex, 0, "keyboard stop on the scrolling list");
check(!cue.hidden && /^\d+ more names below · scroll the list$/.test(cue.textContent), `cue: ${cue.textContent}`);
same(cue.getAttribute("aria-hidden"), "true", "cue hidden from screen readers");
check(list.parentElement.classList.contains("has-more"), "fade while more names follow");
same(getComputedStyle(list.parentElement, "::after").content, '""', "fade drawn");
const hiddenBelow = [...list.children]
  .filter((item) => item.offsetTop + item.offsetHeight / 2 > list.clientHeight).length;
same(cue.textContent.startsWith(`${hiddenBelow} more`), true, "cue count");
// Scrolling to the end: the cue says so, keeps its line, and the fade goes.
const height = panel().getBoundingClientRect().height;
list.scrollTop = list.scrollHeight; list.dispatchEvent(new Event("scroll")); await wait(20);
same(cue.textContent, "End of the list", "cue at the end");
check(!cue.hidden && !list.parentElement.classList.contains("has-more"), "fade at the end");
same(panel().getBoundingClientRect().height, height, "panel height at the end");
// A refresh keeps the scroll position and the focus of the list.
list.scrollTop = 40; list.dispatchEvent(new Event("scroll")); list.focus();
await fire();
const after = panel().querySelector("ul");
check(after !== list && after.scrollTop === 40 && document.activeElement === after, "scroll or focus after refresh");
// Wide window: three columns.
check(getComputedStyle(after).gridTemplateColumns.split(" ").length === 3, "three columns at 1150 px");
"""

# Narrow workspace: one column and a higher list
NARROW = PLAYERS_HEAD + r"""
host.status = running2({players: 25, max_players: 60});
await start(); await wait(80);
answers.push(ok({state: "OK", players: roster(25)}));
await openPanel();
const list = panel().querySelector("ul");
same(getComputedStyle(list).gridTemplateColumns.split(" ").length, 1, "one column");
same(getComputedStyle(list).maxHeight, "240px", "narrow height");
check(document.documentElement.scrollWidth <= window.innerWidth, "sideways scroll");
check(!byId("overview-players-more").hidden, "cue");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class OverviewPlayersDynamicTests(unittest.TestCase):
    """D18: player count and names panel in the composed shell."""

    def test_count_states_and_in_place_updates(self) -> None:
        """The count shows only while running, updates in place, and is never announced."""
        self.assertEqual(run_shell_harness(COUNT, window_size="1174,812", budget=8000), "PASS")

    def test_panel_open_close_keyboard_states_and_privacy(self) -> None:
        """The panel opens and closes from the toggle, lists sorted names, and shows every state."""
        self.assertEqual(run_shell_harness(PANEL, window_size="1174,812", budget=10000), "PASS")

    def test_refresh_cadence_with_fake_timers(self) -> None:
        """Names are read every 10 s only while open on a visible Overview of a running server."""
        self.assertEqual(run_shell_harness(CADENCE, window_size="1174,812", budget=10000), "PASS")

    def test_scroll_cue_for_a_long_list(self) -> None:
        """A list that scrolls inside says that more names follow and keeps its position on refresh."""
        self.assertEqual(run_shell_harness(SCROLL, window_size="1174,812", budget=8000), "PASS")

    def test_narrow_list_has_one_column(self) -> None:
        """Below 760 px the names stand in one column with a higher list."""
        self.assertEqual(run_shell_harness(NARROW, window_size="724,1000", budget=8000), "PASS")


if __name__ == "__main__":
    unittest.main()
