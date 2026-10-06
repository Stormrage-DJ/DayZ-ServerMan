"""Shared harness of the names panel tests (D18, QF-083): a fake names read, fake 10 s timers, rosters, and
the body of the nameless-rows check."""
from __future__ import annotations

try:
    from tests.overview_harness import OVERVIEW_HEAD
except ModuleNotFoundError:
    from overview_harness import OVERVIEW_HEAD


# Fake names read, fake 10 s timers, and a roster that arrives unsorted with one player without a name
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

# QF-083: DayZ leaves names empty, so rows are numbered by connection time, longest first
NAMELESS = r"""
host.status = running2({players: 3, max_players: 60});
await start(); await wait(80);
const NOTE = "This server does not share player names. Connection times are shown.";
const times = () => [...panel().querySelectorAll("li")].map((item) =>
  item.querySelector(".overview-player-time")?.textContent || "-").join("|");
// The real run: names empty, given in no particular order, one without a time.
const empty = (seconds) => ({name: "", duration_seconds: seconds});
answers.push(ok({state: "OK", players: [empty(2580), empty(null), empty(2700)]}));
await openPanel();
same(names().join("|"), "Player 1|Player 2|Player 3", "numbered rows");
same(times(), "45 min|43 min|-", "longest connected first, unknown time last");
check([...panel().querySelectorAll(".overview-player-name")].every((node) => node.classList.contains("is-unnamed")),
  "nameless style");
same(byId("overview-players-head").querySelector("p:last-child").textContent, NOTE, "note in the head");
same(panel().lastElementChild.textContent,
  "Names are shown in this window only. DayZ-ServerMan does not log or save them.", "privacy foot kept");
// A refresh: the times grew by 10 s, and the numbers stay with the same players; the note stays while it reads.
answers.push("pending");
await fire();
check(panel().textContent.includes("Updating…") && panel().textContent.includes(NOTE), "note while refreshing");
toggleButton().click(); await wait(20);
answers.length = 0;
answers.push(ok({state: "OK", players: [empty(2710), empty(2590), empty(null)]}));
await openPanel();
same(`${names().join("|")} ${times()}`, "Player 1|Player 2|Player 3 45 min|43 min|-", "numbers after a refresh");
// Mixed list: names sorted by name, then the numbered rows; no note.
toggleButton().click(); await wait(20);
answers.push(ok({state: "OK", players: [empty(60), {name: "zed", duration_seconds: 5}, empty(600),
  {name: "Anna", duration_seconds: 7}]}));
await openPanel();
same(names().join("|"), "Anna|zed|Player 1|Player 2", "mixed order");
same(times(), "less than a minute|less than a minute|10 min|1 min", "mixed times");
check(!panel().textContent.includes(NOTE), "note in a mixed list");
// 25 nameless players: the scroll cue and the keyboard stop still work.
toggleButton().click(); await wait(20);
host.status = running2({players: 25, max_players: 60}); await tick();
answers.push(ok({state: "OK", players: Array.from({length: 25}, (_, index) => empty(index * 60))}));
await openPanel();
same(names()[0] + " " + names()[24], "Player 1 Player 25", "25 numbered rows");
same(times().split("|")[0], "24 min", "longest first among 25");
check(panel().querySelector("ul").tabIndex === 0 && !byId("overview-players-more").hidden, "scroll cue");
"""
