// Profile mod-copy controls for reusing an ordered list from another profile.
"use strict";

// Report whether a source mod conflicts with an entry already in the draft.
function profileModAlreadyListed(mod, existing) {
  const directory = String(mod.directory || "").trim().toLowerCase();
  const workshopId = String(mod.source?.workshop_id || "").trim();
  return existing.some((item) => {
    const sameDirectory = String(item.directory || "").trim().toLowerCase() === directory;
    const sameWorkshop = workshopId
      && String(item.source?.workshop_id || "").trim() === workshopId;
    return sameDirectory || sameWorkshop;
  });
}

// Apply one saved profile's mods to the current draft without persisting it.
function copyProfileMods(source, mode, modRows) {
  const existing = [...modRows.querySelectorAll("[data-mod-row]")].map(modValue);
  const copied = mode === "replace"
    ? source.mods
    : source.mods.filter((mod) => !profileModAlreadyListed(mod, existing));
  // Replace the draft only when the operator selected the destructive mode.
  if (mode === "replace") modRows.replaceChildren();
  copied.forEach((mod) => modRows.append(renderMod(mod)));
  refreshModRows(modRows); setProfileDirty();
  // Explain the unsaved result in the profile's existing feedback region.
  const feedback = document.getElementById("profile-feedback");
  if (feedback) {
    feedback.textContent = copied.length
      ? `Copied ${copied.length} mod${copied.length === 1 ? "" : "s"} from ${source.display_name}. Save the profile to keep this change.`
      : `${source.display_name} has no additional mods to copy.`;
  }
}

// Build the compact source and copy-mode chooser below the mod toolbar.
function renderProfileModCopyPanel(modRows, copyButton) {
  const panel = profileNode("div", "profile-mod-copy"); panel.hidden = true;
  const sources = profileState.records.filter(
    (item) => item.profile_id !== profileState.selected?.profile_id,
  );
  const sourceLabel = profileNode("label", "profile-mod-copy-field", "Copy from profile");
  const sourceSelect = document.createElement("select"); sourceSelect.setAttribute("aria-label", "Copy mods from profile");
  sources.forEach((profile) => {
    const option = profileNode("option", "", `${profile.display_name} (${profile.mods.length} mods)`);
    option.value = profile.profile_id; sourceSelect.append(option);
  });
  sourceLabel.append(sourceSelect);
  const modeLabel = profileNode("label", "profile-mod-copy-field", "Copy method");
  const modeSelect = document.createElement("select"); modeSelect.setAttribute("aria-label", "Mod copy method");
  [["merge", "Add missing mods"], ["replace", "Replace current list"]].forEach(([value, text]) => {
    const option = profileNode("option", "", text); option.value = value; modeSelect.append(option);
  });
  modeLabel.append(modeSelect);
  // Keep cancellation harmless and require an explicit copy action.
  const actions = profileNode("div", "profile-mod-copy-actions");
  const cancel = profileNode("button", "button", "Cancel"); cancel.type = "button";
  cancel.addEventListener("click", () => { panel.hidden = true; copyButton.focus(); });
  const confirm = profileNode("button", "button button-primary", "Copy mods"); confirm.type = "button";
  confirm.addEventListener("click", () => {
    const source = sources.find((item) => item.profile_id === sourceSelect.value);
    if (!source) return;
    copyProfileMods(source, modeSelect.value, modRows); panel.hidden = true; copyButton.focus();
  });
  actions.append(cancel, confirm); panel.append(sourceLabel, modeLabel, actions); return panel;
}

// Build the Add mod and Copy mods controls as one cohesive editor tool area.
function renderProfileModTools(modRows) {
  const tools = profileNode("div", "profile-mod-tools");
  const toolbar = profileNode("div", "profile-mod-toolbar");
  const sourcesAvailable = profileState.records.some(
    (item) => item.profile_id !== profileState.selected?.profile_id,
  );
  const copy = profileNode("button", "button", "Copy mods"); copy.type = "button";
  copy.disabled = !sourcesAvailable;
  copy.title = sourcesAvailable ? "Copy mods from another profile" : "No other profile is available";
  const panel = renderProfileModCopyPanel(modRows, copy);
  copy.addEventListener("click", () => { panel.hidden = !panel.hidden; if (!panel.hidden) panel.querySelector("select").focus(); });
  const add = profileNode("button", "button", "Add mod"); add.type = "button";
  add.addEventListener("click", () => {
    const row = renderMod(); modRows.append(row); refreshModRows(modRows);
    setProfileDirty(); row.querySelector("[data-mod-directory]").focus();
  });
  toolbar.append(copy, add); tools.append(toolbar, panel); return tools;
}
