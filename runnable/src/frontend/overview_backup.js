// Backup-after-stop preference control for the overview lifecycle actions.
"use strict";

// Report whether the profile preference enables a backup after stop.
function backupControlEnabled(profile) {
  return Boolean(
    profile && window.ServerManProfileContext.backupAfterStop(profile.profile_id),
  );
}

// Persist the backup-after-stop preference for one profile.
async function saveBackupControl(profile, checkbox) {
  const enabled = checkbox.checked;
  // Keep the control disabled while the preference is saved.
  checkbox.disabled = true;
  const result = await window.ServerManProfileContext.setBackupAfterStop(
    profile.profile_id, enabled,
  );
  // Roll the checkbox back and warn when the preference could not be saved.
  if (!result.success) {
    checkbox.checked = !enabled;
    window.ServerManUi.setHostStatus(
      "Backup-after-stop preference could not be saved", "is-warning", "preferences",
    );
  } else {
    window.ServerManUi.clearHostStatus("preferences");
  }
  // Restore the control state from the profile's runtime support.
  checkbox.disabled = !profile.runtime_profile;
}

// Build the backup-after-stop checkbox with its explanatory tooltip.
function createBackupControl(profile) {
  const label = window.ServerManUi.element("label", "check-row");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.id = "backup-after-stop";
  checkbox.checked = backupControlEnabled(profile);
  // Disable the control until the profile has a runtime directory.
  checkbox.disabled = !profile?.runtime_profile;
  // Explain what the preference does and what it needs.
  label.title = profile?.runtime_profile
    ? "Create a verified backup after a successful stop."
    : "Set a runtime profile directory before enabling automatic backups.";
  label.append(
    checkbox,
    window.ServerManUi.element("span", "", "Backup after stop"),
  );
  if (profile) checkbox.addEventListener("change", () => saveBackupControl(profile, checkbox));
  return label;
}

// Publish the overview backup control used by the lifecycle actions.
window.ServerManOverviewBackup = Object.freeze({
  choice: createBackupControl,
  enabled: backupControlEnabled,
});
