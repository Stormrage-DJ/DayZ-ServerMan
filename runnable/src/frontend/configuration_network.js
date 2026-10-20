// Presents the Network group and complete review; shared Configuration owns every edit and action.
"use strict";

// Arrange actual backend fields without introducing another draft or save owner.
function mountConfigurationNetwork(section) {
  const rows = section.querySelector(".guided-setting-list");
  rows.classList.add("configuration-network-ports");
  [...rows.children].forEach((row) => row.classList.add("configuration-network-port"));
}

// Show the reviewed profile and each labelled old/new value before the single Apply action.
function showConfigurationReview(result, updates) {
  const summary = configElement("div", "notice");
  summary.setAttribute("role", "status");
  const profile = window.ServerManProfileContext.profiles().find((item) => item.profile_id === result.profile_id);
  summary.append(configElement("p", "", `${Object.keys(updates).length} validated change(s) for ${profile?.display_name || "the selected profile"}.`));
  const table = configElement("table");
  const header = configElement("tr");
  ["Setting", "Current value", "New value"].forEach((label) => header.append(configElement("th", "", label)));
  const head = configElement("thead"); head.append(header); table.append(head);
  const body = configElement("tbody");
  for (const [key, value] of Object.entries(updates)) {
    const field = configurationState.values.get(key);
    const definition = window.ServerManConfigurationCatalog.serverGroups.flatMap((group) => group.fields)
      .find((item) => item.key === key);
    const change = result.changes?.find((item) => item.key === key);
    const display = (item) => field?.secret ? "Hidden" : item === null || item === undefined ? "Not set"
      : typeof item === "boolean" ? item ? "Enabled" : "Disabled" : String(item);
    const row = configElement("tr");
    [definition?.label || "Setting", display(change ? change.before : field?.value), display(change ? change.after : value)]
      .forEach((text) => row.append(configElement("td", "", text)));
    body.append(row);
  }
  table.append(body); summary.append(table,
    configElement("p", "", "Apply saves all reviewed changes. Ports take effect at the next server start."));
  document.getElementById("configuration-feedback").replaceChildren(summary);
}

window.ServerManConfigurationNetwork = Object.freeze({mount: mountConfigurationNetwork, review: showConfigurationReview});
