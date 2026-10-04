// Section registry: one registration per workspace section drives its heading, opening, profile changes, and operation events.
"use strict";

// Registered sections by identifier, in registration order.
const sectionRegistry = new Map();

// Register one workspace section; a second registration of the same identifier is refused.
function registerSection(id, definition) {
  if (sectionRegistry.has(id)) throw new Error(`Section "${id}" is already registered.`);
  if (typeof definition.open !== "function") throw new Error(`Section "${id}" needs an open handler.`);
  // Unknown fields pass through unchanged, so later fields (navigation group, icon) need no registry change.
  sectionRegistry.set(id, Object.freeze({
    needsSnapshot: false, reopensOnProfileChange: false, keepsUnsavedEdits: false,
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
  description: "Server state, lifecycle controls, and the next safe operator action.",
  needsSnapshot: true, reopensOnProfileChange: true,
  open: (snapshot) => window.ServerManOverview.open(snapshot),
  operationFinished: (operation) => window.ServerManOverview.operationFinished(operation),
  trackOperation: (operation) => window.ServerManOverview.track(operation.operation_id),
});
// Profiles: opens on the shared profile selection and keeps unsaved edits across a reload.
registerSection("profiles", {
  title: "Profiles",
  description: "Create and maintain complete DayZ server launch profiles.",
  reopensOnProfileChange: true, keepsUnsavedEdits: true,
  open: () => window.ServerManProfiles.open(window.ServerManProfileContext.selectedId()),
  operationFinished: (operation) => window.ServerManProfiles.operationFinished(operation),
});
// Configuration: keeps unsaved edits across a reload.
registerSection("configuration", {
  title: "Configuration",
  description: "Edit the selected server's core configuration with guided controls.",
  reopensOnProfileChange: true, keepsUnsavedEdits: true,
  open: () => window.ServerManConfiguration.open(),
  operationFinished: (operation) => window.ServerManConfiguration.operationFinished(operation),
});
// Tweaks.
registerSection("tweaks", {
  title: "Tweaks",
  description: "Fine-tune the selected server with compact, map-aware controls.",
  reopensOnProfileChange: true,
  open: () => window.ServerManTweaks.open(),
  operationFinished: (operation) => window.ServerManTweaks.operationFinished(operation),
});
// Mods.
registerSection("mods", {
  title: "Mods",
  description: "Download, update, and apply mods for the selected server.",
  reopensOnProfileChange: true,
  open: () => window.ServerManMods.open(),
  operationFinished: (operation) => window.ServerManMods.operationFinished(operation),
});
// Backups: the restore review is asked before the backup creation; the ZIP restore reports from any section.
registerSection("backups", {
  title: "Backups",
  description: "Create and restore verified server backups.",
  reopensOnProfileChange: true,
  open: () => window.ServerManBackups.open(),
  operationFinished: (operation) => window.ServerManRestore.operationFinished(operation)
    || window.ServerManBackups.operationFinished(operation),
  backgroundOperationFinished: (operation) => window.ServerManProfileRestore?.operationFinished(operation),
});
// Logs: independent of the profile selection and of operations.
registerSection("logs", {
  title: "Logs",
  description: "Inspect manager activity and captured DayZ server output.",
  open: () => window.ServerManLogs.open(),
});
// Settings: reads the loaded snapshot; the legacy import and the locations form both see every event.
registerSection("settings", {
  title: "Settings",
  description: "Configure portable application paths and SteamCMD authentication.",
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
  open: openSection,
  profileChanged: sectionProfileChanged,
  operationFinished: sectionOperationFinished,
});
