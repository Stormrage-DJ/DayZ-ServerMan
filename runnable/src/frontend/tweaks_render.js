// Rendering helpers for the tweaks workspace UI.
"use strict";

// Build one element through the shared UI helper.
function tweakNode(tag, className = "", text = "") { return window.ServerManUi.element(tag, className, text); }
// Turn a configuration key into readable label text.
function tweakLabel(key) { return key.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " "); }

// Describe what a supply percentage does with the group's item counts of the loaded mission.
function supplyEffect(group, field, percent, api) {
  const facts = api.state.snapshots.get(group.target)?.facts;
  if (!facts) return "";
  if (field.key === "ammo_fill") return `${facts.fill_types} ammunition and magazine types in this mission.`;
  const counts = facts.groups?.[field.key];
  if (!counts) return "";
  if (!counts.types) return "No item type of this group spawns in this mission.";
  const target = Math.round(counts.nominal * percent / 100);
  return `${counts.types} item types · ${counts.nominal} on the map originally · about ${target} at ${percent}%`;
}

// Build a percentage slider with its shown value and, for supply groups, its effect.
function percentControl(wrapper, group, field, value, api) {
  const input = document.createElement("input");
  input.type = "range"; input.min = String(field.range.min); input.max = String(field.range.max);
  input.step = String(field.range.step); input.value = String(value ?? 100);
  input.setAttribute("aria-label", field.label);
  const shown = tweakNode("output", "tweak-percent-value");
  const effect = tweakNode("small", "tweak-percent-effect");
  // Show the slider position and its effect while it moves; the draft changes on release
  const update = () => {
    shown.textContent = `${input.value}%`; input.setAttribute("aria-valuetext", `${input.value}%`);
    effect.textContent = supplyEffect(group, field, Number(input.value), api);
  };
  update(); input.addEventListener("input", update);
  input.addEventListener("change", () => api.setValue(group.target, field.key, Number(input.value)));
  const row = tweakNode("div", "tweak-percent-row"); row.append(input, shown);
  wrapper.append(row);
  if (effect.textContent) wrapper.append(effect);
  return wrapper;
}

// Build the input control for one tweakable field.
function controlForField(group, field, api) {
  const wrapper = tweakNode("label", "guided-setting-control");
  // Show the field label above its control.
  wrapper.append(tweakNode("span", "tweak-field-label", field.label));
  const value = api.value(group.target, field.key);
  if (field.kind === "percent") return percentControl(wrapper, group, field, value, api);
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
      // A toggle that cannot use its stored original keeps the read state, locked, with the reason and way out (6.5).
      const refusal = window.ServerManOperationMessages.zoneBaseline(api.state.snapshots.get(field.key)?.zone_baseline);
      if (!refusal) return;
      control.disabled = true;
      const notice = tweakNode("p", "notice notice-warning", refusal);
      notice.setAttribute("role", "status"); section.append(notice);
    });
    return section;
  }
  // Starter loadouts render their own item picker.
  if (group.target === "starter_loadout") return renderStarter(section, api);
  section.append(renderFieldRows(group, api)); return section;
}

// Describe the mission's own starting setup and offer to keep it or turn it off.
function renderMissionStarter(mission, api) {
  const panel = tweakNode("div", "starter-mission");
  panel.append(tweakNode("strong", "", "The mission's own starting items"));
  const items = mission.items.map((item) => item.count > 1 ? `${item.name} \u00d7${item.count}` : item.name);
  if (!items.length && !mission.choices.length) {
    panel.append(tweakNode("p", "", "The mission gives no items of its own."));
  }
  if (items.length) panel.append(tweakNode("p", "", `Gives: ${items.join(", ")}.`));
  mission.choices.forEach((choice) => panel.append(tweakNode("p", "", `One of: ${choice.join(", ")}.`)));
  if (mission.random) panel.append(tweakNode("p", "", "Some items are chosen at random."));
  if (mission.effects.length) panel.append(tweakNode("p", "", `It also ${mission.effects.join(", ")}.`));
  const keep = tweakNode("label", "check-row"); const input = document.createElement("input");
  input.type = "checkbox"; input.id = "starter-mission-items";
  input.checked = api.state.drafts.get("starter_loadout").mission_items;
  input.addEventListener("change", () => api.setStarterMissionItems(input.checked));
  keep.append(input, tweakNode("span", "", "Give the mission's own starting items"));
  panel.append(keep);
  if (!input.checked) {
    panel.append(tweakNode("p", "status-label status-warning",
      "Off: a new character gets only the items chosen below. Everything listed above is off as well."));
  }
  return panel;
}

// Render the mission's own starting setup and the starter item picker.
function renderStarter(section, api) {
  const snapshot = api.state.snapshots.get("starter_loadout");
  if (snapshot?.mission_starter) {
    section.append(renderMissionStarter(snapshot.mission_starter, api),
      tweakNode("strong", "starter-own-title", "Your starting items, given in addition"));
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

// Mount the map editor action before the tabs or the loading message.
function renderMapEditorEntry(root) {
  const slot = tweakNode("div", "panel-heading"); slot.id = "map-editor-entry";
  root.prepend(slot); window.ServerManMapEditorEntry.render(slot);
}

// Offer the gameplay file switch on this page: Gameplay Tweaks only take effect with cfgGameplay.json in use.
function renderGameplaySwitch(api) {
  const panel = tweakNode("div", "notice notice-warning tweak-gameplay-switch"); panel.setAttribute("role", "status");
  panel.append(tweakNode("p", "", "The server does not use its gameplay file (cfgGameplay.json) yet, so these "
    + "settings cannot be changed. Turning it on makes the server read the gameplay settings of its mission."));
  const button = tweakNode("button", "button button-primary", api.state.enablingGameplay
    ? "Turning on the gameplay file\u2026" : "Turn on gameplay file");
  button.type = "button"; button.id = "tweaks-enable-gameplay"; button.disabled = api.state.enablingGameplay;
  window.ServerManBusy?.mark(button);
  button.addEventListener("click", () => api.enableGameplay());
  panel.append(button);
  return panel;
}

// Render the full tweaks workspace for the current selection.
function renderWorkspace(api) {
  const root = tweakNode("section", "panel tweaks-panel"); root.id = "tweaks-workspace";
  renderMapEditorEntry(root);
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
    if (api.state.gameplayOff) root.append(renderGameplaySwitch(api));
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
window.ServerManTweaksRender = Object.freeze({ workspace: renderWorkspace, entry: renderMapEditorEntry });
