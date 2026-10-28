# Release notes

Each release has one section. The newest release is first.

## 1.2.0 (2026-10-28)

This release makes large mod downloads finish, limits the number of backups,
lets you set starter items on every mission, and repairs profiles whose files
were deleted outside DayZ-ServerMan.

### Mod downloads

- SteamCMD ends a Workshop download after about 5 minutes. DayZ-ServerMan now
  starts it again after a 10-second pause when it runs out of time or loses
  the connection. The download continues where it stopped.
- Each update gets the time that its download size needs at 10 MB/s, and at
  least 5 minutes. An 11.7 GB map gets about 20 minutes.
- Mods download smallest first. A mod larger than 2.7 GB gets its own SteamCMD
  session, so it cannot hold back the small mods.
- A failed Steam sign-in, a missing entitlement, or a Workshop content failure
  ends the update at once, because another run cannot fix it.
- When the time is used up, the page says that the download did not finish in
  the time allowed for its size, instead of saying that Steam could not be
  reached.

### Backups and restores

- Each profile keeps its newest backups: 3 by default, from 1 to 100 in
  **Settings** or with `settings set --backups-to-keep N`. A new backup deletes
  the older backups of its profile beyond that number, and the result names
  them. Only verified backups count. Backups of other profiles, renamed ZIP
  files and unreadable ZIP files are never deleted.
- A successful restore deletes its recovery copy in `backups\recovery`. A
  failed or interrupted restore still uses it to put the old files back. To
  keep the state from before a restore, create a backup first.
- A restore needs only the backup ZIP. It creates every folder that is missing,
  such as a deleted `serverman\<profile>` folder or a deleted mission folder.

### Profiles with missing files

- A profile whose configuration was deleted no longer stops the creation of a
  new profile on another mission. On the same mission, the refusal names the
  profile.
- A profile whose folder or mission folder was deleted can be deleted.

### Tweaks

- **Gameplay Tweaks** offers **Turn on gameplay file** when the server does not
  use `cfgGameplay.json`. You no longer go to **Configuration** for it.
- **Starter Items** works on every mission. It shows the starting items of the
  mission's own code, its random choices and its other effects. Your own items
  are given in addition. **Give the mission's own starting items** turns the
  mission's code off and on. DayZ-ServerMan never edits the statements of the
  mission's code.
- A starter block of an earlier version moves into the new form the next time
  you save the starter items. Its items stay the same.

### Changes

- **Settings**: the button is now **Save settings**.
- The review of a world replacement says that the recovery copy is deleted
  when the restore succeeds.
- The map editor keeps the plan state and the operation status on one footer
  line, so the map no longer changes size during a save.

### Removed

- The conversion of a legacy starter loadout, with the window's **Review
  conversion** action and the command `tweaks convert-loadout`. Use
  `tweaks set --target starter-loadout`. A script that calls
  `tweaks convert-loadout` now ends with exit code 2.

### Known limitations

- SteamCMD's 5-minute limit cannot be changed. Each retry starts a new SteamCMD
  run. When Steam does not report the size of a mod, the update allows 5
  minutes.
- A mission whose starting code ends early (`return`) cannot get your own
  starter items. The page names the reason.
- The description of a mission's starting items lists items that the code
  chooses at random among its fixed items.

## 1.1.0 (2026-10-27)

This release adds the mission map editor and the loot supply settings.

### Mission map editor

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

### Loot supply

- A **Loot Supply** tab in **Tweaks › Gameplay Tweaks**. It has one slider for
  each item group. The slider sets how much of the group the economy keeps on
  the map, from 0% to 300% in steps of 5%. The groups are weapons, weapon
  attachments, magazines, loose ammunition, ammunition boxes, explosives, food
  and drink, medical supplies, tools, clothing, and backpacks and containers.
- An ammunition fill slider and a slider for the chance that a weapon spawns
  with a magazine.
- **Initial loot** (`InitialSpawn`) as a slider from 0% to 100%.
- The throughput values **Item types per respawn**, **Items per type per
  respawn** and **Restart respawn**.
- The `loot-supply` and `weapon-magazines` targets of `tweaks show` and
  `tweaks set`.

### Changes

- Each Tweaks page has one **Review changes** action and then one **Apply
  changes** action for all changed files of the page. The page reloads once,
  after the last apply.
- **Initial spawn attempts** (`SpawnInitial`) has a correct description. It is
  not a percentage.
- **Respawn attempts** and **Initial spawn attempts** moved from the Respawns
  tab to the Loot Supply tab.

### Removed

- The **Medical item spawns** setting. The **Medical supplies** slider replaces
  it. `tweaks medical set` accepts only `medical_loot_zones`. A script that
  calls `tweaks medical set medical_item_spawns` now ends with exit code 2.

### Known limitations

- Older builds cannot restore archives that hold editor records. A profile
  backup gets the new archive format (schema 4) only when the profile has an
  editor record for its active mission. Other backups keep the old format and
  stay readable by older builds. An older build refuses a schema 4 archive.
  Restore such an archive with this release or a later one.
- Polygon and vertex moves are drag only. A vertex coordinate table is not
  available yet.
- The loot supply changes only `db/types.xml`. Types files of mods that
  `cfgeconomycore.xml` adds do not change.

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
