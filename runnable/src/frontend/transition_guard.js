// Shared transition guard that protects unsaved edits during navigation.
"use strict";

// Guard state: dirty owners, their actions, and the open dialog.
const dirtyOwners = new Map();
const ownerActions = new Map();
let pendingTransition = null;
let guardDialog = null;
let inertedElements = [];

// Recursively sort object keys so values compare deterministically.
function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  // Sort keys so property order never affects comparison.
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])]));
  }
  return value;
}

// Deep-freeze a copy of the value for reviewed requests.
function immutableCopy(value) {
  if (Array.isArray(value)) return Object.freeze(value.map(immutableCopy));
  if (value && typeof value === "object") {
    return Object.freeze(Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, immutableCopy(item)]),
    ));
  }
  return value;
}

// Produce a stable string fingerprint of a value.
function canonicalFingerprint(value) {
  return JSON.stringify(canonicalValue(value));
}

// List the owners that are currently dirty.
function dirtyOwnerIds(owners = null) {
  const scope = owners || [...dirtyOwners.keys()];
  return scope.filter((owner) => dirtyOwners.get(owner) === true);
}

// Report whether any owner holds unsaved changes.
function hasUnsavedChanges() {
  return dirtyOwnerIds().length > 0;
}

// Register an owner with its discard and focus actions.
function registerOwner(owner, discard, focus) {
  ownerActions.set(owner, Object.freeze({ discard, focus }));
  dirtyOwners.set(owner, false);
}

// Update one owner's dirty flag, rejecting unknown owners.
function setDirty(owner, dirty) {
  if (!ownerActions.has(owner)) throw new Error("Unknown transition-guard owner.");
  dirtyOwners.set(owner, dirty === true);
}

// Tear the dialog down and return the interrupted transition.
function closeGuardDialog() {
  const transition = pendingTransition;
  // Restore the page behind the dialog first.
  inertedElements.forEach(({ element, inert }) => { element.inert = inert; });
  inertedElements = [];
  if (guardDialog) guardDialog.remove();
  guardDialog = null;
  // Clear the pending transition so a later guard can open again.
  pendingTransition = null;
  return transition;
}

// Keep the current workspace and return focus to the first dirty owner.
function stay() {
  const transition = closeGuardDialog();
  const first = transition ? dirtyOwnerIds(transition.owners)[0] : null;
  const action = first ? ownerActions.get(first) : null;
  // Land the operator on the first control that holds unsaved edits.
  if (action && typeof action.focus === "function") action.focus();
}

// Discard every dirty owner in the transition, then run its commit.
function discardAndContinue() {
  const transition = pendingTransition;
  if (!transition) return;
  // Discard each listed owner and clear its dirty flag.
  dirtyOwnerIds(transition.owners).forEach((owner) => {
    ownerActions.get(owner).discard();
    dirtyOwners.set(owner, false);
  });
  closeGuardDialog();
  // Continue the interrupted action after the dialog closes.
  if (transition && typeof transition.commit === "function") transition.commit();
  const returnFocus = transition.returnFocus;
  // Restore focus to the invoking element, or to the page heading.
  if (returnFocus && returnFocus.isConnected && typeof returnFocus.focus === "function") {
    returnFocus.focus();
  } else {
    const heading = document.getElementById("page-title");
    if (heading) {
      heading.tabIndex = -1;
      heading.focus();
    }
  }
}

// Keep keyboard focus inside the guard dialog.
function containGuardFocus(event) {
  // Treat Escape as staying in place.
  if (event.key === "Escape") {
    event.preventDefault();
    stay();
    return;
  }
  if (event.key !== "Tab" || !guardDialog) return;
  // Wrap focus between the first and last buttons.
  const buttons = [...guardDialog.querySelectorAll("button")];
  const first = buttons[0];
  const last = buttons.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

// Open the unsaved-changes dialog for the interrupted transition.
function showGuard(message, commit, owners) {
  if (guardDialog) return;
  // Freeze the transition so its commit and owners cannot change mid-dialog.
  pendingTransition = Object.freeze({
    commit,
    owners: Object.freeze([...owners]),
    returnFocus: document.activeElement,
  });
  const dialog = document.createElement("section");
  // Describe the dialog as modal and label it.
  dialog.className = "unsaved-guard panel notice notice-warning";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "unsaved-guard-title");
  const title = document.createElement("h2");
  title.id = "unsaved-guard-title";
  title.textContent = "Unsaved changes";
  const copy = document.createElement("p");
  copy.textContent = message;
  // Offer staying in place or discarding the edits.
  const actions = document.createElement("div");
  actions.className = "action-row";
  const stayButton = document.createElement("button");
  stayButton.type = "button";
  stayButton.className = "button button-primary";
  stayButton.textContent = "Stay";
  stayButton.addEventListener("click", stay);
  const discardButton = document.createElement("button");
  discardButton.type = "button";
  discardButton.className = "button";
  discardButton.textContent = "Discard changes";
  discardButton.addEventListener("click", discardAndContinue);
  dialog.addEventListener("keydown", containGuardFocus);
  actions.append(stayButton, discardButton);
  dialog.append(title, copy, actions);
  document.body.append(dialog);
  guardDialog = dialog;
  // Suspend the page behind the dialog and focus the safe choice.
  inertedElements = [...document.body.children]
    .filter((element) => element !== dialog && !element.hasAttribute("data-announcer"))
    .map((element) => ({ element, inert: element.inert }));
  inertedElements.forEach(({ element }) => { element.inert = true; });
  stayButton.focus();
}

// Run a transition directly when nothing is dirty, otherwise guard it.
function requestTransition(message, commit) {
  const owners = [...dirtyOwners.keys()];
  if (!dirtyOwnerIds(owners).length) {
    commit();
    return true;
  }
  showGuard(message, commit, owners);
  return false;
}

// Guard a transition that concerns only one owner.
function requestOwnerTransition(owner, message, commit) {
  if (!ownerActions.has(owner)) throw new Error("Unknown transition-guard owner.");
  if (!dirtyOwnerIds([owner]).length) {
    commit();
    return true;
  }
  showGuard(message, commit, [owner]);
  return false;
}

// Ask for confirmation before the window closes with unsaved edits.
function requestNativeClose() {
  if (!hasUnsavedChanges()) return true;
  showGuard(
    "Stay and review your edits, or discard them before closing DayZ-ServerMan.",
    null,
    [...dirtyOwners.keys()],
  );
  return false;
}

// Publish the shared transition guard.
window.ServerManTransitions = Object.freeze({
  canonicalFingerprint,
  hasUnsavedChanges,
  immutableCopy,
  registerOwner,
  requestNativeClose,
  requestOwnerTransition,
  requestTransition,
  setDirty,
});
