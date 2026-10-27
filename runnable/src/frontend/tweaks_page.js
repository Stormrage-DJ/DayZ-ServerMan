// Tweaks page actions: one review and one apply for every changed part of the shown page.
"use strict";

// Applies of the page that finished while others still ran: a success, failure texts, and the failed drafts.
const tweakSettled = { succeeded: false, unchanged: false, failures: [], drafts: new Map() };

// List the changed targets of the shown page.
function pageTargets() { return activeTargets().filter(targetDirty); }
// Report whether every changed target of the page has a review that still matches it.
function pageReviewed() { const targets = pageTargets(); return targets.length > 0 && targets.every(targetReviewed); }
// Report whether a target of the page has an apply that runs or waits.
function pagePending() {
  const pending = new Set(tweaksState.pending.values());
  return pageTargets().some((target) => pending.has(target));
}

// Review every changed target of the page, then apply them all with the same action.
async function reviewOrApplyTweakPage() {
  const targets = pageTargets(); if (!targets.length) return;
  if (pageReviewed()) {
    // Apply in page order; a refused apply stops the rest, and its text stays in the feedback
    for (const target of targets) if (!await applyTweakTarget(target)) break;
    return renderTweaksActions();
  }
  let changes = 0; let unchanged = false;
  for (const target of targets.filter((item) => !targetReviewed(item))) {
    const review = await reviewTweakTarget(target);
    if (!review) return renderTweaksActions();
    unchanged ||= review.message === window.ServerManOperationMessages.medicalUnchanged;
    changes += review.changed_fields.length;
  }
  // Count the reviewed changes and files of the whole page, also those reviewed earlier
  const files = targets.length;
  const sentence = `${changes} change(s) validated in ${files} file(s). Select Apply changes to publish them.`;
  showTweakFeedback(unchanged && !changes ? window.ServerManOperationMessages.medicalUnchanged : sentence);
  renderTweaksActions();
}

// Rebuild the unsaved indicator, the one review or apply action of the page, and discard.
function renderTweaksActions() {
  const row = document.getElementById("tweaks-actions"); if (!row) return;
  row.replaceChildren();
  // Show the aggregate unsaved indicator.
  const status = window.ServerManUi.element("span", "unsaved-indicator",
    anyTweakDirty() ? "Unsaved tweak changes" : "No unsaved changes");
  if (anyTweakDirty()) status.classList.add("is-dirty"); row.append(status);
  // One action covers every changed part of the page: first the review, then the apply.
  if (pageTargets().length) {
    const reviewed = pageReviewed();
    const button = window.ServerManUi.element("button", "button button-primary", reviewed ? "Apply changes" : "Review changes");
    button.type = "button"; button.disabled = pagePending();
    // Only the apply step submits operations and is locked while another one runs or waits.
    if (reviewed) window.ServerManBusy?.mark(button);
    button.addEventListener("click", reviewOrApplyTweakPage); row.append(button);
  }
  // Offer discarding every draft at once.
  const discard = window.ServerManUi.element("button", "button", "Discard all changes");
  discard.type = "button"; discard.disabled = !anyTweakDirty(); discard.addEventListener("click", discardTweaks); row.append(discard);
}

// Track tweak applies until all of them settle, then reload once and report.
function tweakOperationFinished(operation) {
  const target = tweaksState.pending.get(operation.operation_id); if (!target) return false;
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) return true;
  tweaksState.pending.delete(operation.operation_id);
  if (operation.state === "SUCCEEDED") {
    tweakSettled.succeeded = true;
    // A medical toggle that changed no file keeps its sentence on the page after the reload.
    tweakSettled.unchanged ||= operation.result?.message === window.ServerManOperationMessages.medicalUnchanged;
  } else {
    // A failed part returns to review and keeps its draft over the reload.
    tweaksState.reviewed.delete(target);
    if (tweaksState.drafts.has(target)) tweakSettled.drafts.set(target, tweakCopy(tweaksState.drafts.get(target)));
    tweakSettled.failures.push(window.ServerManOperationBar?.pageResult(operation).text || "Tweak publication failed.");
  }
  // Wait for the other applies of the page; one reload follows the last one.
  if (tweaksState.pending.size) { renderTweaksActions(); return true; }
  const settled = { ...tweakSettled, failures: [...tweakSettled.failures], drafts: new Map(tweakSettled.drafts) };
  Object.assign(tweakSettled, { succeeded: false, unchanged: false, failures: [], drafts: new Map() });
  const report = () => {
    if (settled.failures.length) showTweakFeedback(settled.failures.join(" "), true);
    else if (settled.unchanged) showTweakFeedback(window.ServerManOperationMessages.medicalUnchanged);
  };
  if (!settled.succeeded) { report(); renderTweaksActions(); return true; }
  void loadTweaks().then(() => {
    settled.drafts.forEach((draft, key) => { if (tweaksState.drafts.has(key)) tweaksState.drafts.set(key, draft); });
    if (settled.drafts.size) { setTweakDirty(); renderTweaks(); }
    report();
  });
  return true;
}

// Publish the tweaks workspace entry points.
window.ServerManTweaks = Object.freeze({ open: loadTweaks, operationFinished: tweakOperationFinished });
