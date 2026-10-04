// Host sentences in operator wording. A sentence that the host wrote is shown.
// An internal identifier inside it is translated in place: a field key, a path role, or an enum value.
// The reason of a recovery block is worded here too. Raw keys, roles, and values are never printed.
"use strict";

// Label of each profile and Steam field that a host sentence may name by its key, as its form shows it.
const hostFieldLabels = Object.freeze({
  display_name: "Display name", profile_id: "Profile ID", game_port: "Game port",
  server_executable: "Server executable", server_config: "Server config", mission_root: "Mission root",
  runtime_profile: "DayZ-relative runtime directory", extra_arguments: "Extra direct-process arguments",
  launch_scope: "Launch scope", instance_id: "Instance ID",
  steam_account_name: "Steam account name", account_name: "Steam account name",
  steam_authentication_mode: "Sign-in", authentication_mode: "Sign-in",
});
// Word of each enum value that a host sentence may name, as its selector shows it.
const hostEnumWords = Object.freeze({ ACCOUNT: "Steam account", ANONYMOUS: "Anonymous" });
// Reason sentence of an unfinished legacy import; the host words this block in two ways.
const HOST_IMPORT_BLOCK = "A legacy import was interrupted and could not be undone safely. "
  + "Restart DayZ-ServerMan; it checks the unfinished import again when it starts.";
// Known reasons of a recovery block, by a fragment of the host reason; the first match wins.
const hostBlockReasons = Object.freeze([
  ["Direct profile restore", "A profile restore from a backup archive did not finish. Stop the DayZ server, then restart DayZ-ServerMan; it checks the unfinished restore when it starts."],
  ["restore recovery", "A backup restore did not finish cleanly. Open Backups; DayZ-ServerMan checks the unfinished restore again there."],
  ["mod publication recovery", "Applying mods to the server folder was interrupted and could not be undone safely. Restart DayZ-ServerMan; it checks the server folder again when it starts."],
  ["unresolved mod publication", "Applying mods to the server folder was interrupted, and it cannot be checked because no DayZ server folder is set."],
  ["interrupted mod publication", "Applying mods to the server folder was interrupted and must be finished. This is possible only while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. Stop the server, then restart DayZ-ServerMan."],
  ["interrupted SteamCMD update", "A mod update was interrupted, so its result is not known. Restart DayZ-ServerMan, then update the mods again."],
  ["process-tree exit", "SteamCMD did not close cleanly, so the mod update cannot be confirmed. Close SteamCMD, restart DayZ-ServerMan, then update the mods again."],
  ["Profile provisioning recovery", "Creating a profile was interrupted and could not be undone safely. Restart DayZ-ServerMan; it checks the unfinished profile again when it starts."],
  ["migration recovery", HOST_IMPORT_BLOCK],
  ["Migration publication", HOST_IMPORT_BLOCK],
]);
// Reason shown when a block reason is neither known nor a plain sentence.
const HOST_BLOCK_FALLBACK = "An earlier operation did not finish cleanly.";
// Identifier table and its search pattern, built once from the field catalogues on first use.
const hostIdentifierCache = { table: null, pattern: null };

// Put a field label between quotation marks, as a name that the operator finds on the page.
function quotedHostLabel(label) {
  return `“${label}”`;
}

// Collect every known identifier with its replacement: field keys, tweak keys, path roles, and enum values.
function hostIdentifierTable() {
  if (hostIdentifierCache.table) return hostIdentifierCache.table;
  const table = new Map();
  // Configuration and tweak fields are named by the label of their control.
  (window.ServerManConfigurationCatalog?.serverGroups || []).forEach((group) => group.fields
    .forEach((field) => table.set(field.key, quotedHostLabel(field.label))));
  (window.ServerManTweaksCatalog?.gameplayTabs || []).forEach((tab) => tab.groups
    .forEach((group) => group.fields.forEach((field) => table.set(field.key, quotedHostLabel(field.label)))));
  Object.entries(hostFieldLabels).forEach(([key, label]) => table.set(key, quotedHostLabel(label)));
  // A path role reads as a noun inside the sentence.
  window.ServerManDiagnosticLabels.roleKeys()
    .forEach((role) => table.set(role, `the ${window.ServerManDiagnosticLabels.role(role)}`));
  Object.entries(hostEnumWords).forEach(([value, word]) => table.set(value, quotedHostLabel(word)));
  // Longer identifiers are tried first, so a key never matches inside a longer key.
  const keys = [...table.keys()].sort((left, right) => right.length - left.length)
    .map((key) => key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  hostIdentifierCache.pattern = new RegExp(`(?<![\\w.])(?:${keys.join("|")})(?![\\w])`, "g");
  hostIdentifierCache.table = table;
  return table;
}

// Return the on-screen label of a field key in quotation marks, or null for an unknown key.
function hostFieldLabel(key) {
  return hostIdentifierTable().get(String(key)) || null;
}

// Report whether a text still holds something that is not operator wording.
// That is an upper-case identifier, a snake_case word, a camelCase word, or a bracketed list of names.
function hostTextLeaks(text) {
  return /[A-Z]{2,}_[A-Z_]+/.test(text) || /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/.test(text)
    || /\b[a-z]+[A-Z]\w*/.test(text) || /[[\]{}]/.test(text);
}

// Turn a host message into one operator sentence, or return null when it cannot be shown.
// It cannot be shown when it is empty, is a bare code, or still holds an identifier after the translation.
function hostSentence(message) {
  const table = hostIdentifierTable();
  let text = String(message || "").trim().replace(hostIdentifierCache.pattern, (key) => table.get(key));
  text = text.replaceAll("DayZ root", "DayZ server folder");
  if (!text.includes(" ") || hostTextLeaks(text)) return null;
  // Start with a capital letter and end with a full stop.
  text = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

// Word why changes are blocked: the known sentence, else the host sentence, else the fallback.
function hostBlockReason(reason) {
  const text = String(reason || "");
  const known = hostBlockReasons.find(([fragment]) => text.includes(fragment));
  return known ? known[1] : hostSentence(text) || HOST_BLOCK_FALLBACK;
}

// Publish the host sentence lookups used by the message catalogue and by the pages.
window.ServerManHostSentences = Object.freeze({
  sentence: hostSentence,
  fieldLabel: hostFieldLabel,
  blockReason: hostBlockReason,
  leaks: hostTextLeaks,
});
