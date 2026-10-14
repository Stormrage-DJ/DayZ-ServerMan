// The close dialog of the editor: one dialog for unsaved changes, typed bounds and an unfinished outline (R15, 5.4).
"use strict";

// Texts: UI-UX 2026-10-14 00:10:31, 00:53:00 and 02:21:57, Product Owner 00:52:59. The wording module is near its
// size gate.
const mapCloseDialogWords = Object.freeze({
  title: Object.freeze({editor: "Close the map editor?", application: "Close DayZ-ServerMan?"}),
  unsaved: "Your latest changes to this map plan are not saved.", reason: (reason) => `Reason: ${reason}`,
  blocked: "They cannot be saved, because the plan must be reloaded first.",
  bounds: "The terrain bounds you entered are not saved.",
  boundsReview: "The terrain bounds you entered need a placement review, so they cannot be saved here. " +
    "Choose Keep editing to review the placements.",
  saveBounds: "Save changes and discard bounds", saveBoundsOutline: "Save changes, discard bounds and outline",
  kept: "If you discard them, the saved plan does not change.", application: "Keep editing keeps DayZ-ServerMan open.",
  outline: (count) => `The outline you are drawing is not finished (${count} ${count === 1 ? "point" : "points"}). ` +
    "It is not part of the plan yet.",
  keep: "Keep editing", save: "Save changes", discard: "Discard changes", discardOutline: "Discard outline",
  saveBoth: "Save changes and discard outline", discardBoth: "Discard changes and outline",
  discarded: "Your changes were discarded. The saved plan is shown.",
  boundsDiscarded: "The terrain bounds you entered were discarded. The fields show the current bounds again.",
});

// active: the open close dialog ({content, resolve, token}). A replacement changes the token, so the answer of the
// dialog that it closed is ignored.
const mapCloseDialogState = {active: null};

// The reason of the last failed save: the plan save, or the bounds save while typed bounds count. blocked: a
// refusal of either needs a reload, so nothing can save (UI-UX 2026-10-14 02:21:57).
function mapCloseDialogCause(bounds) {
  const autosave = window.ServerManMapAutosave, typed = bounds ? window.ServerManMapBounds.failure() : null;
  const plan = autosave.reason() ? {reason: autosave.reason(), at: autosave.failedAt()} : null;
  const last = plan && typed ? (typed.at > plan.at ? typed : plan) : plan ?? typed;
  const text = !last || /[.!?]$/.test(last.reason) ? last?.reason ?? "" : `${last.reason}.`;
  return {cause: text, blocked: autosave.blocked() || Boolean(typed?.reload)};
}

// Sentence order: unsaved, reason, blocked, bounds, discard, outline, application.
function mapCloseDialogText(content) {
  const words = mapCloseDialogWords, parts = [], changes = content.plan || content.bounds;
  if (content.plan) parts.push(words.unsaved);
  if (changes) parts.push(content.cause && words.reason(content.cause), content.blocked && words.blocked);
  if (content.bounds) parts.push(content.review ? words.boundsReview : words.bounds);
  if (changes) parts.push(words.kept);
  if (content.points) parts.push(words.outline(content.points));
  if (content.reason === "application") parts.push(words.application);
  return parts.filter(Boolean).join(" ");
}

// Keep editing comes first and has the first focus (`ServerManMapDom.dialog`); Escape keeps editing too.
function mapCloseDialogShow(active) {
  const words = mapCloseDialogWords, content = active.content, token = ++active.token, changes = content.plan || content.bounds;
  const choices = [{label: words.keep, value: "cancel"}];
  // Bounds that need the review are left out of the save; the plan save then drops them (UI-UX 02:21:57)
  const review = content.review && content.plan;
  if (changes && content.save) choices.push({value: "save", label: review ? (content.points ? words.saveBoundsOutline :
    words.saveBounds) : (content.points ? words.saveBoth : words.save)});
  choices.push({label: changes ? (content.points ? words.discardBoth : words.discard) : words.discardOutline,
    value: "close", danger: true});
  window.ServerManMapDom.dialog(words.title[content.reason] ?? words.title.editor, mapCloseDialogText(content), choices)
    .then((value) => {
      if (token !== active.token) return;
      if (mapCloseDialogState.active === active) mapCloseDialogState.active = null;
      active.resolve(value);
    });
}

// Ask once; content: {reason, plan, cause, blocked, bounds, review, points, save}.
function mapCloseDialogAsk(content) {
  return new Promise((resolve) => {
    const active = {content, resolve, token: 0};
    mapCloseDialogState.active = active;
    mapCloseDialogShow(active);
  });
}

// An application close that joins an open editor dialog closes it without an answer and opens the application
// form with the same content in its place, so a screen reader announces it (UI-UX 2026-10-14 00:53:00).
function mapCloseDialogUpgrade() {
  const active = mapCloseDialogState.active;
  if (!active || active.content.reason === "application") return;
  active.content = {...active.content, reason: "application"};
  active.token += 1;
  [...document.querySelectorAll("dialog")].at(-1)?.dispatchEvent(new Event("cancel", {cancelable: true}));
  mapCloseDialogShow(active);
}

// The close module asks and upgrades; the release uses the discard texts.
window.ServerManMapCloseDialog = Object.freeze({ask: mapCloseDialogAsk, upgrade: mapCloseDialogUpgrade,
  cause: mapCloseDialogCause, words: mapCloseDialogWords});
