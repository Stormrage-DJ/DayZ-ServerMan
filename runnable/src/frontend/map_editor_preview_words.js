// Window texts of the Apply and Restore preview dialog (8.10). Backend texts of the answer are never copied here.
"use strict";

// Product Owner texts of 2026-10-15 15:57:10 (decisions 3, 4, 5 and 10), 16:03:40 and 18:04:08 in evidence 8.10,
// word for word
const mapPreviewWordsFixed = Object.freeze({
  titles: Object.freeze({apply: "Apply map plan", previous: "Restore previous state", original: "Restore original",
    adopt: "Adopt mission"}),
  confirm: Object.freeze({apply: "Apply", previous: "Restore previous state", original: "Restore original", adopt: "Adopt"}),
  cancel: "Cancel", close: "Close", again: "Preview again",
  stop: Object.freeze({
    apply: "Stop the DayZ server before you apply. DayZ-ServerMan does not apply while the server runs.",
    restore: "Stop the DayZ server before you restore. DayZ-ServerMan does not restore while the server runs.",
    // ADOPT-19 is empty: adoption writes no mission file, and the frame draws no empty stop paragraph
    adopt: ""}),
  stale: Object.freeze({
    apply: "The mission files or the map editor records can have changed after this preview. Preview again before you apply.",
    restore: "The mission files or the map editor records can have changed after this preview. Preview again before you restore.",
    adopt: "The mission files or the map editor records can have changed after this preview. Preview again before you adopt."}),
  headings: Object.freeze({
    refusals: "Apply is not possible", notices: "Check before you apply", external: "Files changed outside the map editor",
    encounterChoices: "Unavailable encounters", changes: "Map plan changes", encounters: "Encounters",
    loot: "Loot report", files: "Mission files",
    restoreRefusals: "Restore is not possible", restoreNotices: "Check before you restore",
    writes: "What the restore writes", overwritten: "Outside changes that the restore overwrites",
    kept: "Outside changes that the restore keeps",
    // ADOPT-10, ADOPT-11, ADOPT-14 and ADOPT-17 (Product Owner 2026-10-16 14:26:38 in evidence 10.1)
    adoptRefusals: "Adoption is not possible", adoptHappened: "What happened", adoptWrites: "What adoption writes",
    adoptUnchanged: "What stays unchanged"}),
  // ADOPT-1: the label of the action and of the second Apply button. ADOPT-2: its busy reason. ADOPT-3: the reading
  // line. Last, the ADOPT-15 label of the record list.
  adopt: "Adopt mission…", adoptBusy: "Wait until the adoption preview is ready.",
  adoptReading: "Preparing the adoption preview",
  // ADOPT-30 and ADOPT-33 (Product Owner 2026-10-17 13:07:29 in evidence 10.16)
  adoptNotOpened: "The adoption preview did not open because the map editor could not save the current draft.",
  adoptReload: (reason) => `Not adopted: ${reason} Reload plan shows its current state.`, adoptRecords: "Records that adoption writes",
  bounds: "The terrain bounds of this plan are not confirmed. Apply cannot write loot tiers until you confirm the " +
    "bounds under Terrain bounds.",
  outline: "The outline you are drawing is not finished. It is not part of the plan, so Apply does not write it.",
  others: Object.freeze({
    apply: "Other profiles use this mission folder or have map editor records for it. Apply changes the mission " +
      "files for every profile that uses this folder.",
    previous: "Other profiles use this mission folder or have map editor records for it. The restore changes the " +
      "mission files for every profile that uses this folder."}),
  externalIntro: "Choose for each file: keep the change or replace the file.",
  replaceNote: "DayZ-ServerMan saves a copy of the current file before it replaces the file.",
  // Reused 8.7 choice names (Product Owner 2026-10-15 01:16:10 in evidence 8.7)
  keep: "Keep the change", replace: "Replace the file",
  encounterIntro: "Apply cannot write these encounters on this mission. Exclude each one from this Apply to continue.",
  // Reused 7.4 text (Product Owner 2026-10-14 10:17:01)
  linkedLoot: "Its linked loot is still applied.",
  noPrevious: "No zone or encounter list of a last Apply is recorded, so every zone and encounter shows as added.",
  added: "Added", changed: "Changed", removed: "Removed",
  written: "Infected encounters that Apply writes",
  excluded: "Excluded in the plan. Apply does not write these encounters.",
  // Loot report list labels (Product Owner 2026-10-15 18:04:08, PO-1a, PO-1b and PO-2)
  lootUnavailable: "Zones and encounters with a loot tier that Apply cannot write",
  lootCapacity: "Building groups with a capacity that Apply changes",
  lootKept: "Buildings that do not get the capacity of this plan",
  affected: "Files that Apply changes",
  coordinated: "Changes made by other DayZ-ServerMan settings, for example medical loot zones. Apply keeps them.",
  restoreAffected: "Files that the restore changes",
  restoreCoordinated: "Changes made by other DayZ-ServerMan settings, for example medical loot zones. The restore " +
    "overwrites them.",
  // {change} of the previous-state line, by the record kind in `replaces`
  changes: Object.freeze({apply: "Apply", restore_original: "Restore original",
    restore_previous: "Restore previous state", medical: "medical loot zones change"}),
});

// The date and time of a record, in the form of the main window ("medium" date, "short" time); a bad value stays raw
function mapPreviewWordsTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) :
    date.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
}

// Texts with values; {name}, {count}, {path} and {time} come from the answer
window.ServerManMapPreviewWords = Object.freeze({
  ...mapPreviewWordsFixed, time: mapPreviewWordsTime,
  deletedProfile: (id) => `${id} (deleted profile)`,
  exclude: (name) => `Exclude ${name} from this Apply`,
  global: (current, applied) => `Global container capacity: ${window.ServerManMapInspectWording.signed(current)}` +
    (Number.isInteger(applied) && applied !== current ?
      ` (last Apply: ${window.ServerManMapInspectWording.signed(applied)})` : ""),
  entries: (count) => count === 1 ? "1 entry" : `${count} entries`,
  remaining: (count) => count === 1 ? "1 choice is still needed before you can apply." :
    `${count} choices are still needed before you can apply.`,
  previousWrites: (kind, time) => "The restore writes the mission files as they were before the last recorded " +
    `change: ${mapPreviewWordsFixed.changes[kind] ?? kind}, ${mapPreviewWordsTime(time)}.`,
  originalWrites: (time) => `The restore writes the original that DayZ-ServerMan saved on ${mapPreviewWordsTime(time)}.`,
  notOverwritten: (path) => `${path} changed outside the map editor. The restore does not change this file.`,
});
