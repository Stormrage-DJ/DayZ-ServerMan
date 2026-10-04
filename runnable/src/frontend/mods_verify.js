// Verify files: the on-demand full content check of the Workshop mods and their server folder copies.
"use strict";

// Profile, row in progress, and per-mod problems of the last verification shown in the mod table.
const modsVerifyState = { profileId: "", activeId: "", problems: {} };
// The two phases of a verification; their wording comes from the catalogue.
const verifyPhases = Object.freeze(["verify_source", "verify_target"]);
// Operator wording for each problem state of a download.
const verifySourceProblems = Object.freeze({
  MISSING: "Download missing", FAILED: "Download could not be read",
  CHANGED: "Download changed since it was recorded",
});
// Operator wording for each problem state of a server folder copy.
const verifyTargetProblems = Object.freeze({
  NOT_APPLIED: "Not applied to the server folder", DIFFERS: "Server copy differs from the download",
  FAILED: "Server copy could not be read",
});
// Accessible description of the action: what it reads and how long it can take.
const VERIFY_FILES_DESCRIPTION = "Reads all mod files of this server profile and compares them with "
  + "the server folder. This can take minutes. No file is changed.";

// Report whether a verification cannot start: busy page, no profile, or no Workshop mod.
function verifyFilesBlocked() {
  return modsState.busy || !selectedProfile()
    || !modsState.inventory.some((row) => row.source_kind === "workshop" && row.workshop_id);
}

// Add the "Verify files" button and its description to the action group of the check header.
function attachVerifyFilesButton(header) {
  const button = modsNode("button", "button", "Verify files");
  button.id = "verify-mod-files"; button.type = "button";
  // Describe the cost of the action for assistive technology and as a tooltip.
  const description = modsNode("span", "sr-only", VERIFY_FILES_DESCRIPTION);
  description.id = "verify-mod-files-description";
  button.setAttribute("aria-describedby", description.id);
  button.title = VERIFY_FILES_DESCRIPTION;
  button.disabled = verifyFilesBlocked();
  button.addEventListener("click", verifyModFiles);
  // The verification runs on the operation lane, so the button is locked while another operation runs.
  header.querySelector(".mods-header-actions").append(window.ServerManBusy.mark(button), description);
  return header;
}

// Lock or unlock the visible "Verify files" button for the current page state.
function syncVerifyFilesButton() {
  const button = document.getElementById("verify-mod-files");
  if (button) button.disabled = verifyFilesBlocked();
}

// List the problems of one verified mod in operator wording.
function verifyItemProblems(item) {
  const problems = [];
  const source = verifySourceProblems[item?.source_state];
  if (source) problems.push(source);
  // A copy that had no readable download to compare with is not reported as unreadable.
  const compared = item?.target_state !== "FAILED" || !["MISSING", "FAILED"].includes(item?.source_state);
  const target = verifyTargetProblems[item?.target_state];
  if (target && compared) problems.push(target);
  return problems;
}

// Return the status-cell lines of one row: the running mark, or the problems that were found.
function verifyRowLines(row) {
  const id = row.workshop_id;
  if (!id || modsVerifyState.profileId !== (selectedProfile()?.profile_id || "")) return [];
  if (modsVerifyState.activeId === id) return [{ text: "Verifying…", title: "", tone: "verifying" }];
  return (modsVerifyState.problems[id] || []).map((text) => ({ text, title: "", tone: "problem" }));
}

// Capture what the verification adds to the rows, so a change redraws the table.
function verifyRowsSignature() {
  return [modsVerifyState.profileId, modsVerifyState.activeId, modsVerifyState.problems];
}

// Forget the row marks of an earlier verification and redraw the rows that showed them.
function clearVerifyMarks() {
  modsVerifyState.activeId = ""; modsVerifyState.problems = {};
  updateModInventory();
}

// Queue a verification for the selected profile through the shared pending-operation flow.
async function verifyModFiles() {
  const profile = selectedProfile();
  if (!profile) return modsFeedback("Select a profile first.", true);
  const context = captureModsContext();
  const result = await window.pywebview.api.verify_mod_files(
    profile.profile_id, profile.revision, modsState.settings.revision,
  );
  if (!isModsContextActive(context)) return;
  // Bind the row marks to the profile whose files are verified.
  if (result && result.success) { modsVerifyState.profileId = profile.profile_id; clearVerifyMarks(); }
  acceptModsOperation(result,
    window.ServerManOperationLabels.phase("VERIFY_WORKSHOP_FILES", verifyPhases[0]).text);
}

// Mark the row whose files a running verification reads; the operation bar shows phase and percent.
// Any other running operation outdates the earlier marks.
function verifyFilesProgress(operation) {
  if (operation.kind !== "VERIFY_WORKSHOP_FILES") {
    if (modsVerifyState.activeId || Object.keys(modsVerifyState.problems).length) clearVerifyMarks();
    return;
  }
  // Find the mod whose download or server copy is read right now.
  const items = operation.progress_detail?.items;
  const active = (Array.isArray(items) ? items : [])
    .find((item) => verifyPhases.includes(item?.phase));
  modsVerifyState.activeId = active ? String(active.workshop_id) : "";
  updateModInventory();
}

// Report a finished verification: the summary, the problem rows, a cancellation, or a failure.
function verifyFilesFinished(operation) {
  modsVerifyState.activeId = "";
  // The page shows the result of the verification itself, so the operation bar does not announce it again.
  const outcome = window.ServerManOperationBar.pageResult(operation);
  // A cancellation is the operator's choice and never an error.
  if (operation.state === "CANCELLED") {
    updateModInventory();
    modsFeedback(outcome.text);
    return true;
  }
  if (operation.state !== "SUCCEEDED") {
    updateModInventory();
    modsFeedback(outcome.text, true);
    return true;
  }
  // Keep the problems of each mod for its status cell.
  const items = Array.isArray(operation.result?.items) ? operation.result.items : [];
  const problems = {};
  items.forEach((item) => {
    const found = verifyItemProblems(item);
    if (found.length) problems[String(item.workshop_id)] = found;
  });
  modsVerifyState.problems = problems;
  updateModInventory();
  // Say nothing about a profile that is no longer the selected one.
  if (modsVerifyState.profileId !== selectedProfile()?.profile_id) return true;
  // Count the mods that need the operator, not the single lines.
  const count = Object.keys(problems).length;
  if (count) {
    modsFeedback(count === 1 ? "1 problem found" : `${count} problems found`);
    document.querySelector("#mods-feedback .notice")?.classList.add("notice-warning");
  } else {
    modsFeedback(!items.length ? "No Workshop mods to verify"
      : items.length === 1 ? "The mod is verified; the server copy matches"
        : `All ${items.length} mods verified; server copies match`);
  }
  return true;
}

// Publish the verification hooks used by the Mods workspace and its table.
window.ServerManModsVerify = Object.freeze({
  attach: attachVerifyFilesButton, sync: syncVerifyFilesButton, rowLines: verifyRowLines,
  signature: verifyRowsSignature, progress: verifyFilesProgress, finished: verifyFilesFinished,
});
