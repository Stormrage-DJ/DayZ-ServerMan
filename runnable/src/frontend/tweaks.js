// Tweaks workspace: load, review, apply, and discard configuration tweaks.
"use strict";

// Workspace state: snapshots, drafts, reviews, and pending applies.
const tweaksState = {
  profileId: null, snapshots: new Map(), baselines: new Map(), drafts: new Map(),
  reviewed: new Map(), pending: new Map(), errors: new Map(), generation: 0,
  area: "gameplay", subtab: "player",
  // True while the server configuration does not use cfgGameplay.json; the page offers to turn it on
  gameplayOff: false, enablingGameplay: false,
};
// The group notice while the gameplay file is off; the switch itself sits at the top of Gameplay Tweaks
const GAMEPLAY_OFF_GROUP_TEXT = "Turn on the gameplay file above to change these settings.";
// Targets backed by mission configuration files.
const missionTargets = new Set(["economy", "weather", "starter_loadout", "events", "loot_supply", "weapon_magazines"]);
// Every managed target loaded for the tweaks workspace.
const loadTargets = ["gameplay", "economy", "weather", "starter_loadout", "events", "loot_supply", "weapon_magazines"];
// Medical feature targets backed by the legacy feature adapter.
const medicalTargets = new Set(["medical_loot_zones"]);

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
  const entryLoad = window.ServerManMapEditorEntry.beginLoad(profileId, workspace);
  // Show the loading state while each target is read.
  window.ServerManUi.renderLoading("Loading tweaks", "Reading each managed DayZ configuration target.");
  window.ServerManTweaksRender.entry(document.getElementById("content-region").firstElementChild);
  try {
    // Load the mission, standard, and medical targets together.
    const results = await Promise.all(loadTargets.map(async (target) => {
      const method = missionTargets.has(target) ? "load_mission_configuration" : "load_configuration";
      return [target, await window.pywebview.api[method](profileId, target)];
    }));
    const medical = await window.pywebview.api.load_medical_features(profileId);
    if (generation !== tweaksState.generation || !window.ServerManWorkspace.isActive(workspace)) return;
    window.ServerManMapEditorEntry.loaded(entryLoad, results.find(([target]) => target === "economy")[1]);
    // Collect per-target failures for partial rendering.
    tweaksState.errors = new Map(results.filter(([, result]) => !result.success)
      .map(([target, result]) => [target, window.ServerManOperationMessages.bridgeError(
        result, "This part of the tweaks could not be loaded.")]));
    // A gameplay file that is off is no failure: the page offers the switch and the groups point to it
    tweaksState.gameplayOff = results.some(([target, result]) => target === "gameplay"
      && result.error?.code === "GAMEPLAY_NOT_ENABLED");
    tweaksState.enablingGameplay = false;
    if (tweaksState.gameplayOff) tweaksState.errors.set("gameplay", GAMEPLAY_OFF_GROUP_TEXT);
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
    window.ServerManMapEditorEntry.loaded(entryLoad, { error: { message: error instanceof Error ? error.message : "" } });
    // Report unexpected load failures through the host error panel.
    window.ServerManUi.renderHostError({ error: {
      message: error instanceof Error ? error.message : "Tweaks could not be loaded.",
    } });
    window.ServerManTweaksRender.entry(document.getElementById("content-region").firstElementChild);
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

// Keep or turn off the mission's own starting items.
function setStarterMissionItems(enabled) {
  tweaksState.drafts.get("starter_loadout").mission_items = enabled;
  tweaksState.reviewed.delete("starter_loadout"); setTweakDirty(); renderTweaks();
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
  // Starter loadouts publish their full ordered item list and whether the mission's own items stay on.
  if (target === "starter_loadout") return { items: [...after.items], mission_items: after.mission_items };
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

// Report whether a target's current changes have a review that still matches them.
function targetReviewed(target) {
  return tweaksState.reviewed.get(target)?.fingerprint === tweakFingerprint(changedUpdates(target));
}

// Report whether the workspace is still the one that started an asynchronous step.
function tweakStepCurrent(step) {
  return step.generation === tweaksState.generation && step.profileId === tweaksState.profileId
    && window.ServerManWorkspace.isActive(step.workspace);
}

// Capture the workspace that an asynchronous step belongs to.
function tweakStep() {
  return { generation: tweaksState.generation, profileId: tweaksState.profileId,
    workspace: window.ServerManWorkspace.capture("tweaks") };
}

// Review one target's changes. Return the preview value, or null after a refusal that the feedback shows.
async function reviewTweakTarget(target) {
  const updates = changedUpdates(target); const step = tweakStep();
  const args = editArguments(target, window.ServerManTransitions.immutableCopy(updates));
  const method = medicalTargets.has(target) ? "preview_medical_feature"
    : missionTargets.has(target) ? "preview_mission_configuration" : "preview_configuration";
  const result = await window.pywebview.api[method](...args);
  if (!tweakStepCurrent(step)) return null;
  if (!result.success) { showTweakError(result, "Changes could not be reviewed."); return null; }
  // Remember the reviewed arguments. A zone review of an editor-managed mission returns a fingerprint, and its
  // Apply sends it back (8.13).
  const reviewedFingerprint = result.value.preview_fingerprint;
  tweaksState.reviewed.set(target, { fingerprint: tweakFingerprint(updates),
    args: typeof reviewedFingerprint === "string" ? [...args, reviewedFingerprint] : args });
  return result.value;
}

// Queue the reviewed change of one target. Return false after a refusal that the feedback shows.
async function applyTweakTarget(target) {
  const step = tweakStep();
  const method = medicalTargets.has(target) ? "apply_medical_feature"
    : missionTargets.has(target) ? "apply_mission_configuration" : "apply_configuration";
  const result = await window.pywebview.api[method](...tweaksState.reviewed.get(target).args);
  if (!tweakStepCurrent(step)) return false;
  if (!result.success) { showTweakError(result, "Changes could not be queued."); return false; }
  // Track the queued operation; the operation bar shows it.
  tweaksState.pending.set(result.value.operation_id, target);
  window.ServerManOperationBar?.adopt(result.value.operation_id);
  return true;
}

// Review one target's changes first, then apply the reviewed arguments.
async function reviewOrApplyTweakTarget(target) {
  const updates = changedUpdates(target);
  if (!Object.keys(updates).length || (updates.events && !Object.keys(updates.events).length)) return;
  if (targetReviewed(target)) {
    await applyTweakTarget(target);
    return renderTweaksActions();
  }
  const review = await reviewTweakTarget(target);
  if (!review) return;
  // A medical toggle that changes no group says so instead of counting changes.
  const unchanged = window.ServerManOperationMessages.medicalUnchanged;
  showTweakFeedback(review.message === unchanged ? unchanged
    : `${review.changed_fields.length} ${target.replaceAll("_", " ")} change(s) validated. Select Apply to publish one file.`);
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

// Turn on "Use gameplay configuration" of the server configuration from this page; the reload follows the apply.
async function enableGameplayFile() {
  if (tweaksState.enablingGameplay) return;
  const step = tweakStep();
  tweaksState.enablingGameplay = true; renderTweaks();
  const failed = (result, fallback) => {
    if (!tweakStepCurrent(step)) return;
    tweaksState.enablingGameplay = false; renderTweaks(); showTweakError(result, fallback);
  };
  const loaded = await window.pywebview.api.load_configuration(tweaksState.profileId, "server");
  if (!loaded.success) return failed(loaded, "The server configuration could not be read.");
  const server = loaded.value;
  const args = [server.profile_id, "server", server.profile_revision, server.settings_revision, server.digest,
    server.server_config_digest, { enableCfgGameplayFile: true }];
  const preview = await window.pywebview.api.preview_configuration(...args);
  if (!preview.success) return failed(preview, "The gameplay file could not be turned on.");
  const applied = await window.pywebview.api.apply_configuration(...args);
  if (!applied.success) return failed(applied, "The gameplay file could not be turned on.");
  if (!tweakStepCurrent(step)) return;
  // The page reloads when the apply succeeds, and then shows the gameplay settings
  tweaksState.pending.set(applied.value.operation_id, "server");
  window.ServerManOperationBar?.adopt(applied.value.operation_id);
  showTweakFeedback("Turning on the gameplay file.");
}

// Switch the tweaks area and rerender.
function chooseTweakArea(area) { tweaksState.area = area; renderTweaks(); }
// Switch the gameplay subtab and rerender.
function chooseGameplayTab(tab) { tweaksState.subtab = tab; renderTweaks(); }

// Render the tweaks workspace through the render module.
function renderTweaks() {
  window.ServerManTweaksRender.workspace({
    state: tweaksState, value: tweakValue, setValue: setTweakValue, setEventValue,
    setStarterItem, chooseArea: chooseTweakArea, chooseTab: chooseGameplayTab,
    enableGameplay: enableGameplayFile, setStarterMissionItems,
  });
  // Clear the busy state once the workspace is rendered.
  renderTweaksActions(); document.getElementById("content-region").setAttribute("aria-busy", "false");
}

// Register the tweaks workspace with the shared transition guard.
window.ServerManTransitions.registerOwner("tweaks", discardTweaks, focusTweaks);
