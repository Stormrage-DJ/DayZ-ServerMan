// Shared profile selection state for every workspace selector.
"use strict";

// Known profiles, the remembered selection, and per-profile safety flags.
const profileContextState = {
  profiles: [], selectedId: null, initialized: false, generation: 0,
  backupAfterStopProfiles: new Set(),
};
// Element identifiers of every profile selector in the shell.
const profileSelectorIds = [
  "global-profile", "overview-profile", "configuration-profile",
  "backup-profile", "profile-workspace-selector",
];

// Align every visible selector with the remembered profile when it is offered.
function syncVisibleProfileSelectors() {
  profileSelectorIds.forEach((id) => {
    const select = document.getElementById(id);
    if (!select) return;
    // Only apply the remembered profile where it is still offered.
    const available = [...select.options].some(
      (option) => option.value === profileContextState.selectedId,
    );
    if (available) select.value = profileContextState.selectedId;
  });
}

// Rebuild the global selector from the known profiles and the current choice.
function renderGlobalProfileSelector() {
  const select = document.getElementById("global-profile");
  select.textContent = "";
  // Show a placeholder entry when no profiles exist.
  if (!profileContextState.profiles.length) {
    const option = document.createElement("option");
    option.textContent = "No profiles"; option.value = ""; select.append(option);
  }
  // List every known profile and mark the remembered one.
  profileContextState.profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.textContent = profile.display_name; option.value = profile.profile_id;
    option.selected = profile.profile_id === profileContextState.selectedId; select.append(option);
  });
  select.disabled = !profileContextState.profiles.length;
  // Keep the remaining selectors in step with the same choice.
  syncVisibleProfileSelectors();
}

// Broadcast a selection change with a fresh generation for stale-work detection.
function announceProfileSelection() {
  // Advance the generation so stale listeners can be ignored.
  profileContextState.generation += 1;
  document.dispatchEvent(new CustomEvent("serverman:profile-change", {
    detail: { profileId: profileContextState.selectedId, generation: profileContextState.generation },
  }));
}

// Remember the chosen profile and persist it through the host service.
async function commitProfileSelection(profileId) {
  // Ignore identifiers that are not in the known profile list.
  if (!profileContextState.profiles.some((profile) => profile.profile_id === profileId)) return;
  profileContextState.selectedId = profileId; renderGlobalProfileSelector(); announceProfileSelection();
  // Ask the host to persist the choice for the next session.
  const result = await window.pywebview.api.save_selected_profile(profileId);
  if (!result.success) {
    window.ServerManUi.setHostStatus("Profile selection could not be remembered", "is-warning", "preferences");
  } else {
    window.ServerManUi.clearHostStatus("preferences");
  }
}

// Switch profiles through the transition guard so unsaved edits are handled.
function selectGlobalProfile(profileId) {
  // Do nothing when the requested profile is already selected.
  if (profileId === profileContextState.selectedId) return;
  syncVisibleProfileSelectors();
  window.ServerManTransitions.requestTransition(
    "Discard unsaved changes to switch server profiles.",
    () => { void commitProfileSelection(profileId); },
  );
}

// Load profiles and remembered preferences once, unless a reload is forced.
async function initializeProfileContext(force = false) {
  // Reuse the loaded state unless the caller demands a refresh.
  if (profileContextState.initialized && !force) return { success: true };
  // Fetch the profile list and stored preferences together.
  const [profiles, preferences] = await Promise.all([
    window.pywebview.api.list_profiles(), window.pywebview.api.get_ui_preferences(),
  ]);
  if (!profiles.success) return profiles;
  // Adopt the returned profiles and backup-after-stop flags.
  profileContextState.profiles = profiles.value;
  profileContextState.backupAfterStopProfiles = new Set(
    preferences.success && Array.isArray(preferences.value.backup_after_stop_profiles)
      ? preferences.value.backup_after_stop_profiles : [],
  );
  // Prefer the remembered choice, falling back to the first profile.
  const remembered = profileContextState.selectedId
    || (preferences.success ? preferences.value.selected_profile_id : null);
  profileContextState.selectedId = profiles.value.some((item) => item.profile_id === remembered)
    ? remembered : profiles.value[0]?.profile_id || null;
  // Mark the context ready and publish it to the selectors.
  profileContextState.initialized = true; renderGlobalProfileSelector(); return { success: true };
}

// Persist the backup-after-stop preference for one known profile.
async function setBackupAfterStop(profileId, enabled) {
  // Report an error for profiles outside the known list.
  if (!profileContextState.profiles.some((profile) => profile.profile_id === profileId)) {
    return { success: false, error: { message: "The selected profile is unavailable." } };
  }
  const result = await window.pywebview.api.save_backup_after_stop(profileId, enabled);
  // Track the accepted change in the local flag set.
  if (result.success) {
    if (enabled) profileContextState.backupAfterStopProfiles.add(profileId);
    else profileContextState.backupAfterStopProfiles.delete(profileId);
  }
  return result;
}

// Route operator changes on the global selector into the shared flow.
document.getElementById("global-profile").addEventListener(
  "change", (event) => selectGlobalProfile(event.target.value),
);

// Publish the shared profile context used by every workspace.
window.ServerManProfileContext = Object.freeze({
  initialize: initializeProfileContext,
  selectedId: () => profileContextState.selectedId,
  profiles: () => [...profileContextState.profiles],
  select: selectGlobalProfile,
  backupAfterStop: (profileId) => profileContextState.backupAfterStopProfiles.has(profileId),
  setBackupAfterStop,
});
