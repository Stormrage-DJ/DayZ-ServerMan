// Operator wording for path diagnostics, process diagnostics, per-mod outcomes, and small host enumerations.
// Raw statuses, codes, and roles are keys here and are never printed.
"use strict";

// Label of each path role inside a sentence.
const pathRoleLabels = Object.freeze({
  dayz_root: "DayZ server folder", dayz_executable: "DayZ server program",
  steamcmd_root: "SteamCMD folder", steamcmd_executable: "SteamCMD program",
  workshop_content_root: "Workshop download folder",
  backup_root: "backup folder", custom_backup_root: "backup folder",
});
// Per path status: the short label and the sentence; "{l}" stands for the role label.
const pathStatusLabels = Object.freeze({
  READY: ["Ready", "The {l} is ready."],
  UNCONFIGURED: ["Not set", "Choose the {l}."],
  MISSING: ["Not found", "The {l} does not exist. Choose the correct location."],
  MOVED: ["Moved or missing", "The {l} is no longer where it was. Reconnect the drive or choose the new location."],
  NOT_FILE: ["Not a file", "This location is not a file. Choose the {l}."],
  NOT_DIRECTORY: ["Not a folder", "This location is not a folder. Choose a folder."],
  NOT_WRITABLE: ["Read-only", "DayZ-ServerMan cannot write to the {l}. Choose another folder or change its permissions."],
  UNSUPPORTED_NETWORK: ["Network location", "Network locations are not supported. Choose a folder on a local drive."],
  UNSUPPORTED_REPARSE: ["Linked folder", "Links and junctions are not supported. Choose the real folder."],
  INACCESSIBLE: ["Cannot be opened", "Windows did not allow access to the {l}. Check its permissions."],
});
// Sentence per process diagnostic code of the server status.
const processDiagnosticLabels = Object.freeze({
  RECOVERY_REQUIRED: "Recovery is required before the server can be controlled.",
  INVENTORY_UNAVAILABLE: "The running programs could not be read.",
  INVENTORY_INCOMPLETE: "The running programs could not be read completely.",
  PROCESS_AMBIGUOUS: "More than one matching DayZ process is running.",
});
// Text per outcome of one mod in an update result.
const modOutcomeLabels = Object.freeze({
  VERIFIED_CURRENT: "Already current", DOWNLOADED_VERIFIED: "Downloaded", UPDATED_VERIFIED: "Updated",
  AUTHENTICATION_FAILED: "Steam sign-in failed",
  ENTITLEMENT_FAILED: "This Steam account may not download the item",
  CONNECTION_FAILED: "Steam could not be reached", CONTENT_FAILED: "Download failed",
  CANCELLED: "Cancelled", NOT_ATTEMPTED: "Not tried", UNKNOWN_FAILED: "Could not verify",
});
// The two item error codes that add information to an outcome.
const modOutcomeDetails = Object.freeze({
  CACHE_VERIFICATION_FAILED: "Downloaded files could not be verified",
  CACHE_MANIFEST_ID_MISSING: "Download record is incomplete",
});
// Why a backup cannot be restored, per restore compatibility value of its card.
const backupRestoreReasons = Object.freeze({
  LEGACY_PROFILE_SCHEMA: "This backup was made by an older version, before backups held the full profile data. "
    + "Create a new backup to have one that can be restored.",
  PENDING_RUNTIME_PROFILE_SUPPORT: "This backup has content that this version cannot restore.",
});
// What a restore does with one target, inside a sentence.
const restoreActionLabels = Object.freeze({ REPLACE: "replace", CREATE: "create" });
// World destination of a profile restore, as the dialog offers it.
const storagePolicyLabels = Object.freeze({
  allocate_new: "New isolated mission and storage ID",
  preserve_original: "Original mission, only if absent",
  replace_existing: "Replace selected existing world",
});

// Look up one own key of a frozen table, or return the fallback.
function diagnosticLookup(table, key, fallback) {
  return Object.hasOwn(table, String(key)) ? table[key] : fallback;
}

// Word one path diagnostic: status label, sentence, and whether the path is ready.
function pathDiagnosticText(role, diagnostic) {
  if (!diagnostic) return Object.freeze({ label: "Not checked", sentence: "Choose a location to check it.", ready: false });
  const place = diagnosticLookup(pathRoleLabels, role, "location");
  const [label, sentence] = diagnosticLookup(pathStatusLabels, diagnostic.status, ["Needs attention", "Check the {l}."]);
  return Object.freeze({ label, sentence: sentence.replace("{l}", place), ready: diagnostic.status === "READY" });
}

// Word one mod outcome of an update result; only two error codes add a detail.
function modOutcomeText(entry) {
  const outcome = diagnosticLookup(modOutcomeLabels, entry?.outcome, "Failed");
  const detail = diagnosticLookup(modOutcomeDetails, entry?.error_code, "");
  return detail ? `${outcome} (${detail})` : outcome;
}

// Publish the diagnostic wording used by Settings, Overview, Mods, Backups, and the legacy import.
window.ServerManDiagnosticLabels = Object.freeze({
  path: pathDiagnosticText,
  role: (role) => diagnosticLookup(pathRoleLabels, role, "location"),
  roleKeys: () => Object.keys(pathRoleLabels),
  process: (code) => diagnosticLookup(processDiagnosticLabels, code, "The server state could not be confirmed."),
  modOutcome: modOutcomeText,
  restoreReason: (compatibility) => diagnosticLookup(backupRestoreReasons, compatibility,
    "This backup cannot be restored by this version."),
  restoreAction: (action) => diagnosticLookup(restoreActionLabels, action, "change"),
  storagePolicy: (policy) => diagnosticLookup(storagePolicyLabels, policy, "Automatic"),
});
