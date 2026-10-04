// Tweaks workspace: load, review, apply, and discard configuration tweaks.
"use strict";

// Workspace state: snapshots, drafts, reviews, and pending applies.
const tweaksState = {
  profileId: null, snapshots: new Map(), baselines: new Map(), drafts: new Map(),
  reviewed: new Map(), pending: new Map(), errors: new Map(), generation: 0,
  area: "gameplay", subtab: "player",
};
// Targets backed by mission configuration files.
const missionTargets = new Set(["economy", "weather", "starter_loadout", "events"]);
// Every managed target loaded for the tweaks workspace.
const loadTargets = ["gameplay", "economy", "weather", "starter_loadout", "events"];
// Medical feature targets backed by the legacy feature adapter.
const medicalTargets = new Set(["medical_loot_zones", "medical_item_spawns"]);

// Copy a tweak value so drafts never alias stored snapshots.
function tweakCopy(value) { return JSON.parse(JSON.stringify(value)); }
// Fingerprint a tweak value for change and review tracking.
function tweakFingerprint(value) { return window.ServerManTransitions.canonicalFingerprint(value); }
// Report whether one target's draft differs from its baseline.
function targetDirty(target) {
  if (!tweaksState.baselines.has(target) || !tweaksState.drafts.has(target)) return false;
  return tweakFingerprint(tweaksState.drafts.get(target)) !== tweakFingerprint(tweaksState.baselines.get(target));
}
// Report whether any target holds unsaved tweak changes.
function anyTweakDirty() { return [...tweaksState.drafts.keys()].some(targetDirty); }
// Publish the aggregate dirty state to the transition guard.
function setTweakDirty() { window.ServerManTransitions.setDirty("tweaks", anyTweakDirty()); }

// Reset every draft to its baseline and refresh the workspace.
function discardTweaks() {
  tweaksState.drafts = new Map([...tweaksState.baselines].map(([key, value]) => [key, tweakCopy(value)]));
  tweaksState.reviewed.clear(); setTweakDirty(); renderTweaks();
}
// Move focus to the first tweak control.
function focusTweaks() { document.querySelector("#tweaks-workspace input, #tweaks-workspace select")?.focus(); }

// Reduce a loaded snapshot to the values the editor tracks.
function snapshotValues(target, snapshot) {
  // Medical features track only their enabled flag.
  if (medicalTargets.has(target)) return { enabled: snapshot.enabled };
  // Mission targets carry the whole values object.
  if (missionTargets.has(target)) return tweakCopy(snapshot.values);
  return Object.fromEntries(snapshot.fields.map((item) => [item.key, item.value]));
}

// Load every managed target and prepare the editable drafts.
async function loadTweaks() {
  const profileId = window.ServerManProfileContext.selectedId();
  const generation = ++tweaksState.generation;
  const workspace = window.ServerManWorkspace.capture("tweaks");
  // Show the loading state while each target is read.
  window.ServerManUi.renderLoading("Loading tweaks", "Reading each managed DayZ configuration target.");
  try {
    // Load the mission, standard, and medical targets together.
    const results = await Promise.all(loadTargets.map(async (target) => {
      const method = missionTargets.has(target) ? "load_mission_configuration" : "load_configuration";
      return [target, await window.pywebview.api[method](profileId, target)];
    }));
    const medical = await window.pywebview.api.load_medical_features(profileId);
    if (generation !== tweaksState.generation || !window.ServerManWorkspace.isActive(workspace)) return;
    // Collect per-target failures for partial rendering.
    tweaksState.errors = new Map(results.filter(([, result]) => !result.success)
      .map(([target, result]) => [target, window.ServerManOperationMessages.bridgeError(
        result, "This part of the tweaks could not be loaded.")]));
    // Fold loaded medical features into the same result set.
    if (medical.success) {
      Object.entries(medical.value.features).forEach(([feature, value]) => results.push([feature, {
        success: true, value: { ...value, feature, profile_id: medical.value.profile_id,
          profile_revision: medical.value.profile_revision, settings_revision: medical.value.settings_revision },
      }]));
    } else {
      const message = window.ServerManOperationMessages.bridgeError(medical, "Medical features could not be loaded.");
      medicalTargets.forEach((target) => tweaksState.errors.set(target, message));
    }
    // Adopt snapshots, baselines, and fresh drafts under the current profile.
    const loaded = results.filter(([, result]) => result.success);
    tweaksState.profileId = profileId;
    tweaksState.snapshots = new Map(loaded.map(([target, result]) => [target, result.value]));
    tweaksState.baselines = new Map(loaded.map(([target, result]) => [target, snapshotValues(target, result.value)]));
    tweaksState.drafts = new Map([...tweaksState.baselines].map(([target, value]) => [target, tweakCopy(value)]));
    tweaksState.reviewed.clear(); tweaksState.pending.clear(); setTweakDirty(); renderTweaks();
  } catch (error) {
    if (generation !== tweaksState.generation || !window.ServerManWorkspace.isActive(workspace)) return;
    // Report unexpected load failures through the host error panel.
    window.ServerManUi.renderHostError({ error: {
      message: error instanceof Error ? error.message : "Tweaks could not be loaded.",
    } });
  }
}

// Read the current draft value for one control.
function tweakValue(target, key) {
  const draft = tweaksState.drafts.get(target);
  // Events are edited per event, not by flat key.
  return target === "events" ? undefined : draft?.[key];
}
// Update one draft value and invalidate its review.
function setTweakValue(target, key, value) {
  tweaksState.drafts.get(target)[key] = value;
  tweaksState.reviewed.delete(target); setTweakDirty(); renderTweaksActions();
}
// Update one event field and invalidate the events review.
function setEventValue(name, key, value) {
  tweaksState.drafts.get("events").events[name][key] = value;
  tweaksState.reviewed.delete("events"); setTweakDirty(); renderTweaksActions();
}
// Toggle one starter item while keeping the canonical order.
function setStarterItem(item, enabled) {
  const items = new Set(tweaksState.drafts.get("starter_loadout").items);
  if (enabled) items.add(item); else items.delete(item);
  // Rebuild the item list in the catalog's canonical order.
  const order = Object.values(window.ServerManTweaksCatalog.starterGroups).flat();
  tweaksState.drafts.get("starter_loadout").items = order.filter((candidate) => items.has(candidate));
  tweaksState.reviewed.delete("starter_loadout"); setTweakDirty(); renderTweaksActions();
}

// Reduce a target's draft to the values that changed.
function changedUpdates(target) {
  const before = tweaksState.baselines.get(target); const after = tweaksState.drafts.get(target);
  // Compare each event field and keep only changed values.
  if (target === "events") {
    const events = {};
    Object.entries(after.events).forEach(([name, fields]) => {
      const changed = Object.fromEntries(Object.entries(fields).filter(([key, value]) => value !== before.events[name]?.[key]));
      if (Object.keys(changed).length) events[name] = changed;
    });
    return { events };
  }
  // Starter loadouts publish their full ordered item list.
  if (target === "starter_loadout") return { items: [...after.items] };
  return Object.fromEntries(Object.entries(after).filter(([key, value]) => value !== before[key]));
}

// Build the host argument list for one target kind.
function editArguments(target, updates) {
  const snapshot = tweaksState.snapshots.get(target);
  if (medicalTargets.has(target)) return [snapshot.profile_id, target, updates.enabled,
    snapshot.profile_revision, snapshot.settings_revision, snapshot.digest];
  if (missionTargets.has(target)) return [snapshot.profile_id, target, snapshot.profile_revision,
    snapshot.settings_revision, snapshot.digest, updates];
  return [snapshot.profile_id, target, snapshot.profile_revision, snapshot.settings_revision,
    snapshot.digest, snapshot.server_config_digest, updates];
}

// Review changed values first, then apply the reviewed arguments.
async function reviewOrApplyTweakTarget(target) {
  const updates = changedUpdates(target);
  if (!Object.keys(updates).length || (updates.events && !Object.keys(updates.events).length)) return;
  const fingerprint = tweakFingerprint(updates);
  const generation = tweaksState.generation; const profileId = tweaksState.profileId;
  const workspace = window.ServerManWorkspace.capture("tweaks");
  const reviewed = tweaksState.reviewed.get(target);
  const args = editArguments(target, window.ServerManTransitions.immutableCopy(updates));
  // Review again when the values changed since the last review.
  if (!reviewed || reviewed.fingerprint !== fingerprint) {
    const method = medicalTargets.has(target) ? "preview_medical_feature"
      : missionTargets.has(target) ? "preview_mission_configuration" : "preview_configuration";
    const result = await window.pywebview.api[method](...args);
    if (generation !== tweaksState.generation || profileId !== tweaksState.profileId
        || !window.ServerManWorkspace.isActive(workspace)) return;
    if (!result.success) return showTweakError(result, "Changes could not be reviewed.");
    // Remember the reviewed arguments and ask the operator to apply.
    tweaksState.reviewed.set(target, { fingerprint, args });
    showTweakFeedback(`${result.value.changed_fields.length} ${target.replaceAll("_", " ")} change(s) validated. Select Apply to publish one file.`);
    return renderTweaksActions();
  }
  const method = medicalTargets.has(target) ? "apply_medical_feature"
    : missionTargets.has(target) ? "apply_mission_configuration" : "apply_configuration";
  const result = await window.pywebview.api[method](...reviewed.args);
  if (generation !== tweaksState.generation || profileId !== tweaksState.profileId
      || !window.ServerManWorkspace.isActive(workspace)) return;
  if (!result.success) return showTweakError(result, "Changes could not be queued.");
  // Track the queued operation and refresh the actions; the operation bar shows it.
  tweaksState.pending.set(result.value.operation_id, target);
  window.ServerManOperationBar?.adopt(result.value.operation_id);
  renderTweaksActions();
}

// Show one notice in the tweaks feedback region.
function showTweakFeedback(message, error = false) {
  const region = document.getElementById("tweaks-feedback");
  if (!region) return;
  const notice = window.ServerManUi.element("div", `notice ${error ? "notice-error" : ""}`, message);
  notice.setAttribute("role", error ? "alert" : "status"); region.replaceChildren(notice);
}
// Report a tweak failure with a fallback message.
function showTweakError(result, fallback) {
  showTweakFeedback(window.ServerManOperationMessages.bridgeError(result, fallback), true);
}

// List the targets the operator can act on in the current view.
function activeTargets() {
  // Event and population views publish the events target only.
  if (tweaksState.area === "events" || tweaksState.area === "population") return ["events"];
  // Expand medical groups to their individual feature keys.
  return [...new Set(window.ServerManTweaksCatalog.gameplayTabs
    .find((tab) => tab.id === tweaksState.subtab).groups.flatMap((item) =>
      item.target === "medical" ? item.fields.map((field) => field.key) : [item.target]))];
}

// Rebuild the review, apply, and discard actions for the current drafts.
function renderTweaksActions() {
  const row = document.getElementById("tweaks-actions"); if (!row) return;
  row.replaceChildren();
  // Show the aggregate unsaved indicator.
  const status = window.ServerManUi.element("span", "unsaved-indicator",
    anyTweakDirty() ? "Unsaved tweak changes" : "No unsaved changes");
  if (anyTweakDirty()) status.classList.add("is-dirty"); row.append(status);
  // Offer review or apply per dirty target.
  activeTargets().forEach((target) => {
    if (!targetDirty(target)) return;
    const button = window.ServerManUi.element("button", "button button-primary",
      tweaksState.reviewed.has(target) ? `Apply ${target.replaceAll("_", " ")}` : `Review ${target.replaceAll("_", " ")}`);
    button.type = "button"; button.disabled = [...tweaksState.pending.values()].includes(target);
    // Only the apply step submits an operation and is locked while another one runs or waits.
    if (tweaksState.reviewed.has(target)) window.ServerManBusy?.mark(button);
    button.addEventListener("click", () => reviewOrApplyTweakTarget(target)); row.append(button);
  });
  // Offer discarding every draft at once.
  const discard = window.ServerManUi.element("button", "button", "Discard all changes");
  discard.type = "button"; discard.disabled = !anyTweakDirty(); discard.addEventListener("click", discardTweaks); row.append(discard);
}

// Switch the tweaks area and rerender.
function chooseTweakArea(area) { tweaksState.area = area; renderTweaks(); }
// Switch the gameplay subtab and rerender.
function chooseGameplayTab(tab) { tweaksState.subtab = tab; renderTweaks(); }

// Convert the legacy starter block after the operator confirms.
async function reviewStarterConversion() {
  const snapshot = tweaksState.snapshots.get("starter_loadout");
  if (!snapshot?.conversion_required || !await starterConversionDialog(snapshot)) return;
  const generation = tweaksState.generation; const workspace = window.ServerManWorkspace.capture("tweaks");
  const result = await window.pywebview.api.convert_starter_loadout(
    snapshot.profile_id, snapshot.profile_revision, snapshot.settings_revision, snapshot.digest,
  );
  if (generation !== tweaksState.generation || !window.ServerManWorkspace.isActive(workspace)) return;
  if (!result.success) return showTweakError(result, "Legacy starter loadout could not be converted.");
  // Track the queued conversion and refresh the workspace; the operation bar shows it.
  tweaksState.pending.set(result.value.operation_id, "starter_loadout");
  window.ServerManOperationBar?.adopt(result.value.operation_id); renderTweaks();
}

// Render the tweaks workspace through the render module.
function renderTweaks() {
  window.ServerManTweaksRender.workspace({
    state: tweaksState, value: tweakValue, setValue: setTweakValue, setEventValue,
    setStarterItem, reviewStarterConversion, chooseArea: chooseTweakArea, chooseTab: chooseGameplayTab,
  });
  // Clear the busy state once the workspace is rendered.
  renderTweaksActions(); document.getElementById("content-region").setAttribute("aria-busy", "false");
}

// Track tweak applies until they settle, then reload or report.
function tweakOperationFinished(operation) {
  const target = tweaksState.pending.get(operation.operation_id); if (!target) return false;
  if (!["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"].includes(operation.state)) return true;
  tweaksState.pending.delete(operation.operation_id);
  // Reload after success, otherwise return the target to review.
  if (operation.state === "SUCCEEDED") void loadTweaks();
  else {
    tweaksState.reviewed.delete(target);
    showTweakFeedback(window.ServerManOperationBar?.pageResult(operation).text || "Tweak publication failed.", true);
    renderTweaksActions();
  }
  return true;
}

// Register the tweaks workspace with the shared transition guard.
window.ServerManTransitions.registerOwner("tweaks", discardTweaks, focusTweaks);
// Publish the tweaks workspace entry points.
window.ServerManTweaks = Object.freeze({ open: loadTweaks, operationFinished: tweakOperationFinished });
