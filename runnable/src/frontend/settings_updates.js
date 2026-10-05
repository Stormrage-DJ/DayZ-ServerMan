// Settings "Update checks" panel: the switch for the automatic Steam checks; it saves at once.
"use strict";

// A save in flight: the value it stores, so a redraw during the save shows that value and stays locked.
const settingsUpdatesState = { saving: false, pending: true };

// What the switch does and what it sends, shown under its label.
const SETTINGS_UPDATES_HELP = "When this is on, DayZ-ServerMan asks Steam whether your mods have a newer version: "
  + "at start, when you select another server, and every 30 minutes. Only Workshop item numbers are sent. "
  + "It also asks which DayZ server build Steam offers, at start and every 6 hours: SteamCMD signs in anonymously "
  + "and reads the public app information. Your Steam account is not used, and no file of the DayZ server is changed; "
  + "SteamCMD updates files in its own folder only. “Check now” on Overview checks both; on Mods it checks the mods.";

// Write the result line of the switch; the line is a polite status.
function settingsUpdatesFeedback(text) {
  const line = document.getElementById("automatic-update-checks-feedback");
  if (line && line.textContent !== text) line.textContent = text;
}

// Show a value on the switch that is in the page now; a redraw may have replaced the one that was pressed.
function showAutomaticUpdateChecks(enabled, locked) {
  const box = document.getElementById("automatic-update-checks");
  if (!box) return;
  box.checked = enabled;
  box.disabled = locked;
}

// Save the switch at once; a failed save puts the stored value back.
async function saveAutomaticUpdateChecks(event) {
  const enabled = event.currentTarget.checked;
  // Keep the switch locked while the value is stored, also on a switch that a redraw builds meanwhile.
  settingsUpdatesState.saving = true; settingsUpdatesState.pending = enabled;
  showAutomaticUpdateChecks(enabled, true);
  let result = null;
  try { result = await window.pywebview.api.save_automatic_update_checks(enabled); } catch (_error) { result = null; }
  settingsUpdatesState.saving = false;
  if (!result || !result.success) {
    showAutomaticUpdateChecks(window.ServerManUpdateStatus.automaticChecks(), false);
    settingsUpdatesFeedback("The setting could not be saved.");
    return;
  }
  showAutomaticUpdateChecks(result.value?.automatic_update_checks !== false, false);
  settingsUpdatesFeedback(`Saved. Automatic checks are ${enabled ? "on" : "off"}.`);
  // The update state takes the new value and is read again, so the badge and the Mods wording follow.
  window.ServerManUpdateStatus.setAutomaticChecks(enabled);
  void window.ServerManUpdateStatus.recheck();
}

// Build the panel from the value that the update state holds; the switch is not part of "Save locations".
function renderSettingsUpdates() {
  const node = window.ServerManUi.element;
  const panel = node("section", "panel settings-updates");
  panel.append(node("h2", "", "Update checks"));
  const label = node("label", "check-row settings-switch");
  const box = document.createElement("input");
  box.type = "checkbox"; box.id = "automatic-update-checks"; box.setAttribute("role", "switch");
  // While a save runs, the switch shows the value being stored and stays locked.
  box.checked = settingsUpdatesState.saving ? settingsUpdatesState.pending : window.ServerManUpdateStatus.automaticChecks();
  box.disabled = settingsUpdatesState.saving;
  box.setAttribute("aria-describedby", "automatic-update-checks-help");
  box.addEventListener("change", saveAutomaticUpdateChecks);
  label.append(box, node("span", "", "Check Steam for mod and server updates automatically"));
  const help = node("p", "settings-updates-help", SETTINGS_UPDATES_HELP); help.id = "automatic-update-checks-help";
  const feedback = node("p", "settings-updates-feedback"); feedback.id = "automatic-update-checks-feedback";
  feedback.setAttribute("role", "status");
  panel.append(label, help, feedback);
  return panel;
}

// Read the stored value when Settings opens, so the switch is true also before the first status read.
async function loadSettingsUpdates() {
  let result = null;
  try { result = await window.pywebview.api.get_ui_preferences(); } catch (_error) { result = null; }
  if (!result?.success) return;
  const enabled = result.value.automatic_update_checks !== false;
  window.ServerManUpdateStatus.setAutomaticChecks(enabled);
  const box = document.getElementById("automatic-update-checks");
  if (box && !box.disabled) box.checked = enabled;
}

// Publish the panel for the Settings page.
window.ServerManSettingsUpdates = Object.freeze({
  render: renderSettingsUpdates,
  load: loadSettingsUpdates,
});
