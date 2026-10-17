# Release notes

Each release has one section. The newest release is first.

## Mission map editor

### What this release adds

- A separate editor window for each profile. You open it from the main
  window. It edits the loot tiers, the container capacity and the encounters of
  the selected mission.
- Terrain bounds that the editor detects from the mission, or that you enter
  and confirm. The editor works in native metres and shows a grid of 0 to 10.
  It works without a map image.
- Zoom and pan with the mouse. Zones have names. You can hide a zone on the
  map or lock it against edits.
- Detection of changes to the mission files outside the map editor. Apply
  warns about them, and you choose for each file to keep the change or to
  replace the file.
- A one-time import of a Pripyat prototype plan. The zones keep their
  positions. Items that cannot be mapped are listed, and you confirm them.
- Zones as circles and polygons. The highest zone in the order wins. A global
  setting and a zone setting change the container capacity together.
- An optional map image that you supply. DayZ-ServerMan ships no map images.
- Inspect shows the original tier, the effective tier, the winning zone and the
  covering encounters at a point.
- One undo and redo history for the whole plan.
- Plans that save by themselves for each profile. The window shows the save
  state and the changes since the last applied configuration.
- Apply and Restore as separate, explicit steps. Both need a stopped server.
  Apply shows a preview first. A protected copy of the original files is kept.
  You can restore the original state or the previous state.
- Recovery for an interrupted Apply or Restore. The manager checks at start
  and writes log lines for each recovered operation.
- Export, import and copy of a plan between profiles in the window. The
  buttons are **Export plan**, **Import plan** and **Copy from another
  profile**. Import and copy show a preview and ask for **Replace** or
  **Merge**.
- The `map` commands in the command line: `show`, `save`, `inspect`,
  `set-aside`, `preview`, `apply`, `restore`, `recover`, `export`, `import`,
  `copy` and `adopt`.
- Profile backups that carry the editor plan and the editor records of the
  active mission. Restore and profile delete keep these records in step with
  the mission.

### Known limitations

- Older builds cannot restore archives that hold editor records. A profile
  backup gets the new archive format (schema 4) only when the profile has an
  editor record for its active mission. Other backups keep the old format and
  stay readable by older builds. An older build refuses a schema 4 archive.
  Restore such an archive with this release or a later one.
- Polygon and vertex moves are drag only. A vertex coordinate table is not
  available yet.

### Supported terrain families

Apply and Restore original work for these terrain families. Each has the
DayZ server build of its test:

- `chernarusplus`: DayZ server 1.29.163709. Loot tiers, container capacity,
  the medical toggle and infected.
- `Pripyat`: DayZ server 1.29.163709. The same, with global capacity 0 only.
- `enoch`: DayZ server 1.30.164078.38. Loot tiers, container capacity and the
  medical toggle. No test with a DayZ server covered infected encounters
  there, but you can apply them.

sakhal is not supported yet. Apply and Restore original refuse on sakhal and
on every other family that is not listed. You can still plan, save, import,
export and inspect there. The operator guide has the limits of each test.
