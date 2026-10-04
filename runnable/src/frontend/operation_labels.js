// Operator wording for operation kinds, phases, and states. Raw identifiers are keys here and are never printed.
"use strict";

// Per operation kind: name while active, success sentence, failure sentence, cancellation sentence.
const operationKindLabels = Object.freeze({
  START_SERVER: ["Starting server", "Server started.", "The server could not be started.", "Server start cancelled."],
  STOP_SERVER: ["Stopping server", "Server stopped.", "The server could not be stopped.", "Backup cancelled. The server stays stopped."],
  RESTART_SERVER: ["Restarting server", "Server restarted.", "The server could not be restarted.", "Restart cancelled during the backup. The server stays stopped."],
  CREATE_BACKUP: ["Creating backup", "Backup created.", "The backup could not be created.", "Backup cancelled."],
  RESTORE_BACKUP: ["Restoring backup", "Backup restored.", "The backup could not be restored.", "Restore cancelled. Server files were not changed."],
  RESTORE_PROFILE_FROM_BACKUP: ["Restoring profile from backup", "Profile restored.", "The profile could not be restored.", "Profile restore cancelled."],
  SAVE_PROFILE: ["Saving profile", "Profile saved.", "The profile could not be saved.", "Profile save cancelled."],
  DELETE_PROFILE: ["Deleting profile", "Profile deleted.", "The profile could not be deleted.", "Profile deletion cancelled."],
  PROVISION_PROFILE: ["Creating profile", "Profile created.", "The profile could not be created.", "Profile creation cancelled."],
  APPLY_CONFIGURATION: ["Applying configuration changes", "Configuration changes applied.", "The configuration changes could not be applied.", "Configuration changes cancelled. Nothing was changed."],
  APPLY_MISSION_CONFIGURATION: ["Applying tweaks", "Tweaks applied.", "The tweaks could not be applied.", "Tweaks cancelled. Nothing was changed."],
  APPLY_MEDICAL_FEATURE: ["Applying medical loot setting", "Medical loot setting applied.", "The medical loot setting could not be applied.", "Medical loot change cancelled. Nothing was changed."],
  CONVERT_STARTER_LOADOUT: ["Converting starter loadout", "Starter loadout converted.", "The starter loadout could not be converted.", "Starter loadout conversion cancelled. Nothing was changed."],
  SAVE_SETTINGS: ["Saving application locations", "Application locations saved.", "The application locations could not be saved.", "Saving application locations cancelled."],
  SAVE_STEAM_SETTINGS: ["Saving Steam sign-in settings", "Steam sign-in settings saved.", "The Steam sign-in settings could not be saved.", "Saving Steam sign-in settings cancelled."],
  AUTHENTICATE_STEAMCMD: ["Signing in to Steam", "Steam sign-in completed.", "Steam sign-in did not complete.", "Steam sign-in cancelled."],
  UPDATE_WORKSHOP_ITEMS: ["Updating mods", "Mods are downloaded and checked.", "The mods could not be updated.", "Mod update cancelled."],
  PUBLISH_MODS_AND_KEYS: ["Applying mods to the server", "Mods and keys applied.", "The mods could not be applied.", "Applying mods cancelled."],
  APPLY_MODS_AND_RESTART: ["Applying mods and restarting the server", "Mods applied and server restarted.", "Applying mods and restarting the server did not finish.", "Applying mods and restarting cancelled."],
  VERIFY_WORKSHOP_FILES: ["Verifying mod files", "Mod files verified.", "The mod files could not be verified.", "Verification cancelled. Finished mods stay recorded."],
  IMPORT_LEGACY: ["Importing legacy data", "Legacy data imported.", "The legacy data could not be imported.", "Legacy import cancelled."],
  REVALIDATE_LEGACY_BACKUPS: ["Checking legacy backups", "Legacy backups checked.", "The legacy backups could not be checked.", "Legacy backup check cancelled."],
});
// Wording for a kind that this catalogue does not know yet.
const operationKindFallback = Object.freeze(
  ["Working", "Operation finished.", "The operation did not finish.", "Operation cancelled."],
);

// Per "kind/phase": text and whether the percent moves during the phase (true: determinate).
// "*" rows hold for every kind; "LIFECYCLE" rows hold for the backup inside a stop or restart.
const operationPhaseLabels = Object.freeze({
  "*/accepted": ["Waiting to start", false],
  "*/queued": ["Waiting to start", false],
  "*/running": ["Starting", false],
  "START_SERVER/preflight": ["Checking and starting the server", false],
  "STOP_SERVER/preflight": ["Checking the server state", false],
  "STOP_SERVER/STOP_SERVER": ["Saving the world and stopping the server", false],
  "RESTART_SERVER/STOP_SERVER": ["Saving the world and stopping the server", false],
  "RESTART_SERVER/preflight": ["Stopping and starting the server", false],
  "RESTART_SERVER/START_SERVER": ["Starting the server", false],
  "LIFECYCLE/BACKUP_DISCOVER": ["Backup: copying files", true],
  "LIFECYCLE/BACKUP_STAGE": ["Backup: checking copied files", true],
  "LIFECYCLE/BACKUP_HASH": ["Backup: writing the file list", true],
  "LIFECYCLE/BACKUP_WRITE_MANIFEST": ["Backup: building and verifying the archive", true],
  "LIFECYCLE/BACKUP_VERIFY": ["Backup: saving the archive", true],
  "LIFECYCLE/BACKUP_PUBLISH": ["Backup: saving the archive", true],
  "CREATE_BACKUP/DISCOVER": ["Copying files", true],
  "CREATE_BACKUP/STAGE": ["Checking copied files", true],
  "CREATE_BACKUP/HASH": ["Writing the file list", true],
  "CREATE_BACKUP/WRITE_MANIFEST": ["Building and verifying the archive", true],
  "CREATE_BACKUP/VERIFY": ["Saving the backup", true],
  "CREATE_BACKUP/PUBLISH": ["Saving the backup", true],
  "RESTORE_BACKUP/VERIFY_SOURCE": ["Saving a recovery copy and preparing files to restore", true],
  "RESTORE_BACKUP/STAGE_TARGETS": ["Saving a recovery copy and preparing files to restore", true],
  "RESTORE_BACKUP/PREPARE_RECOVERY": ["Recording the restore plan", true],
  "RESTORE_BACKUP/WRITE_JOURNAL": ["Replacing server files", false],
  "RESTORE_PROFILE_FROM_BACKUP/VERIFYING_BACKUP": ["Verifying the backup", true],
  "RESTORE_PROFILE_FROM_BACKUP/PREPARING": ["Preparing files", true],
  "RESTORE_PROFILE_FROM_BACKUP/PREPARED": ["Restoring server files", true],
  "RESTORE_PROFILE_FROM_BACKUP/PUBLISHING": ["Restoring server files", true],
  "RESTORE_PROFILE_FROM_BACKUP/PROFILE_PUBLISHING": ["Saving the profile", true],
  "PROVISION_PROFILE/stage_profile": ["Preparing profile files", true],
  "PROVISION_PROFILE/publish_profile_files": ["Creating server files", true],
  "PROVISION_PROFILE/reuse_profile_files": ["Reusing existing server files", true],
  "PROVISION_PROFILE/save_profile": ["Saving the profile", true],
  "PROVISION_PROFILE/verify_launch": ["Checking that the profile can start", true],
  "APPLY_CONFIGURATION/loaded": ["Checking the changes", true],
  "APPLY_CONFIGURATION/validated": ["Writing the file", true],
  "APPLY_CONFIGURATION/verified": ["Finishing", true],
  "APPLY_MISSION_CONFIGURATION/loaded": ["Checking the changes", true],
  "APPLY_MISSION_CONFIGURATION/validated": ["Writing the file", true],
  "APPLY_MISSION_CONFIGURATION/published": ["Verifying the written file", true],
  "APPLY_MISSION_CONFIGURATION/verified": ["Finishing", true],
  "APPLY_MEDICAL_FEATURE/loaded": ["Checking the changes", true],
  "APPLY_MEDICAL_FEATURE/validated": ["Writing the file", true],
  "APPLY_MEDICAL_FEATURE/verified": ["Finishing", true],
  "CONVERT_STARTER_LOADOUT/loaded": ["Checking the changes", true],
  "CONVERT_STARTER_LOADOUT/validated": ["Writing the file", true],
  "CONVERT_STARTER_LOADOUT/published": ["Verifying the written file", true],
  "CONVERT_STARTER_LOADOUT/verified": ["Finishing", true],
  "AUTHENTICATE_STEAMCMD/preflight": ["Checking settings and folders", true],
  "AUTHENTICATE_STEAMCMD/interactive_authentication": ["Waiting for sign-in in the SteamCMD window", false],
  "UPDATE_WORKSHOP_ITEMS/preflight": ["Checking settings and folders", true],
  "UPDATE_WORKSHOP_ITEMS/resolve_items": ["Reading downloaded mods", true],
  "UPDATE_WORKSHOP_ITEMS/check_remote": ["Checking Steam for changes", false],
  "UPDATE_WORKSHOP_ITEMS/download": ["Downloading changed mods", false],
  "UPDATE_WORKSHOP_ITEMS/verify_items": ["Verifying downloaded mods", true],
  "UPDATE_WORKSHOP_ITEMS/verify_set": ["Finishing", true],
  "VERIFY_WORKSHOP_FILES/verify_source": ["Verifying downloaded files", true],
  "VERIFY_WORKSHOP_FILES/verify_target": ["Verifying server folder copies", true],
  "PUBLISH_MODS_AND_KEYS/PUBLICATION_PREFLIGHT": ["Checking the reviewed plan", true],
  "PUBLISH_MODS_AND_KEYS/DISCOVER_ITEM": ["Reading downloaded mods", true],
  "PUBLISH_MODS_AND_KEYS/CACHE_PROOF_RECHECK": ["Checking downloaded mods", true],
  "PUBLISH_MODS_AND_KEYS/STAGE_TARGET": ["Preparing mod folders", true],
  "PUBLISH_MODS_AND_KEYS/CHECK_TARGET": ["Checking the server folder", true],
  "PUBLISH_MODS_AND_KEYS/COPY_FILE": ["Copying mod files", true],
  "PUBLISH_MODS_AND_KEYS/COPY_KEY": ["Copying key files", true],
  "PUBLISH_MODS_AND_KEYS/BEFORE_PUBLICATION": ["Replacing mod folders on the server", false],
  "PUBLISH_MODS_AND_KEYS/AFTER_LIVE_TARGET": ["Replacing mod folders on the server", false],
  "PUBLISH_MODS_AND_KEYS/COMPENSATE_TARGET": ["Undoing changes after a problem", false],
  "PUBLISH_MODS_AND_KEYS/VERIFY_BEFORE_START": ["Checking the server folder before the start", false],
  "PUBLISH_MODS_AND_KEYS/START_SERVER": ["Starting the server", false],
  "APPLY_MODS_AND_RESTART/preflight": ["Checking the reviewed plan", false],
  "IMPORT_LEGACY/DISCOVER": ["Copying legacy files", true],
  "IMPORT_LEGACY/COPY_SOURCE": ["Converting legacy data", true],
  "IMPORT_LEGACY/CONVERT": ["Verifying converted data", true],
  "IMPORT_LEGACY/VERIFY": ["Saving imported data", true],
  "IMPORT_LEGACY/PUBLISH": ["Finishing", true],
  "IMPORT_LEGACY/REPORT": ["Finishing", true],
});
// Wording for a phase that this catalogue does not know yet; such a phase never shows a percent.
const operationPhaseFallback = Object.freeze(["Working", false]);
// Phases that the lane sets when an operation ends; the result sentence stands in their place.
const operationTerminalPhases = Object.freeze(["complete", "cancelled", "failed", "shutdown"]);
// Kinds whose backup phases carry the "BACKUP_" prefix.
const operationLifecycleKinds = Object.freeze(["STOP_SERVER", "RESTART_SERVER", "APPLY_MODS_AND_RESTART"]);
// Kinds whose steps are steps of other kinds: their phases are also looked up under those kinds, in this order.
const operationBorrowedPhases = Object.freeze({
  APPLY_MODS_AND_RESTART: ["PUBLISH_MODS_AND_KEYS", "RESTART_SERVER"],
});

// Label per operation state, for the queue list and for screen-reader text.
const operationStateLabels = Object.freeze({
  ACCEPTED: "Waiting", QUEUED: "Waiting", RUNNING: "In progress", CANCELLING: "Cancelling",
  SUCCEEDED: "Done", FAILED: "Failed", CANCELLED: "Cancelled", RECOVERY_REQUIRED: "Needs recovery",
});

// Line that a page shows while its own operation is cancelling, per kind.
const operationCancellingTexts = Object.freeze({
  AUTHENTICATE_STEAMCMD: "Cancelling. The sign-in stops at the next safe moment.",
  UPDATE_WORKSHOP_ITEMS: "Cancelling. The update stops at the next safe moment.",
  VERIFY_WORKSHOP_FILES: "Cancelling. The verification stops at the next safe moment.",
  PUBLISH_MODS_AND_KEYS: "Cancelling. Applying the mods stops at the next safe moment.",
  APPLY_MODS_AND_RESTART: "Cancelling. The restart stops at the next safe moment.",
});

// Return the four texts of an operation kind, or the fallback for an unknown kind.
function operationKindText(kind) {
  const [name, success, failure, cancelled] = Object.hasOwn(operationKindLabels, String(kind))
    ? operationKindLabels[kind] : operationKindFallback;
  return Object.freeze({ name, success, failure, cancelled });
}

// Return the text of a phase and whether its percent moves; an unknown phase gets the fallback.
function operationPhaseText(kind, phase) {
  // Look up the kind's own row first, then the shared backup rows of a stop or restart, then the generic rows.
  const keys = [`${kind}/${phase}`];
  if (Object.hasOwn(operationBorrowedPhases, String(kind))) {
    keys.push(...operationBorrowedPhases[kind].map((other) => `${other}/${phase}`));
  }
  if (operationLifecycleKinds.includes(kind) && String(phase).startsWith("BACKUP_")) keys.push(`LIFECYCLE/${phase}`);
  keys.push(`*/${phase}`);
  const key = keys.find((candidate) => Object.hasOwn(operationPhaseLabels, candidate));
  const [text, determinate] = key ? operationPhaseLabels[key] : operationPhaseFallback;
  return Object.freeze({ text, determinate });
}

// Report whether a phase is a working phase: set by a checkpoint, not by the lane itself.
function isWorkingPhase(phase) {
  return typeof phase === "string" && phase !== ""
    && !operationTerminalPhases.includes(phase) && !Object.hasOwn(operationPhaseLabels, `*/${phase}`);
}

// Return the label of an operation state, or the label for an unknown value.
function operationStateText(state) {
  return Object.hasOwn(operationStateLabels, String(state)) ? operationStateLabels[state] : "Status unknown";
}

// Return the cancelling line of an operation kind, or the line for a kind without its own.
function operationCancellingText(kind) {
  return Object.hasOwn(operationCancellingTexts, String(kind)) ? operationCancellingTexts[kind]
    : "Cancelling. The operation stops at the next safe moment.";
}

// Publish the catalogue lookups; the tables themselves stay private to this module.
window.ServerManOperationLabels = Object.freeze({
  kind: operationKindText,
  phase: operationPhaseText,
  state: operationStateText,
  cancelling: operationCancellingText,
  isWorkingPhase,
});
