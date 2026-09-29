// Rendering helpers for the tweaks workspace UI.
"use strict";

// Build one element through the shared UI helper.
function tweakNode(tag, className = "", text = "") { return window.ServerManUi.element(tag, className, text); }
// Turn a configuration key into readable label text.
function tweakLabel(key) { return key.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " "); }

// Build the input control for one tweakable field.
function controlForField(group, field, api) {
  const wrapper = tweakNode("label", "guided-setting-control");
  // Show the field label above its control.
  wrapper.append(tweakNode("span", "tweak-field-label", field.label));
  const value = api.value(group.target, field.key);
  let input;
  // Boolean fields use an enabled or disabled choice.
  if (field.kind === "boolean") {
    input = document.createElement("select");
    [["true", "Enabled"], ["false", "Disabled"]].forEach(([raw, label]) => {
      const option = tweakNode("option", "", label); option.value = raw; input.append(option);
    });
    input.value = String(Boolean(value));
  } else {
    input = document.createElement("input");
    input.type = field.kind === "password" ? "password" : field.kind === "string" ? "text" : "number";
    input.step = field.kind === "integer" ? "1" : "any"; input.value = value ?? "";
  }
  // Store the converted value when the operator changes it.
  input.addEventListener("change", () => {
    let next = input.value;
    if (field.kind === "boolean") next = next === "true";
    else if (!["string", "password"].includes(field.kind)) next = Number(next);
    api.setValue(group.target, field.key, next);
  });
  wrapper.append(input);
  return wrapper;
}

// Group fields into rows so paired controls sit together.
function fieldRows(fields) {
  const rows = [];
  // Join fields that share a pair name into one row.
  fields.forEach((field) => {
    const previous = rows.at(-1);
    if (field.pair && previous?.pair === field.pair) previous.fields.push(field);
    else rows.push({ pair: field.pair, fields: [field] });
  });
  return rows;
}

// Render the field rows for one group.
function renderFieldRows(group, api) {
  const list = tweakNode("div", "guided-setting-list");
  // Build each row with its paired controls and shared hint.
  fieldRows(group.fields).forEach((row) => {
    const item = tweakNode("div", "guided-setting-row");
    const controls = tweakNode(
      "div", `guided-setting-controls${row.fields.length > 1 ? " is-paired" : ""}`,
    );
    row.fields.forEach((field) => controls.append(controlForField(group, field, api)));
    item.append(controls, tweakNode("p", "guided-setting-hint", row.fields[0].hint));
    list.append(item);
  });
  return list;
}

// Render one field group, including medical and starter special cases.
function renderGroup(group, api) {
  const section = tweakNode("fieldset", "tweak-group");
  section.append(tweakNode("legend", "", group.title));
  if (group.hint) section.append(tweakNode("p", "tweak-group-hint", group.hint));
  const targets = group.target === "medical" ? group.fields.map((field) => field.key) : [group.target];
  const failures = targets.map((target) => api.state.errors.get(target)).filter(Boolean);
  // Show a warning instead of controls when every target failed to load.
  if (failures.length === targets.length) {
    const notice = tweakNode("p", "notice notice-warning", failures[0]);
    notice.setAttribute("role", "status"); section.append(notice); return section;
  }
  // Medical features render one enable choice per feature.
  if (group.target === "medical") {
    group.fields.forEach((field) => {
      const row = tweakNode("div", "tweak-feature-row");
      const copy = tweakNode("div"); copy.append(tweakNode("strong", "", field.label), tweakNode("small", "", field.hint));
      // Name unavailable features with their load error.
      const unavailable = api.state.errors.get(field.key);
      if (unavailable) {
        row.append(copy, tweakNode("small", "status-label status-warning", "Unavailable"));
        row.title = unavailable; section.append(row); return;
      }
      const control = document.createElement("select");
      [["true", "Enabled"], ["false", "Disabled"]].forEach(([value, label]) => {
        const option = tweakNode("option", "", label); option.value = value; control.append(option);
      });
      control.value = String(Boolean(api.value(field.key, "enabled")));
      control.addEventListener("change", () => api.setValue(field.key, "enabled", control.value === "true"));
      row.append(copy, control); section.append(row);
    });
    return section;
  }
  // Starter loadouts render their own item picker.
  if (group.target === "starter_loadout") return renderStarter(section, api);
  section.append(renderFieldRows(group, api)); return section;
}

// Render the starter loadout picker or its conversion prompt.
function renderStarter(section, api) {
  const snapshot = api.state.snapshots.get("starter_loadout");
  // Offer conversion first when the block predates ownership comments.
  if (snapshot?.conversion_required) {
    const preview = snapshot.conversion;
    const notice = tweakNode("div", "notice notice-warning starter-conversion");
    notice.append(
      tweakNode("strong", "", "Legacy starter loadout detected"),
      tweakNode("p", "", `Found ${preview.items.length} supported item(s) on lines ${preview.start_line}-${preview.end_line}.`),
      tweakNode("p", "", "Convert this block before editing it here. Conversion adds ownership comments without changing the current statements or item order."),
    );
    const items = tweakNode("p", "starter-conversion-items", preview.items.join(", "));
    const action = tweakNode("button", "button button-primary", "Review conversion");
    action.type = "button";
    action.disabled = [...api.state.pending.values()].includes("starter_loadout");
    action.addEventListener("click", api.reviewStarterConversion);
    notice.append(items, action); section.append(notice); return section;
  }
  const selected = new Set(api.state.drafts.get("starter_loadout").items);
  // List starter items by category as checkboxes.
  Object.entries(window.ServerManTweaksCatalog.starterGroups).forEach(([title, items]) => {
    const category = tweakNode("div", "starter-category"); category.append(tweakNode("strong", "", title));
    const choices = tweakNode("div", "starter-grid");
    items.forEach((item) => {
      const label = tweakNode("label", "check-row"); const input = document.createElement("input");
      input.type = "checkbox"; input.checked = selected.has(item);
      input.addEventListener("change", () => api.setStarterItem(item, input.checked));
      label.append(input, tweakNode("span", "", item)); choices.append(label);
    });
    category.append(choices); section.append(category);
  });
  return section;
}

// Render one event or population table.
function renderEventTable(group, fields, api) {
  const section = tweakNode("section", "tweak-group event-group"); section.append(tweakNode("h3", "", group.title));
  // Show a warning when events could not be loaded.
  if (api.state.errors.has("events")) {
    section.append(tweakNode("p", "notice notice-warning", api.state.errors.get("events")));
    return section;
  }
  const table = tweakNode("div", "compact-table");
  table.style.setProperty("--table-columns", String(fields.length));
  // Label the table columns with readable field names.
  const header = tweakNode("div", "compact-row compact-header"); header.append(tweakNode("span", "", "Event"));
  fields.forEach((name) => header.append(tweakNode("span", "", tweakLabel(name)))); table.append(header);
  // Render one numeric input per field for each named entry.
  group.names.forEach((name) => {
    const values = api.state.drafts.get("events").events[name]; if (!values) return;
    const row = tweakNode("div", "compact-row");
    row.append(tweakNode("strong", "", name.replace(/^Static|^Vehicle|^Animal|^Ambient/, "")));
    fields.forEach((key) => {
      const input = document.createElement("input"); input.type = "number"; input.step = "1";
      input.setAttribute("aria-label", `${name} ${tweakLabel(key)}`); input.value = values[key];
      input.addEventListener("change", () => api.setEventValue(name, key, Number(input.value))); row.append(input);
    }); table.append(row);
  });
  section.append(table); return section;
}

// Build one tab button with keyboard navigation.
function tabButton(label, active, action) {
  const button = tweakNode("button", `tab-button${active ? " is-active" : ""}`, label);
  button.type = "button"; button.setAttribute("role", "tab"); button.setAttribute("aria-selected", String(active));
  button.tabIndex = active ? 0 : -1;
  // Move focus and activation with the arrow, Home, and End keys.
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...button.parentElement.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(button); let next = index;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else next = (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault(); tabs[next].focus(); tabs[next].click();
  });
  button.addEventListener("click", action); return button;
}

// Render the full tweaks workspace for the current selection.
function renderWorkspace(api) {
  const root = tweakNode("section", "panel tweaks-panel"); root.id = "tweaks-workspace";
  // Build the primary area tabs.
  const top = tweakNode("div", "tab-list primary-tabs"); top.setAttribute("role", "tablist");
  [["gameplay", "Gameplay Tweaks"], ["events", "In-Game Events"], ["population", "Vehicles & Animals"]]
    .forEach(([id, label]) => top.append(tabButton(label, api.state.area === id, () => api.chooseArea(id))));
  root.append(top);
  if (api.state.area === "gameplay") {
    // Gameplay shows subtabs and the selected group set.
    const tabs = tweakNode("div", "tab-list secondary-tabs"); tabs.setAttribute("role", "tablist");
    window.ServerManTweaksCatalog.gameplayTabs.forEach((tab) => tabs.append(
      tabButton(tab.label, api.state.subtab === tab.id, () => api.chooseTab(tab.id)),
    )); root.append(tabs);
    const selected = window.ServerManTweaksCatalog.gameplayTabs.find((tab) => tab.id === api.state.subtab);
    const groups = tweakNode("div", "tweak-groups"); selected.groups.forEach((group) => groups.append(renderGroup(group, api))); root.append(groups);
  } else {
    // Events and population share the compact table layout.
    const eventFields = api.state.area === "events"
      ? ["active", "nominal", "lifetime", "restock", "saferadius", "distanceradius", "cleanupradius"]
      : ["active", "nominal", "min", "max"];
    const groups = api.state.area === "events" ? window.ServerManTweaksCatalog.eventGroups : window.ServerManTweaksCatalog.populationGroups;
    groups.forEach((group) => root.append(renderEventTable(group, eventFields, api)));
  }
  const feedback = tweakNode("div", "configuration-feedback"); feedback.id = "tweaks-feedback";
  const actions = tweakNode("div", "action-row tweaks-actions"); actions.id = "tweaks-actions";
  // Append the feedback and action regions and mount the workspace.
  root.append(feedback, actions); document.getElementById("content-region").replaceChildren(root);
}

// Publish the tweaks render module.
window.ServerManTweaksRender = Object.freeze({ workspace: renderWorkspace });
