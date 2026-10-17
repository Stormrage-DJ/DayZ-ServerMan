// Window texts of Export plan, Import plan and Copy from another profile. The XFER texts are the Product Owner texts of 2026-10-17
// 17:14:17 in evidence 9.1, word for word. The window also reuses the CLI import and export texts of evidence 10.1
// without their CLI-only lines (PO-91-2); they are copied here word for word, because the page has no CLI module.
"use strict";

// XFER-1 to XFER-32 of the import and export forms, and the two new texts of the Architect entry
const mapTransferWordsFixed = Object.freeze({
  heading: "Plan file", exportLabel: "Export plan…", importLabel: "Import plan…", exportLink: "Export plan…",
  // XFER-4: why Import is not available, in the order of the gates
  reasons: Object.freeze({
    loading: "Import and copy are not available until the map plan is loaded.",
    readOnly: "Import and copy are not available because the map plan is read only.",
    conflict: "Import and copy are not available because autosave stopped. Choose Export plan to keep your " +
      "changes, then Reload plan.",
    busy: "Import and copy are not available while a map operation runs.",
    reading: "Wait until the current file or preview step ends.",
    bounds: "Import and copy are not available until you confirm the terrain bounds."}),
  exportLoading: "Export is not available until the map plan is loaded.",
  exporting: "Exporting the map plan", checkingFile: "Checking the file", importing: "Importing the map plan",
  paused: "Editing and autosave are paused until the import ends. You can still move and zoom the map and select " +
    "items.",
  notOpened: "The import did not start because the map editor could not save the current draft.",
  stillUnsaved: "The plan in the window is still not saved. The file is the way to keep it.",
  stale: "Not imported: The saved map plan changed. Reload plan shows its current state. Nothing was changed.",
  placement: "The terrain or the bounds differ. Check the placement of the selected objects.",
  undo: "Import map plan", title: "Import map plan",
  intro: "DayZ-ServerMan checked the file. Nothing is written to the mission files.",
  legend: "How to add the objects", modes: Object.freeze({replace: "Replace", merge: "Merge"}),
  placementBox: "I will check the placement of every object.", confirm: "Import plan",
  previewStale: "The saved map plan can have changed after this preview. Preview again before you import.",
  exportInvalid: "Not exported: The map plan in the window is not valid. Nothing was written.",
  selectionUnknown: "The map editor no longer has the chosen file. Nothing was changed. Choose Import plan to " +
    "select the file again.",
  // PO-91-2: the window form of the destination `no_valid_plan` text
  noValidPlan: "This profile has no valid saved map plan. Nothing was changed.",
});

// XFER texts of Copy from another profile (9.2), word for word from the Product Owner entry of 17:14:17
const mapTransferWordsCopy = Object.freeze({
  label: "Copy from another profile…", checking: "Checking the source profile", working: "Copying the map plan",
  paused: "Editing and autosave are paused until the copy ends. You can still move and zoom the map and select " +
    "items.",
  notOpened: "The copy did not start because the map editor could not save the current draft.",
  stale: "Not copied: The saved map plan changed. Reload plan shows its current state. Nothing was changed.",
  undo: "Copy map plan", intro: "DayZ-ServerMan checked the source plan. The source plan does not change. Nothing " +
    "is written to the mission files.", confirm: "Copy plan",
  previewStale: "The saved map plan or the source plan can have changed after this preview. Preview again before " +
    "you copy.",
  pickerTitle: "Choose a source profile", pickerLegend: "Source profile", pickerConfirm: "Continue",
  pickerIntro: "DayZ-ServerMan copies the map plan of the profile that you choose. The source plan does not change.",
  pickerRemaining: "Choose a source profile to continue.", empty: "No other profile exists.",
  pickerStale: "The profiles or the map plans can have changed after this list. Preview again before you continue.",
  disabled: Object.freeze({self: "This is the profile that you edit now.",
    no_mission: "This profile has no active mission link."})});

// XFER-11 by the kind of the host refusal; {file} is the base file name
const mapTransferWordsFiles = Object.freeze({
  unreadable: "{file} could not be read. Nothing was changed.",
  too_large: "{file} is larger than 8 MiB. Nothing was changed.",
  not_utf8: "{file} is not UTF-8 text. Nothing was changed.",
  not_json: "{file} is not valid JSON. Nothing was changed."});

// The CLI texts of evidence 10.1 and 10.33 that the window reuses (PO-91-2); `{path}` receives the base file name
const mapTransferWordsCli = Object.freeze({
  heading: "Compatibility", terrainSame: "Terrain: same as this mission", boundsSame: "Bounds: same",
  unavailable: "Objects that are not available on this mission",
  excluded: "Objects left out because they do not fit the bounds", unmapped: "Items that are not imported",
  placementReview: "The terrain or the bounds differ. Check the placement of every object before you continue.",
  leftOutOne: "1 object was left out.",
  providerUnread: "DayZ-ServerMan could not read whether this mission supports this kind of encounter.",
  noReason: "This mission cannot support this operation.",
  // `map export` texts with {path}
  written: "Map plan written to {path}.", folderOrLink: "{path} is a folder or a link. Name a file.",
  protected: "{path} is in a folder that DayZ-ServerMan uses: the DayZ server folder, the DayZ-ServerMan folder " +
    "or the backup folder. Name a file in another folder.",
  notWritten: "The file {path} could not be written correctly. DayZ-ServerMan removed it."});

// Fill the {name} placeholders of one text
function mapTransferWordsFill(text, values) {
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in values ? String(values[name]) : match));
}

// A number with at most three decimals and no trailing zeros, as `map import` shows the bounds difference
function mapTransferWordsNumber(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  const text = value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  return text === "" || text === "-0" ? "0" : text;
}

// The result line with its singular forms (Product Owner 2026-10-17 03:37:11 in evidence 10.33)
function mapTransferWordsResult(objects, vertices) {
  const things = objects === 1 ? "1 object" : `${objects} objects`;
  const points = vertices === 1 ? "1 polygon vertex" : `${vertices} polygon vertices`;
  return `The plan will have ${things} and ${points}.`;
}

// The reason text of an object that is not available on this mission, as `map import` words it
function mapTransferWordsReason(item) {
  const cli = mapTransferWordsCli, encounters = window.ServerManMapEncounterWording;
  if (item.reason === "tier_not_holdable") return window.ServerManMapInspectWording.tierNotHoldable;
  if (item.reason === "event_not_offered") return encounters.notOffered(item.event_name);
  if (item.reason === "provider_unavailable") return cli.providerUnread;
  if (item.provider === "native_infected") return encounters.infectedReason(item.reason);
  if (["ai_bandits", "native_wolf", "native_bear"].includes(item.provider)) return String(item.reason);
  return cli.noReason;
}

// The review lines that name the file or the source plan (COPY_LABELS in cli/map_wording.py)
function mapTransferWordsLines(copy) {
  const plan = copy ? "the source plan" : "the file", number = mapTransferWordsNumber;
  const insertions = {replace: `Replace: the objects of ${plan} replace the objects of this plan.`,
    top: `Merge: the objects of ${plan} go above all objects of this plan, in their order.`};
  return Object.freeze({
    terrain: (imported, own) => `Terrain: ${imported} in ${copy ? "the source profile" : "the file"}, ${own} in this mission`,
    bounds: (difference) => `Bounds: ${plan} differs by X ${number(difference?.x_min)}, Z ${number(difference?.z_min)}, ` +
      `width ${number(difference?.width)} and height ${number(difference?.height)} metres`,
    objects: (count) => `Objects in ${plan}: ${count}`, insertion: (key) => insertions[key],
    capacity: (destination, imported) => `Global container capacity: ` +
      `${window.ServerManMapInspectWording.signed(destination)} stays. The ${copy ? "source plan" : "file"} has ` +
      `${window.ServerManMapInspectWording.signed(imported)}.`,
    discarded: (count) => `Fields of ${plan} that are not kept: ${count}`});
}

// The words of one flow, import or copy (XFER-14, XFER-28 to XFER-30); the copy texts are the second form
const mapTransferWordsKinds = Object.freeze({
  import: Object.freeze({...mapTransferWordsFixed, checking: mapTransferWordsFixed.checkingFile,
    working: mapTransferWordsFixed.importing,
    refused: (reason) => /Nothing was changed\.$/.test(reason) ? `Not imported: ${reason}` :
      `Not imported: ${reason} Nothing was changed.`,
    done: (revision) => `Map plan imported as revision ${revision}.`, lines: mapTransferWordsLines(false),
    leftOutBox: (count) => count === 1 ? "Import without the listed object" : `Import without the ${count} listed objects`,
    unmappedBox: (count) => count === 1 ? "Import without the listed item" : `Import without the ${count} listed items`,
    remaining: (count) => count === 1 ? "1 choice is still needed before you can import." :
      `${count} choices are still needed before you can import.`}),
  copy: Object.freeze({...mapTransferWordsCopy,
    refused: (reason) => /Nothing was changed\.$/.test(reason) ? `Not copied: ${reason}` :
      `Not copied: ${reason} Nothing was changed.`,
    done: (revision) => `Map plan copied as revision ${revision}.`, lines: mapTransferWordsLines(true),
    title: (profile) => `Copy map plan from ${profile}`,
    source: (profile, mission) => `Source profile: ${profile}. Mission: ${mission}.`,
    mission: (mission) => `Mission: ${mission}`,
    leftOutBox: (count) => count === 1 ? "Copy without the listed object" : `Copy without the ${count} listed objects`,
    unmappedBox: (count) => count === 1 ? "Copy without the listed item" : `Copy without the ${count} listed items`,
    remaining: (count) => count === 1 ? "1 choice is still needed before you can copy." :
      `${count} choices are still needed before you can copy.`})});

window.ServerManMapTransferWords = Object.freeze({
  ...mapTransferWordsFixed, cli: mapTransferWordsCli, fill: mapTransferWordsFill, reason: mapTransferWordsReason,
  kinds: mapTransferWordsKinds, copyLabel: mapTransferWordsCopy.label,
  file: (kind, file) => mapTransferWordsFill(mapTransferWordsFiles[kind] ?? mapTransferWordsFiles.unreadable, {file}),
  modeRefused: (text) => `This mode is not possible. ${text}`,
  problems: (count) => `Objects with problems: ${count}`, row: (name, reason) => `${name}: ${reason}`,
  newIds: (count) => `Objects that get a new ID: ${count}`,
  capacityReplace: (imported) => `Global container capacity: ${window.ServerManMapInspectWording.signed(imported)}.`,
  withLinkedLoot: (name) => `${name} (with its linked loot)`, result: mapTransferWordsResult,
  leftOut: (count) => count === 1 ? mapTransferWordsCli.leftOutOne : `${count} objects were left out.`,
  path: (text, path) => mapTransferWordsFill(text, {path}),
});
