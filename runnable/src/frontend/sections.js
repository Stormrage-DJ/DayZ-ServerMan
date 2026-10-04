// Section registry: one registration per workspace section drives its heading, opening, profile changes, and operation events.
"use strict";

// Registered sections by identifier, in registration order; registration order is navigation order.
const sectionRegistry = new Map();
// Navigation groups in display order; each section names its group.
const sectionGroups = Object.freeze([
  Object.freeze({ id: "operate", label: "Operate" }),
  Object.freeze({ id: "setup", label: "Server setup" }),
  Object.freeze({ id: "application", label: "Application" }),
]);

// Register one workspace section; a second registration of the same identifier is refused.
function registerSection(id, definition) {
  if (sectionRegistry.has(id)) throw new Error(`Section "${id}" is already registered.`);
  if (typeof definition.open !== "function") throw new Error(`Section "${id}" needs an open handler.`);
  // Unknown fields pass through unchanged; group and icon place the section in the navigation,
  // usesProfile shows the server context line under the page title.
  sectionRegistry.set(id, Object.freeze({
    needsSnapshot: false, reopensOnProfileChange: false, keepsUnsavedEdits: false, usesProfile: false,
    operationFinished: null, backgroundOperationFinished: null, trackOperation: null,
    ...definition, id,
  }));
}

// Return one registered section, or undefined when the identifier is unknown.
function getSection(id) {
  return sectionRegistry.get(id);
}

// Open a section; only a section that declares the need receives the loaded snapshot.
function openSection(id, snapshot = null) {
  const section = sectionRegistry.get(id);
  if (section) section.open(section.needsSnapshot ? snapshot : null);
}

// Reopen the visible section after a profile change when it follows the profile selection.
function sectionProfileChanged(id) {
  const section = sectionRegistry.get(id);
  if (section?.reopensOnProfileChange) section.open(null);
}

// Offer an operation to every background handler, then to the visible section; report whether one consumed it.
function sectionOperationFinished(id, operation) {
  let handled = false;
  // Background handlers run from any section, in registration order.
  sectionRegistry.forEach((section) => {
    if (section.backgroundOperationFinished?.(operation)) handled = true;
  });
  const visible = sectionRegistry.get(id);
  if (visible?.operationFinished?.(operation)) handled = true;
  return handled;
}

// Overview: reads the loaded snapshot and reloads when a tracked lifecycle operation ends.
registerSection("overview", {
  title: "Overview",
  description: "Server state, updates, backups and the daily schedule.",
  group: "operate", icon: "M4 4h7v9H4z M13 4h7v5h-7z M13 11h7v9h-7z M4 15h7v5H4z",
  needsSnapshot: true, reopensOnProfileChange: true, usesProfile: true,
  open: (snapshot) => window.ServerManOverview.open(snapshot),
  operationFinished: (operation) => window.ServerManOverview.operationFinished(operation),
  trackOperation: (operation) => window.ServerManOverview.track(operation.operation_id),
});
// Mods.
registerSection("mods", {
  title: "Mods",
  description: "Download, update, and apply mods for the selected server.",
  group: "operate", icon: "M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9",
  reopensOnProfileChange: true, usesProfile: true,
  open: () => window.ServerManMods.open(),
  operationFinished: (operation) => window.ServerManMods.operationFinished(operation),
});
// Backups: the restore review is asked before the backup creation; the ZIP restore reports from any section.
registerSection("backups", {
  title: "Backups",
  description: "Create and restore verified server backups.",
  group: "operate", icon: "M3 5h18v4H3z M5 9v10h14V9 M10 13h4",
  reopensOnProfileChange: true, usesProfile: true,
  open: () => window.ServerManBackups.open(),
  operationFinished: (operation) => window.ServerManRestore.operationFinished(operation)
    || window.ServerManBackups.operationFinished(operation),
  backgroundOperationFinished: (operation) => window.ServerManProfileRestore?.operationFinished(operation),
});
// Logs: independent of the profile selection and of operations.
registerSection("logs", {
  title: "Logs",
  description: "Inspect manager activity and captured DayZ server output.",
  group: "operate", icon: "M6 3h9l4 4v14H6z M15 3v4h4 M9 12h7 M9 16h7",
  open: () => window.ServerManLogs.open(),
});
// Profiles: opens on the shared profile selection and keeps unsaved edits across a reload.
registerSection("profiles", {
  title: "Profiles",
  description: "Create and maintain complete DayZ server launch profiles.",
  group: "setup", icon: "M4 5h16v6H4z M4 13h16v6H4z M7.5 8h.01 M7.5 16h.01",
  reopensOnProfileChange: true, keepsUnsavedEdits: true, usesProfile: true,
  open: () => window.ServerManProfiles.open(window.ServerManProfileContext.selectedId()),
  operationFinished: (operation) => window.ServerManProfiles.operationFinished(operation),
});
// Configuration: keeps unsaved edits across a reload.
registerSection("configuration", {
  title: "Configuration",
  description: "Edit the selected server's core configuration with guided controls.",
  group: "setup",
  icon: "M4 7h9 M17 7h3 M13 7a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M4 17h3 M11 17h9 M7 17a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  reopensOnProfileChange: true, keepsUnsavedEdits: true, usesProfile: true,
  open: () => window.ServerManConfiguration.open(),
  operationFinished: (operation) => window.ServerManConfiguration.operationFinished(operation),
});
// Tweaks.
registerSection("tweaks", {
  title: "Tweaks",
  description: "Fine-tune the selected server with compact, map-aware controls.",
  group: "setup", icon: "M4 17a8 8 0 0 1 16 0z M12 17l3.5-5",
  reopensOnProfileChange: true, usesProfile: true,
  open: () => window.ServerManTweaks.open(),
  operationFinished: (operation) => window.ServerManTweaks.operationFinished(operation),
});
// Settings: reads the loaded snapshot; the legacy import and the locations form both see every event.
registerSection("settings", {
  title: "Settings",
  description: "Application folders and update checks.",
  group: "application",
  icon: "M12 5a7 7 0 1 0 0 14a7 7 0 1 0 0-14z M12 9.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5z M12 2.5V5 M12 19v2.5 "
    + "M2.5 12H5 M19 12h2.5 M5.3 5.3L7 7 M17 17l1.7 1.7 M5.3 18.7L7 17 M17 7l1.7-1.7",
  needsSnapshot: true, keepsUnsavedEdits: true,
  open: (snapshot) => window.ServerManSettings.open(snapshot),
  operationFinished: (operation) => {
    const migrationHandled = window.ServerManMigration.operationFinished(operation);
    const settingsHandled = window.ServerManSettings.operationFinished(operation);
    return Boolean(migrationHandled || settingsHandled);
  },
});

// Publish the registry used by the shell loop and the shared shell helpers.
window.ServerManSections = Object.freeze({
  register: registerSection,
  get: getSection,
  ids: () => [...sectionRegistry.keys()],
  groups: sectionGroups,
  open: openSection,
  profileChanged: sectionProfileChanged,
  operationFinished: sectionOperationFinished,
});
