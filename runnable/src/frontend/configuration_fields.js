// Configuration field rendering: typed inputs, paired rows, and guided groups of the server configuration.
"use strict";

// Build the typed input control for one configuration field.
function configurationInput(field) {
  const input = field.kind === "boolean" ? document.createElement("select") : document.createElement("input");
  input.dataset.configurationField = field.key;
  input.id = `configuration-${field.key.replaceAll(".", "-")}`;
  if (field.kind === "boolean") {
    if (!field.present) {
      const unset = configElement("option", "", "Not set");
      unset.value = "";
      input.append(unset);
    }
    // Offer enabled and disabled states for a boolean setting.
    [["true", "Enabled"], ["false", "Disabled"]].forEach(([value, label]) => {
      const option = configElement("option", "", label);
      option.value = value;
      input.append(option);
    });
    input.value = field.present ? String(field.value) : "";
  } else {
    // Mirror the field kind through the input type and step rules.
    input.type = field.secret ? "password" : field.kind === "string" ? "text" : "number";
    if (field.kind === "number") input.step = "any";
    if (field.kind === "integer") input.step = "1";
    input.value = field.present ? String(field.value) : "";
  }
  // Recompute the unsaved state on every edit.
  input.addEventListener("input", updateUnsavedState);
  input.addEventListener("change", updateUnsavedState);
  return input;
}

// Group consecutive fields that form one paired row.
function configurationRows(fields) {
  const rows = [];
  fields.forEach((field) => {
    const last = rows.at(-1);
    if (field.pair && last?.pair === field.pair) last.fields.push(field);
    else rows.push({ pair: field.pair, fields: [field] });
  });
  return rows;
}

// Render one guided group of available configuration fields.
function renderConfigurationGroup(group, available) {
  const section = configElement("fieldset", "tweak-group configuration-group");
  section.append(configElement("legend", "", group.title));
  if (group.hint) section.append(configElement("p", "tweak-group-hint", group.hint));
  const rows = configElement("div", "guided-setting-list");
  // Keep only the fields the loaded target actually exposes.
  configurationRows(group.fields.filter((definition) => available.has(definition.key))).forEach((row) => {
    // Build one row with its labels and typed inputs.
    const item = configElement("div", "guided-setting-row");
    const controls = configElement("div", `guided-setting-controls${row.fields.length > 1 ? " is-paired" : ""}`);
    row.fields.forEach((definition) => {
      const field = available.get(definition.key);
      const label = configElement("label", "guided-setting-control");
      const caption = configElement("span", "tweak-field-label", definition.label);
      const input = configurationInput(field); caption.htmlFor = input.id;
      label.append(caption, input); controls.append(label);
    });
    item.append(controls, configElement("p", "guided-setting-hint", row.fields[0].hint));
    rows.append(item);
  });
  section.append(rows); return section;
}

// Replace the form with groups built from the loaded field set.
function renderConfigurationFields(loaded) {
  configurationState.loaded = loaded;
  configurationState.reviewed = null;
  // Rebuild the editable value map from the loaded target.
  configurationState.values = new Map(loaded.fields.map((field) => [field.key, field]));
  const form = document.getElementById("configuration-fields");
  form.replaceChildren();
  const available = new Map(loaded.fields.map((field) => [field.key, field]));
  // Render every catalog group that has available fields.
  window.ServerManConfigurationCatalog.serverGroups.forEach((group) => {
    form.append(renderConfigurationGroup(group, available));
  });
  // Show the target path and refresh the unsaved state for the new form.
  document.getElementById("configuration-path").textContent = loaded.relative_path;
  updateUnsavedState();
}
