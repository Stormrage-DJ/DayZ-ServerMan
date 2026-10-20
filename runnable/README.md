# DayZ-ServerMan operator guide

This directory is the complete movable application. Copy the whole directory.
Do not copy only the starter or `src` directory.

## Contents

- [Requirements](#requirements)
- [Start the application](#start-the-application)
- [First setup](#first-setup)
  - [Update checks](#update-checks)
- [Create a server profile](#create-a-server-profile)
- [The sidebar and the workspaces](#the-sidebar-and-the-workspaces)
- [Overview](#overview)
  - [Players online](#players-online)
- [Stop, restart, and automatic backup](#stop-restart-and-automatic-backup)
- [Daily schedule](#daily-schedule)
- [Mods and updates](#mods-and-updates)
  - [Steam sign-in](#steam-sign-in)
  - [Mod states](#mod-states)
  - [Update all](#update-all)
  - [Update & start and Update & restart](#update--start-and-update--restart)
  - [Verify files](#verify-files)
- [DayZ server build](#dayz-server-build)
- [The operation bar](#the-operation-bar)
- [Backups and restores](#backups-and-restores)
  - [Restore an existing profile](#restore-an-existing-profile)
  - [Restore a deleted profile from a ZIP](#restore-a-deleted-profile-from-a-zip)
- [When changes are blocked](#when-changes-are-blocked)
- [The mission map editor](#the-mission-map-editor)
  - [Open the editor](#open-the-editor)
  - [Terrain bounds and coordinates](#terrain-bounds-and-coordinates)
  - [Zones, tiers, and container capacity](#zones-tiers-and-container-capacity)
  - [Map image](#map-image)
  - [Save, export, import, and copy a plan](#save-export-import-and-copy-a-plan)
  - [Preview, Apply, and Restore](#preview-apply-and-restore)
  - [Native limits](#native-limits)
  - [Supported terrain families](#supported-terrain-families)
  - [Infected, bandits, wolves, and bears](#infected-bandits-wolves-and-bears)
  - [Backups with editor records](#backups-with-editor-records)
- [Command line](#command-line)
  - [Run a command](#run-a-command)
  - [Commands](#commands)
  - [Map commands](#map-commands)
  - [Which profile a command uses](#which-profile-a-command-uses)
  - [Questions, reviews, and --yes](#questions-reviews-and---yes)
  - [Scripts, terminals, and --json](#scripts-terminals-and---json)
  - [Output](#output)
  - [Exit codes](#exit-codes)
  - [JSON error codes](#json-error-codes)
  - [One manager that changes things](#one-manager-that-changes-things)
  - [Who controls the server](#who-controls-the-server)
  - [Progress and Ctrl+C](#progress-and-ctrlc)
  - [Restart without the window](#restart-without-the-window)
  - [Folders in Settings](#folders-in-settings)
  - [Examples](#examples)
  - [Known limitations](#known-limitations)
- [Local application data](#local-application-data)
- [Troubleshooting](#troubleshooting)
- [Before using a production server](#before-using-a-production-server)
- [License](#license)

## Requirements

- Windows 11 x64
- Python 3.12 or 3.13 with `venv` and pip
- Microsoft Edge WebView2 Runtime
- Internet access for the first launch and dependency updates

## Start the application

Run:

```text
py DayZ-ServerMan.py
```

The first launch creates `.venv` beside the starter. It then installs the
pinned packages from `requirements.txt` and restarts with the local Python.
Later launches reuse this environment.

Do not copy `.venv` between computers. You can delete it if dependency setup
becomes damaged. The next launch creates it again.

One window can be open for each copy of DayZ-ServerMan. A second window, or a
window while a command changes something, shows a message and closes. To run
commands from a terminal, see [Command line](#command-line).

<!-- shot:readme-01 -->
![Overview with a stopped server, Start server, and the status list for mods, DayZ server, last backup, and schedule](img/readme-01.png)

## First setup

1. Open **Settings**.
2. Select the folder that contains the DayZ server installation.
3. Select the folder that contains `steamcmd.exe` and its Steam library.
4. Keep the portable backup destination or select a custom folder.
5. Select **Save locations** and review the status of each folder.
6. Create, import, or restore a server profile.

<!-- shot:readme-02 -->
![Settings showing the saved DayZ server and SteamCMD folders, each with a Ready status, and the first path resolved from them](img/readme-02.png)

The manager derives these paths from the selected folders:

- `DayZServer_x64.exe`
- `steamcmd.exe`
- `steamapps/workshop/content/221100`

The DayZ server, SteamCMD, backup folder, and manager can be in unrelated
locations. While the form holds an unsaved change, the saved folder that is
still in use shows **In use until saved**. The saved backup destination
carries the **In use** mark.

### Update checks

**Settings → Update checks** has one switch: **Check Steam for mod and server
updates automatically**. It is on by default and saves at once.

<!-- shot:readme-11 -->
![Update checks panel in Settings with the automatic check switch on and the text that says what is sent to Steam](img/readme-11.png)

While the switch is on, DayZ-ServerMan makes two checks:

- **Mods.** It asks the Steam Web API whether your Workshop mods have a newer
  version. Only the Workshop item numbers are sent. No Steam key or sign-in is
  used. Steam also receives the IP address of this computer, as with any
  internet request.

  This check runs at start, on **Mods** open, when you select another server,
  after a task on **Mods**, and when you turn the switch on.
  It is skipped when the last check is less than 5 minutes old, unless a mod
  has no answer yet. It also happens 30 minutes after the last check.
- **DayZ server build.** SteamCMD signs in anonymously and reads the public
  app information of the DayZ server (app 223350). This happens at start,
  unless a check ran in the last 10 minutes, and then every 6 hours. Your
  Steam account is not used. No file of the DayZ server changes. SteamCMD
  updates files in its own folder only.

Neither check downloads or applies anything. If you turn the switch off, no
automatic check runs. **Check now** still checks. **Update all**, **Update &
start**, and **Update & restart** still ask Steam first and send the same
Workshop item numbers.

## Edit network ports

Edit existing ports under **Configuration → Network**. The game port and Steam query port appear together.
Edit both ports and any other configuration fields, then use the existing **Review changes** and **Apply changes** actions below.
The review shows all old and new values. **Discard changes** discards the complete draft. Ports take effect at the next server start; this does not restart the server.

## Create a server profile

Open **Profiles** and select **New profile**. Enter a display name, game port,
and mission. The profile ID is generated from the display name and can be
changed before creation.

The mission list comes from the configured DayZ installation's `mpmissions`
directory. Select **Custom mission…** to enter an installed custom mission such
as `mpmissions\MyMission.chernarusplus`. The directory must already exist
inside the configured DayZ installation. Profile creation does not download
mission files or install required mods.

DayZ-ServerMan creates these operational files inside the DayZ installation:

```text
serverman\<profile-id>\serverDZ.cfg
serverman\<profile-id>\profile\
```

Application source, Python packages, settings, and backups are not placed in
that directory. The generated configuration uses safe initial values and can
be adjusted from **Configuration** after creation.

Deleting a manager-created profile removes its generated files and exclusive
world storage. A restored isolated mission is removed only when ownership is
proven and no other profile references it. Shared missions and backup archives
remain preserved.

When creation succeeds, the new profile is selected and remembered. Select
**Go to Overview**, then **Start server**. A “ready to start” result confirms
that the manager can resolve the executable, config, runtime directory,
mission, and configured mod paths. It cannot guarantee that a custom mission's
internal files or mod dependencies are correct.

<!-- shot:readme-03 -->
![New server profile form with the sample name Sakhal PvE, the generated profile ID, game port 2502, and a mission](img/readme-03.png)

## The sidebar and the workspaces

The sidebar holds the one profile selector. Below it, a line shows the state
of the DayZ server in this installation, for example **Ready** or
**Stopped**. Changing the profile does not change the current page.
DayZ-ServerMan remembers the selected profile.

If the running server was started with another profile, the sidebar names it,
for example **Running: Livonia Test**.

<!-- shot:readme-04 -->
![Sidebar with the profile selector, the Ready state, the three navigation groups, and a badge with the number 4 on Mods](img/readme-04.png)

The workspaces are in three groups:

- **Operate**
  - **Overview** shows the server and its controls, then the status of
    updates, backups, and the schedule.
  - **Mods** checks, downloads, verifies, and applies Workshop mods.
  - **Backups** creates verified ZIP backups and provides reviewed restores.
  - **Logs** shows concise manager activity and captured DayZ server output.
    Routine interface polling is excluded. Select **Manager diagnostics** only
    when you need the underlying structured troubleshooting records.
- **Server setup**
  - **Profiles** defines the launch command, runtime profile, and ordered mods.
  - **Configuration** edits the selected server's core configuration.
  - **Tweaks** edits gameplay, economy, weather, events, spawn, and population
    data.
- **Application**
  - **Settings** sets the application folders, the backup destination, the
    update checks, and the legacy import.

A badge on **Mods** counts the mods that need you: updates available,
downloaded but not applied, and not downloaded. The DayZ server build is not
counted. In a narrow window, the sidebar opens from the menu button, and the
badge shows beside the page title.

## Overview

The Overview has two parts: the server strip and the status list.

**The server strip** shows the server state once, with a short explanation:

- **Starting** means that the managed DayZ process exists, but it is not
  **Ready** yet.
- **Ready** means that the process answers the Steam query, and its log file
  shows that the mission accepts players.
- **Not responding** means that the process exists, but it is not **Ready**
  after two minutes. **Save & Stop** and **Save & Restart** stay available.
- **Running outside the manager** means that a DayZ server runs that
  DayZ-ServerMan did not start. Stop it where you started it.

While the server runs, the strip shows the uptime, the process ID, and the
Steam query port. While it does not run, **Process details** shows the same
facts. If DayZ-ServerMan finds a problem with the process, a note in the strip
says so.

<!-- shot:readme-05 -->
![Server strip with Ready, Players 18 / 60, the uptime and process facts, Backup after stop, and the three lifecycle buttons](img/readme-05.png)

**The status list** has one row for each of these items:

| Row | What it shows | Action |
|---|---|---|
| Mods | Updates available, downloaded but not applied, not downloaded, and the last check. **Check now** checks the mods and the DayZ server build. | **Open Mods** |
| DayZ server | The installed build, the newest build on Steam, and what to do. See [DayZ server build](#dayz-server-build). | none |
| Last backup | The newest backup, whether it can be restored, and its size. | **Open Backups** |
| Next scheduled action | The daily action, the next run, and the result of the last run. | **Set schedule** or **Change schedule** |

A notice above the strip tells you when the setup is not complete, when no
profile exists, or when changes are blocked. It also tells you when the running
server was started with another profile.

### Players online

While the server runs under the manager, the strip shows the player count, for
example **Players 18 / 60**. A full server also shows **Full**. The count comes
from the Steam query that the readiness check already sends. While the server
is **Starting**, no count shows. **Players not known** means one of these:

- the server is **Not responding**, or it stopped answering;
- its answer did not include a valid count;
- it runs outside the manager and does not match the selected profile (see
  below).

Select the count to open the list of players. It shows each name and the time
connected, sorted by name. The list refreshes every 10 seconds while it is open
and the Overview is visible.

DayZ can send the list without names. Then a row shows **Player 1**,
**Player 2**, and so on, with the time connected. The longest-connected player
is first. When no row has a name, the list says **This server does not share
player names. Connection times are shown.** If the server sends no list while
the count shows players, the count stays and the list says that names are not
available.

<!-- shot:readme-09 -->
![Overview with the Players online list open, showing six fictional player names with their connected time and the privacy note](img/readme-09.png)

Player names are shown in this window only. DayZ-ServerMan does not log or
save them.

A server can run outside the manager. Then DayZ-ServerMan asks the query port
of the selected profile at each status update. The count shows only when the
answer carries the server name and the game port of that profile. Then the
list of names also opens. Otherwise the strip shows **Players not known**. The
server still counts as running outside the manager, and its controls do not
change.

## Stop, restart, and automatic backup

**Save & Stop** requests a normal Windows close and waits for the managed DayZ
process to exit. **Save & Restart** uses the same stop process before starting
the selected profile again.

Select **Backup after stop** in the server strip to create a verified backup
after DayZ stops. The setting is stored separately for each profile.

For restart, the order is stop, backup, then start. If backup creation fails,
the server remains stopped and the manager does not restart it.

**Backup after stop** requires a runtime profile directory in the selected
profile.

**Save & Stop** and **Save & Restart** act on the selected profile. If the
running server was started with another profile, both are locked. Select the
running profile in the sidebar to stop or restart it.

## Daily schedule

The **Next scheduled action** row can schedule one daily **Save & Stop** or
**Save & Restart** action.

1. Select **Set schedule** or **Change schedule**.
2. Enter the hour and minute in local time.
3. Select one action.
4. Select **Save schedule**.

To disable the schedule, clear both actions and save. **Cancel** closes the
editor and keeps the saved schedule.

<!-- shot:readme-06 -->
![Schedule row with Save & Restart daily at 04:00 and the open editor with Hour, Minute, the two actions, and Save schedule](img/readme-06.png)

Schedules are stored separately for each profile. They run only while the
DayZ-ServerMan window is open. Commands do not run schedules (see
[Restart without the window](#restart-without-the-window)). If the manager starts after today's scheduled time, it
waits until the next day. If the selected server is not running under this
manager at the scheduled time, the manager records a skipped run.

## Mods and updates

The Mods page shows the Steam sign-in, the update state, and the configured
mods of the selected profile.

<!-- shot:readme-07 -->
![Mods page with the sign-in line, the update summary, Check now, Verify files, Update & start, Update all, and mods in different states](img/readme-07.png)

### Steam sign-in

When sign-in is set, it shows as one line, for example **Steam sign-in:
account example_operator**. Select **Change** to edit the sign-in mode and
account name. Enter passwords and Steam Guard codes only in the visible
SteamCMD window. DayZ-ServerMan does not ask for them or store them.

### Mod states

The header shows a summary and the time of the last successful check. Select
**Check now** to check the mods again. On this page, **Check now** checks the
mods only. Each mod has one state:

| State | Meaning |
|---|---|
| Current | Steam has no newer version, and the server folder holds this version. If no DayZ server folder is set, or it cannot be read, the server copy is not checked. |
| Update available | Steam has a newer version. The row shows the installed and the Steam date. |
| Downloaded - not applied | The download is not yet copied into the server folder, or the server copy was never verified. |
| Not downloaded | The mod is not in the SteamCMD Workshop folder. |
| Installed | The mod is downloaded, but DayZ-ServerMan cannot confirm that it is current. |
| Could not check | No fresh answer from Steam. The row gives the reason and the last successful check. |
| Checking… | The first check is still running. |
| Local | The mod is not managed through the Workshop. |
| Unavailable | The Workshop folder is not set or cannot be read. |
| Downloading, Failed | The state of a mod during or after an update. |

**Could not check** has one of these reasons:

- the check failed;
- Steam does not list the item;
- the last check is too old;
- no check ran yet;
- automatic checks are off.

A mod is never shown as **Current** without a fresh answer from Steam.

### Update all

**Update all** brings every Workshop mod of the profile up to date.

1. DayZ-ServerMan asks Steam again which mods changed.
2. SteamCMD downloads only the mods that changed or are missing. If nothing
   changed, SteamCMD does not start. If Steam cannot be asked, SteamCMD
   receives every mod.
3. DayZ-ServerMan checks the downloads.
4. A review lists the mod folders and key files that it will copy into the
   server folder. Confirm with **Apply mods and keys**.

The page says **All mods are current. Nothing to download or apply.** and opens
no review only in this case: SteamCMD did not run, every mod is recorded as
applied, and no key file is missing. In every other case the review opens. If
it copies nothing, it says **Nothing is written to the server folder**.
Nothing is copied without the review.

The operation bar and the rows show the progress. A row can show
**Checking…** until the update reaches it, then **Downloading** with a
percentage, **Waiting for download**, or **Verifying download**. During the
apply, a row shows **Applying to the server folder…**.

<!-- shot:readme-10 -->
![Mods page during Update all: the operation bar shows Updating mods, and rows show Downloading, Waiting for download, and Checking…](img/readme-10.png)

DayZ-ServerMan copies mods into the server folder only while the server is
stopped. If the server is not stopped, the review says that the mods cannot be
applied now. It names the server state and what to do, for example **Use
Update & restart, or stop the server first.** It offers only **Close**. The
downloads stay. A review that copies nothing can be confirmed in any server
state.

The review cannot see another DayZ-ServerMan that uses the same DayZ
installation. The apply finds it when you confirm, then stops and changes
nothing.

### Update & start and Update & restart

The second update button reads **Update & restart** while a server runs or
starts. In every other state, it reads **Update & start**. It works in two
cases:

- **Update & start** while the server is stopped. It updates the mods, opens
  the review, applies the mods, and then starts the server. The review opens
  also when every mod is current, to confirm the start.
- **Update & restart** while the server runs under the manager with the
  selected profile. After the review, it saves and stops the server and
  creates a backup if **Backup after stop** is set. Then it applies the mods,
  checks the server folder, and starts the server. The confirm button reads
  **Stop server, apply and restart**.

The download and the review happen while the server still runs. If a step
fails, the result says what happened. For example, a failed backup leaves the
server stopped and the mods not applied. The server is offline from the stop
until the start. If the apply stops so that the server folder cannot be
confirmed, changes are blocked (see
[When changes are blocked](#when-changes-are-blocked)).

If every mod is current, **Update & restart** does not stop the server. To
restart anyway, use **Save & Restart** on the Overview.

In all other cases the button is locked. Hover over the button to see why:

- The server starts or stops.
- The server runs outside the manager.
- The server runs with another profile.
- The server state is not known.

### Verify files

**Verify files** reads every file of the profile's Workshop mods and of their
copies in the server folder, and compares them. It can take minutes. It does
not change any file, and the server can keep running. Each mod with a problem
shows it in its row, for example **Server copy differs from the download**.

To save time, DayZ-ServerMan compares the recorded size and change time of each
file. It reads the full content of a mod only in these cases:

- the mod changed on Steam or in its folder;
- no record of the mod exists yet;
- it copies the mod into the server folder;
- you select **Verify files**.

A change that keeps the size and change time of every file is found only by
**Verify files**.

## DayZ server build

The **DayZ server** row of the Overview compares the installed build with the
newest build on Steam. It does not download or change the DayZ server.

When an update exists, the row tells you how to install it:

- If the DayZ server folder belongs to your Steam library, stop the server,
  then update “DayZ Server” through Steam.
- If SteamCMD installed it, stop the server, then update it with SteamCMD.
- If the installer is not known, update it with the program that installed it.

DayZ-ServerMan does not update the server build itself.

## The operation bar

Long tasks, such as an update, a backup, or a restore, show in a bar above the
page. The page stays visible. The bar shows the task name, the current step,
the progress, and **Cancel** where the task can stop safely.

A failed or cancelled result stays in the bar until you select **Dismiss** or
a newer failed or cancelled result replaces it. A cancelled result does not
replace a failure. A new task replaces a success result. **View in Logs**
opens the details.

While a task runs, buttons that change the server are locked. Hover over a
locked button to see why.

## Backups and restores

The default destination is the `backups` directory beside this README. A
custom destination can be selected in Settings. Keep a custom destination
outside the DayZ server folder. A mod apply, a profile restore, or a profile
deletion can replace or remove folders there, with the backups inside them.

Backup names use the profile ID and local creation time:

```text
chernarus_pve_2026-09-28_16-21-51.zip
```

Every new backup includes the server configuration, complete mission directory
(including `storage_<instanceId>` world and mod persistence), runtime profile,
complete profile definition and empty directories. Mod binaries are not included.
The manager verifies the archive before reporting success.

<!-- shot:readme-08 -->
![Backups page for Chernarus PvE with Create backup and three verified backups under Latest backups, each with a restore icon](img/readme-08.png)

### Restore an existing profile

1. Stop all DayZ servers in the configured installation.
2. Select the server profile in the sidebar, then open **Backups**.
3. Under **Latest backups**, select the restore icon on the required card.
4. Review the targets and recovery plan, then select **Restore this backup**.
5. Confirm with **Restore now**, then wait for completion.

The list shows only the three newest backups, newest first. A disabled restore
icon means that archive is incompatible; the card explains why. This limit does
not delete older ZIPs. Restoration uses the existing profile and does not create
another profile or start the server.

### Restore a deleted profile from a ZIP

1. Stop all DayZ servers in the configured installation.
2. Open **Backups → Restore profile from backup…**.
3. Select a full backup ZIP. The picker starts in the configured backup folder.
4. Review the archive summary and destination choices. Leave the ID and ports
   blank to suggest available values.
5. Select **Review restore** and check the proposed profile, paths and ports.
6. Confirm any explicit world replacement, then select **Restore profile**.
7. After success, check readiness and missing mods before starting the server.

This action works with no existing profiles. ZIPs elsewhere and renamed ZIPs
are supported. Browsing does not import or modify the source archive. Cancel
returns to Backups; an invalid or unsupported archive shows one explanation.
If the file changes after selection, browse again and prepare a new review.

Existing IDs are preserved. An occupied mission defaults to an isolated
mission and storage ID. Direct restoration copies only the selected world,
although the full mission archive can contain other worlds. Explicit world
replacement names every affected profile and retains a verified recovery copy.
The restored profile is selected and remembered after success.

Older archives without a complete profile definition cannot recreate a deleted
profile. They remain intact and can still restore an existing profile when
compatible. Create a new full backup to enable direct reconstruction.

## When changes are blocked

An interrupted restore, profile creation, or mod apply can leave the server
folder unfinished. At start, DayZ-ServerMan finishes or undoes such work, but
only while the server is stopped and no other DayZ-ServerMan uses the same
DayZ installation. Otherwise it changes nothing and blocks changes.

The Overview then shows **Recovery required** with a sentence that says what
happened and what to do. Usually: stop the server, then restart
DayZ-ServerMan. For an interrupted backup restore, you can also open
**Backups** again. The block reason is also in **Logs → Manager
diagnostics**. The repository's operations guide (`docs/operations.md`) lists
each case.

Three sentences say that a mod apply, a profile restore, or a backup restore
cannot be checked because no DayZ server folder is set. While only such a
block is set, **Settings** accepts one kind of save:

1. Open **Settings**.
2. Select the DayZ server folder that the interrupted work used. Do not change
   the SteamCMD folder or the backup destination.
3. Select **Save locations**. The page then says **Restart DayZ-ServerMan. It
   then checks the interrupted work in this DayZ server folder.**
4. Restart DayZ-ServerMan. For a backup restore, opening **Backups** also
   works.

The save is refused if the folder does not hold the DayZ server program. It is
also refused if an interrupted restore used another folder. Nothing is saved
then, and you can select another folder. Other changes stay blocked until the
restart.

If you selected the wrong folder for an interrupted mod apply, the next start
can block with **could not be undone safely**. Then close DayZ-ServerMan and
set the correct folder by hand in `config/manager.json`, as the operations
guide describes.

Keep verified recovery copies until they are no longer needed. Restore
journals remain in `data/operations/`.

## The mission map editor

The map editor sets loot tiers, container capacity, and infected encounters of
a mission on a map. It changes no mission file until you apply the plan. The
window steps also exist as commands; see [Map commands](#map-commands).

### Open the editor

1. Select the server profile in the sidebar, then open **Tweaks**.
2. Select **Open map editor**. It opens a separate window for this profile.

Each profile has one editor window. If it is open, the button focuses it and
shows **The map editor of this profile is already open.**

### Editor workspace

The menu band contains **Plan**, **View**, **Map setup**, **Apply**, and **Restore**.
The editing band groups Select, Create, Inspect, and History with labelled icons. Tool settings appear beside the active tool.
Finish creates and selects the polygon. Its properties appear in the right inspector.

Authored areas retain a light fill and hatch when deselected. Original-tier opacity affects only the original overlay; Inherit areas use a neutral pattern.

Object rows show names, visibility and locks. Select an object to reveal its priority controls. Capacity guidance is available under **Capacity limits and loot supply**.

- **View → Objects & layers** opens the left panel. It contains object visibility, locks, priority, and original tiers. The tier opacity slider changes in 10% steps. Global container capacity is under **Plan settings**.
- **View → Properties** opens the selected-object inspector. **View capacity report** opens the report for that selection.
- **View → Reports** opens the bottom panel. Building capacity uses one row per source entry. Expand **Details** for every container, limit, warning, and identifier. Search filters the current answer; it does not read mission files again. Group ceilings apply per group, regardless of placement count.
- **Map setup** contains terrain bounds and the map image. Setup closes before calibration so you can pick points on the map. It returns after calibration ends.
- **Plan** contains Export, Import, and Copy from another profile. Their existing checks and confirmations still apply.

**View** also contains Fit map, Zoom to selection, Full screen, and Coordinates. Escape closes View and returns focus to its button. Finish and Discard appear while an outline is unfinished.

Drag a panel border to resize it. Focus the border and use arrow keys for 10-pixel steps, or Shift with an arrow for 1 pixel. Home and End use the available limits. Escape cancels a resize.

Explicit panel sizes and open states are remembered across editor restarts. At narrow widths, one auxiliary panel opens at a time. Closing a panel gives its space back to the map.

A layout preference failure leaves editing available. Use **Retry** beside the layout notice to try saving again.

### Terrain bounds and coordinates

A plan needs confirmed terrain bounds. The editor reads the bounds of the
terrain when it can. Select **Confirm detected bounds** to accept them. If no
bounds are detected, enter the actual bounds in native metres and select
**Confirm and save manual bounds**. Without confirmed bounds, the editor has
no plan yet, and Apply cannot write loot tiers.

The editor works without a map image. It then shows the plain terrain. The map
has a grid of 0 to 10 on each axis and the native X and Z in metres. Choose
**Grid**, **Native**, or **Both** under **Coordinates**. Distances are in
metres.

- Zoom with the mouse wheel. Pan with a middle-button drag or Space and drag.
- **Fit map** shows the whole terrain. **Zoom to selection** (F) shows the
  selected item.
- Zoom and pan never move a placed item.

### Zones, tiers, and container capacity

Choose a tool in the top toolbar: **Select** (V), **Circle** (C), **Polygon**
(P), **Encounter** (E), or **Inspect** (I).

- A circle has a centre and a radius in metres.
- A polygon needs 3 points. Press Enter to finish it. Press Backspace to
  remove the last point.
- **Objects in priority order** lists the zones. The highest zone wins where
  zones overlap. A new zone goes on top. Use **Move up** and **Move down** to
  change the order.
- A zone has a name. **Visible** hides it on the map only. A hidden zone still
  counts in the mission settings. **Locked** stops edits until you unlock it.
- Undo and Redo use one history for the whole plan: Ctrl+Z, and Ctrl+Y or
  Ctrl+Shift+Z.

Each loot zone has a **Tier** or **Inherit**. The tiers are the tiers of the
tier map of the mission. **Original tiers** shows the tiers that the mission
has now. It is a display only and changes nothing. Each original tier row has an eye button. Tier1 starts hidden; enable its eye to show the grey tint. A separate **Unassigned** eye shows cells with no tier flags in grey and also starts hidden. It uses the original overlay opacity and creates no tier assignment.

**Zone capacity** has four presets: Sparse -25%, Normal 0%, Rich +50%, and
Very Rich +100%. New loot zones start at Rich. **Global container capacity**
goes from -50% to +100% in 25% steps. The global value and the value of the
winning zone add up, against the original capacity.

Select **Inspect** and click a point. It shows the original tier, the
effective tier, the winning zone, the building capacity, and the covering
encounters.

Drag the selected object to move it, including from near a vertex or radius handle.
Hold **Ctrl** to show handles, then Ctrl+drag a handle to edit one vertex or resize a circle.
The action is chosen when the drag starts. Releasing Ctrl hides handles while the current drag keeps its original action.
Escape, a cancelled pointer, or leaving the window cancels the preview without changing geometry.

**Custom layer opacity** fades authored interiors in 10% steps. It preserves borders, red selection, and handles at 0%.
The original-tier slider is independent. Selection uses a deep red outline with a thin white edge.
These layer display choices last for the current editor window and do not edit the plan.

A table of vertex coordinates is not available yet.

### Map image

A map image is optional. DayZ-ServerMan ships no map images. Choose a PNG or
JPEG file that you have the right to use. The editor shows images up to 8,192
pixels on each side and 32 MiB. The editor does not load online map tiles.

Select **Calibrate with three points** to fit the image to the terrain. Or
select **Fit to terrain bounds**. **Dim image** makes the image fainter. A plan
export and a profile backup carry no image file.

### Save, export, import, and copy a plan

The editor saves the plan for each profile by itself. The window shows
**Saving**, **Saved**, or **Not saved** with the reason. **Draft changes**
shows the changes since the last applied configuration.

- If the saved plan changed, **Reload plan** loads it and discards your unsaved
  changes. To keep them, select **Export plan** first.
- If the saved plan is not valid, the editor is read only. **Set aside invalid
  plan** keeps the file under a dated name and starts a new, empty plan.
  Nothing is deleted.
- When you close the window with unsaved changes, it asks what to do with them.

The **Plan file** menu has three buttons:

- **Export plan…** writes the plan you see to a JSON file that you choose.
- **Import plan…** reads a JSON file. It shows a compatibility preview. You
  choose **Replace** or **Merge**. There is no default.
- **Copy from another profile…** shows a list of profiles. It reads the saved
  plan of the profile you choose. That plan is not changed.

An import or copy lists the items that it does not import. It can ask for a
placement review, for example when the terrain differs. You confirm both with
check boxes. The result is one save and one undo step.

The import also reads a plan export of the earlier Pripyat prototype, one time.
It uses the same preview. The zones keep their positions. Items that
DayZ-ServerMan cannot map, for example bandit points, are listed and need your
confirmation. The import writes no game file.

**Import plan** and **Copy from another profile** are not available in these
cases. The plan loads, it is read only, autosave is stopped, or a map
operation runs. They also wait until you confirm the bounds. **Export plan**
needs only a loaded plan and no other file dialog or file read. It also works
when the plan is read only, autosave is stopped, or a map operation runs.

### Preview, Apply, and Restore

**Apply…** shows a preview first. It uses only the saved plan, so the editor
saves the draft before it opens the preview. The preview lists the plan
changes, the encounters, the loot report, and the mission files that change.

- Stop the DayZ server before you apply. DayZ-ServerMan does not apply while
  the server runs.
- A mission file can change outside the map editor. The preview then lists the
  file. Choose **Keep the change** or **Replace the file** for each file.
  DayZ-ServerMan saves a copy of the current file before it replaces the file.
- Some encounters cannot be written on a mission. Exclude each one from this
  Apply to continue.
- A refusal is listed under **Apply is not possible**. Fix the cause, then
  select **Preview again**.
- Apply changes the mission files for every profile that uses the mission
  folder.

DayZ-ServerMan keeps a protected copy of the original files. **Restore
previous state…** returns to the previous state. **Restore original…** returns
to the original files. Both need a stopped server, and both show a preview
first. **Restore original** needs a supported terrain family; see
[Supported terrain families](#supported-terrain-families).

While Apply or a restore runs, editing and autosave are paused. Closing the
editor does not stop the operation.

If the editor shows **Adopt mission…**, the mission moved, was copied, or lost
its last Apply record in a restore. Adoption moves or copies the editor
records. It writes no mission file and needs no stopped server.

An interrupted Apply or restore is finished or undone at start, as for other
interrupted work; see [When changes are blocked](#when-changes-are-blocked).
The editor offers **Check again**, and `map recover` does the same.

### Native limits

Percentages change the native capacity of containers, not the item supply.
More capacity can leave the live loot amount unchanged. Fixed dispatch loot is
unchanged. The preview shows the supported capacity changes and the native
limits. It never promises a live item count.

- A value above the native limit of a group is refused. The preview shows the
  limit.
- Several categories can share one capacity. It is an upper limit for all of
  them together.
- If no item type with a nominal above 0 has a category, more capacity cannot
  add items.
- A container with fewer points than its capacity is limited by its points.
- The map editor never writes `db/types.xml` or the `mapgroupcluster` files.

### Supported terrain families

Apply and Restore original work only for terrain families that passed a test
with a DayZ server. The family is the part of the mission folder name after the
last dot, for example `chernarusplus` in `dayzOffline.chernarusplus`. Upper and
lower case do not matter. A custom mission of a supported family is supported.
Its own checks still apply.

| Terrain family | Server build of the probe | Tested |
|---|---|---|
| `chernarusplus` | 1.29.163709 | Loot tiers, container capacity, medical toggle, infected |
| `Pripyat` | 1.29.163709 | Same, with the limits below |
| `enoch` | 1.30.164078.38 | Loot tiers, container capacity, medical toggle |

**sakhal is not supported yet.** Apply and Restore original refuse on sakhal
and on every other family that is not in the table. Nothing is written. A
folder name without a family after a dot gets the same refusal. On every
family you can still plan, save, import, export, and inspect. Restore previous
state is not limited.

Limits of the tests:

- The tests prove that the DayZ loot economy loads the changed files. They do
  not prove live loot amounts.
- Fixed dispatch items are not governed by the capacity. A group with capacity
  0 can still show its dispatch parts.
- `chernarusplus`: DayZ server 1.30 changed three of its files after the test.
  The writer forms loaded on 1.30 on `enoch`. The changed files stay under the
  checks of each mission.
- `Pripyat`: the test used the installed medical state, where off equals on.
  It used global capacity 0 only, because two groups refuse any other global
  value until you remove the duplicates outside the editor. Only the terrain
  mods were loaded.
- `enoch`: no mission with a stored medical baseline was tested. The group
  `Land_Underground_Storage_POX2` has a point without range and height. A zone
  over it adds one more group with wrong points to the count of DayZ.
- A generated copy of a group keeps the points of its source group. A source
  point without range or height therefore also counts in the copy.

### Infected, bandits, wolves, and bears

- **Infected on chernarusplus and Pripyat.** DayZ accepted the territory
  element of the editor, and infected appeared in the editor zones, on DayZ
  server 1.29.163709. The preview shows a notice when `ZombieMaxCount` or
  `ZoneSpawnDist` is 0. DayZ-ServerMan does not change these values.
- **Infected on enoch.** On enoch you can apply infected encounters, but no
  test with a DayZ server covered them on enoch. They were tested on
  chernarusplus and Pripyat.
- The editor offers an infected event only when `cfgenvironment.xml` registers
  `env/zombie_territories.xml`.
- **Bandits.** Bandit placement is not available: no verified ground height
  source. The editor creates no AI_Bandits encounter.
- **Wolves and bears.** Wolf and bear territories are not supported in this
  version; the pack territory mapping is not verified.

### Backups with editor records

A profile backup carries the editor plan and the editor records of the active
mission. A restore and a profile delete keep these records in step with the
mission.

Older builds cannot restore archives that hold editor records. A profile
backup gets the new archive format (schema 4) only when the profile has an
editor record for its active mission. Other backups keep the old format and
stay readable by older builds. An older build refuses a schema 4 archive.
Restore such an archive with this build or a later one.

## Command line

Every action of the window is also a command for a terminal or a script. The
commands use the same checks, reviews, and wording as the window. They never
open a window.

### Run a command

```text
py DayZ-ServerMan.py --cli <noun> <verb> [options]
```

`--cli` must be the first argument. The first run can create `.venv`, as the
first window start does. Its setup messages go to the error output (stderr).

- `--cli help` lists the commands and the exit codes.
- `--cli help <command>` or `--cli <command> --help` shows the options of one
  command. Help changes nothing.

When a message names a command, type it after `DayZ-ServerMan.py --cli`. For
example, the message **Run help updates check for the commands and options.**
means:

```text
py DayZ-ServerMan.py --cli help updates check
```

Options that many commands share:

| Option | Meaning |
|---|---|
| `--profile ID-OR-NAME` | The profile, by its ID or its display name. |
| `--json` | Print one JSON document instead of text. |
| `--yes` | Answer yes to the question. The review and the checks still run. |
| `--expect-profile-revision N`, `--expect-settings-revision N` | Refuse when the revision is not this number. A script can pin a state that it read before. |

### Commands

The commands follow the groups of the window. A command that reads runs at
any time, also while the window is open. A command that changes something
needs that no other DayZ-ServerMan is active (see
[One manager that changes things](#one-manager-that-changes-things)).

| Window page | Command | Changes something | Asks first |
|---|---|---|---|
| Overview | `status` | no | no |
| | `server status`, `server players [--names]` | no | no |
| | `server start [--wait-ready] [--ready-timeout SECONDS]` | yes | yes |
| | `server stop`, `server restart` with `[--backup-after-stop \| --no-backup-after-stop]` | yes | yes |
| | `profile backup-after-stop on\|off` | yes | no |
| | `schedule show` | no | no |
| | `schedule set HH:MM stop\|restart`, `schedule clear` | yes | no |
| | `updates status` | no | no |
| | `updates check [--scope mods\|server-build\|all] [--force]` | yes (the check results) | no |
| Mods | `mods list` | no | no |
| | `mods verify` | yes (the content records) | no |
| | `mods update [--start \| --restart] [--backup-after-stop \| --no-backup-after-stop]` | yes | yes, after the review |
| | `steam show` | no | no |
| | `steam set --mode account\|anonymous [--account NAME]` | yes | no |
| | `steam login` | yes | needs a terminal |
| Backups | `backup list [--all]` | no | no |
| | `backup create` | yes | yes |
| | `backup restore BACKUP-ID` | yes | yes, after the review |
| | `backup recover` | yes | no |
| | `profile restore --archive ZIP [--profile-id ID] [--name NAME] [--game-port PORT] [--query-port PORT] [--storage preserve\|new\|replace] [--overwrite]` | yes | yes, after the review |
| Logs | `logs [--source manager\|diagnostics\|server] [--lines N]` | no | no |
| Profiles | `profile list`, `profile show`, `profile command`, `profile missions` | no | no |
| | `profile create`, `profile edit` with `--set KEY=VALUE` or `--from-file FILE` | yes | no |
| | `profile delete` | yes | yes |
| Configuration | `config show --target server\|gameplay` | no | no |
| | `config set --target server\|gameplay` with `--set` or `--from-file` | yes | yes, after the review |
| Tweaks | `tweaks show --target economy\|weather\|spawnable-damage\|starter-loadout\|events` | no | no |
| | `tweaks set --target ...` with `--set` or `--from-file` | yes | yes, after the review |
| | `tweaks convert-loadout` | yes | yes |
| | `tweaks medical show` | no | no |
| | `tweaks medical set FEATURE on\|off` | yes | yes, after the review |
| Settings | `settings show` | no | no |
| | `settings check-path PATH --role dayz-root\|steamcmd-root\|backup-root` | no | no |
| | `settings set [--dayz-root PATH] [--steamcmd-root PATH] [--backup-root PATH \| --default-backup-root]` | yes | no |
| | `updates auto on\|off` | yes | no |
| | `migrate preview ROOT` | no | no |
| | `migrate apply ROOT [--item ID ... \| --all-items]` | yes | yes, after the review |
| Map editor (Tweaks) | `map show`, `map inspect [--point X,Z \| --object ID]` | no | no |
| | `map preview` | no | no |
| | `map save --from-file FILE [--expect-revision N]` | yes | no |
| | `map set-aside` | yes | yes, after the review |
| | `map apply [--keep-change PATH] [--replace-file PATH] [--exclude-encounter ID]` | yes | yes, after the review |
| | `map restore original\|previous` | yes | yes, after the review |
| | `map recover` | yes | no |
| | `map export --output FILE [--overwrite]` | no (it writes the file that you name) | no |
| | `map import --from-file FILE --mode replace\|merge [--confirm-listed] [--expect-revision N]` | yes | yes, after the review |
| | `map copy --from-profile ID-OR-NAME --mode replace\|merge [--confirm-listed] [--expect-revision N]` | yes | yes, after the review |
| | `map adopt` | yes | yes, after the review |

`--set KEY=VALUE` changes one value; repeat it for more values. The keys are
the names that the matching `show` command prints in its **Key** column.
`--from-file` reads a UTF-8 JSON file with the changes. With both, the file
comes first, and each `--set` replaces its key.

### Map commands

The `map` commands work on the map plan and the mission of one profile. They
use the same checks and texts as the map editor window. `--cli map --help`
lists them. `--cli help map <command>` shows the options of one command.

| Command | What it does |
|---|---|
| `map show` | Shows the saved map plan of a profile and the state of its mission. |
| `map inspect` | Shows the loot tier and the building capacity at one point, or for one object. |
| `map save` | Saves a map plan from a file over the saved map plan. |
| `map set-aside` | Keeps an invalid saved map plan under a dated name and starts a new, empty plan, after a review. |
| `map preview` | Shows what Apply would change in the mission files. Nothing is changed. |
| `map apply` | Applies the saved map plan to the mission files, after a review. |
| `map restore` | Restores the original mission files or the previous state, after a review. |
| `map recover` | Finishes or undoes an interrupted mission map change. |
| `map export` | Writes the saved map plan to a file. |
| `map import` | Replaces the saved map plan with a plan from a file, or merges the two, after a review. |
| `map copy` | Replaces the saved map plan with the plan of another profile, or merges the two, after a review. |
| `map adopt` | Moves or copies the map editor records of a mission that moved, was copied or was restored, after a review. |

Options of the map commands:

| Option | Used by | Meaning |
|---|---|---|
| `--point X,Z` | `map inspect` | A point in native metres, for example `7500,7500`. For a negative number, write `--point=-5,3`. |
| `--object ID` | `map inspect` | A plan object, by its ID as `map show` lists it. Use `--point` or `--object`. |
| `--from-file FILE` | `map save`, `map import` | A UTF-8 JSON file that holds the map plan. |
| `--expect-revision N` | `map save`, `map import`, `map copy` | Refuse when the saved map plan is not at this revision. |
| `--keep-change PATH`, `--replace-file PATH` | `map apply` | The choice for one mission file that changed outside the map editor. Use the path as the review prints it. Repeat the option for more files. |
| `--exclude-encounter ID` | `map apply` | Leave one encounter out of the Apply. Use the ID as the review prints it. Repeat the option for more encounters. |
| `original` or `previous` | `map restore` | What to restore. |
| `--output FILE` | `map export` | The file to write. |
| `--overwrite` | `map export` | Replace the file when it exists. |
| `--mode replace\|merge` | `map import`, `map copy` | How the plan joins the saved plan. There is no default. |
| `--confirm-listed` | `map import`, `map copy` | Confirm the objects and items that the review lists as not imported, and the placement review. |
| `--from-profile ID-OR-NAME` | `map copy` | The profile whose map plan is copied. |

Notes on the map commands:

- `map show`, `map inspect`, and `map export` run at any time, also while the
  window is open. The other map commands refuse while the window or another
  command that changes something is active (exit code 3, `INSTANCE_ACTIVE`).
  This includes `map preview`, which changes nothing.
- `map show`, `map inspect`, `map preview`, and `map export` use the selected
  profile when you give no `--profile`. The other commands that name a profile
  need `--profile` when more than one profile exists. `map recover` names no
  profile.
- `map apply` and `map restore` need a stopped server. While the server runs,
  they ask the question first. Then they refuse with exit code 3 and change
  nothing.
- `map restore original` refuses with exit code 3 on a terrain family that is
  not supported; see
  [Supported terrain families](#supported-terrain-families). Nothing is
  written.
- `map adopt` writes only manager records. It needs no stopped server.
- `map recover` ends with exit code 0 when no unfinished change is left. It
  ends with exit code 3 when another operation runs or the server is not
  proven stopped. Any other block ends with exit code 6.
- `map export` writes only the file that you name. It checks the path first.
  A missing folder, a folder or link as the file, and a path in a folder that
  DayZ-ServerMan uses end with exit code 2. If the write or the read-back
  check fails, the command removes the new file and ends with exit code 1.
- `map copy` ends with exit code 2 when the source profile is the profile you
  copy to.

### Which profile a command uses

- `--profile` takes the profile ID or the display name. The display name
  ignores upper and lower case.
- A command that reads uses the profile that is selected in the window, or
  the only profile. Its output names the profile first.
- A command that changes something needs `--profile` when more than one
  profile exists.
- A command never changes the profile that is selected in the window.

### Questions, reviews, and --yes

A command asks before it acts where the window asks. The table above shows
these commands. The command prints the review first, then asks
`Continue? [y/N]`. Only `y` or `yes` continues. Any other answer, an empty line,
or Ctrl+C changes nothing and ends with exit code 4.

`--yes` answers the question. The command still computes and prints the
review, and every check still runs.

When the current state does not allow the action, the command refuses before
the question. For example, `server start` while the server runs ends with exit
code 3, and a recovery block ends with exit code 6. Some checks can only run
inside the operation, for example whether another DayZ-ServerMan uses the DayZ
installation. Such a check can refuse after the question.

Notes on single commands:

- `backup restore` lists every file that the restore replaces or creates, as
  the window does. This list can have several hundred lines. The warning and
  the question come last. A script reads the review from `--json`.
- `backup restore` and `profile restore` need a stopped server. While the
  server runs, they refuse with exit code 3 and change nothing. Stop the
  server first.
- `profile restore` that replaces an existing world also needs `--overwrite`.
  Without it, the command prints the review and ends with exit code 4, also
  with `--yes`.
- `mods update` downloads first, then shows the review. If you decline, or
  the state does not allow the apply, the output ends with **The mods are
  downloaded; nothing was applied.** A later `mods update` or the window
  applies them after a new review.
- `map apply` needs a choice for each mission file that changed outside the
  map editor (`--keep-change` or `--replace-file`) and for each unavailable
  encounter (`--exclude-encounter`). Without it, the command ends with exit
  code 4, also with `--yes` and in a terminal. `map import` and `map copy` need
  `--confirm-listed` when the review lists objects that are left out, items
  that are not imported, or a placement review. `map export` needs
  `--overwrite` to replace an existing file. `--yes` never makes a choice and
  never confirms the listed items.
- `migrate apply` imports exactly the items that you name with `--item`, or
  nothing. Without `--item`, it imports every item that can be imported.
  `migrate preview` shows each item and why it cannot be imported.

### Scripts, terminals, and --json

- A command never waits for an answer when its input is not a terminal, for
  example in Windows Task Scheduler. Without `--yes`, it prints the review
  and ends with exit code 4. Nothing changes.
- With `--json`, a command never asks, also in a terminal. Without `--yes`,
  it ends with exit code 4 and the error code `CONFIRMATION_REQUIRED`. The
  review is in `error.details.review`.
- `mods update --json` without `--yes` downloads the mods and returns the
  review, then ends with exit code 4. A script can use this to read the plan
  without an apply.
- `steam login` needs a terminal and text output. SteamCMD asks for the
  password and the Steam Guard code in this terminal. DayZ-ServerMan does not
  read or store them. Without a terminal, or with `--json`, it ends with exit
  code 4 (`NOT_INTERACTIVE`); `--yes` does not change this.
- `server players` shows player names only when the output is a terminal.
  For redirected output and for `--json`, add `--names`. Player names are
  never logged or saved.

### Output

Text output: the result goes to the standard output (stdout). Progress,
notes, and errors go to the error output (stderr).

A long operation ends with one line on stderr. The result on stdout can repeat
it. For example, **All 6 mods verified; server copies match** shows on both.
This is intended: the stderr line is progress, and stdout holds the result. A
script reads stdout only.

With `--json`, stdout holds exactly one JSON document, also when the command
fails:

```text
{"cli_version": 1, "command": "server start", "success": true, "value": {...}}
{"cli_version": 1, "command": "server stop", "success": false,
 "error": {"code": "INSTANCE_ACTIVE", "message": "...", "retryable": true, "details": {...}}}
```

- A read gives the value of the window's request. A value that names no
  profile gets `profile_id`.
- `status` gives `snapshot`, `server`, `updates`, `pending_recoveries`, and
  `profile_id`.
- A change gives `{"operations": [...], "review": ..., "result": ...}`. Each
  operation is the record of a finished step.
- After `server start`, the server state is in `value.result.state`, for
  example `RUNNING_MANAGED`. With `--wait-ready`, `value.result` holds the
  last status read.
- In an error, `details.operation` holds the record of a failed, cancelled,
  or recovery-required operation.
- New fields can appear. `cli_version` changes only when fields are added.

> **Warning:** `--json` prints stored values as they are stored. The JSON of
> `config show` and `config set` holds the server password and the admin
> password in clear text. Text output hides them. Do not send JSON output to
> shared logs, tickets, or chats.

### Exit codes

Text output and `--json` use the same exit code.

| Code | Meaning | What a script does |
|---|---|---|
| 0 | Done. Also when Ctrl+C was pressed but the operation still finished. | Nothing. |
| 1 | Failed. The work started and did not succeed, or a read could not read its data. Something can have changed and been undone. | Read the message and **Logs**. |
| 2 | The command line is not valid. Nothing was changed. | Fix the command line. |
| 3 | Refused in the current state. Nothing was changed. | Fix the state or try again later. |
| 4 | Not confirmed. Nothing was changed. | Add `--yes`, or run it in a terminal. |
| 5 | Cancelled at a safe point after Ctrl+C. | Run it again when needed. |
| 6 | Recovery is needed before changes are possible. | A person acts; see [When changes are blocked](#when-changes-are-blocked). |

**Nothing was changed** (codes 2, 3, and 4) means that the command left no
change in these places:

1. the DayZ server installation: server files, mod folders, keys, missions,
   configuration, and profile folders;
2. the saved data of DayZ-ServerMan: profiles, settings, schedules, and
   preferences;
3. the backups and the backup list;
4. the server process: no server was started, stopped, or restarted;
5. the recovery state: no new interrupted work and no new block.

These side effects can still occur with codes 2, 3, and 4. Each one is safe
to repeat:

- Workshop downloads into the SteamCMD folder, and other SteamCMD files there;
- the stored update check results, and a mod state that follows from them;
- operation records and log lines;
- the instance lock file and its holder file;
- stored reviews and selections that only a later apply reads;
- a temporary copy that the command removed again.

When more than one case applies, the first check that stops the command sets
the code. The checks run in this order:

1. the command line (2);
2. another active DayZ-ServerMan (3);
3. the state checks (3 or 6);
4. the question (4);
5. the operation (0, 1, 5, or 6).

Some cases in detail:

- Code 1: a refusal after the operation changed something, for example a
  restart that stopped the server and then could not start it. The server
  stays stopped. `--json` holds the operation record.
- Code 1: a profile name is valid, but a data file of the profile is missing.
- Code 2: a name that you typed does not exist, for example a profile, a
  backup ID, a key, or a medical feature. A folder for `settings set` must
  exist.
- Code 3: `server stop` or `server restart` names a profile other than the
  running one. The message names the running profile.
- Code 3: the data that a review showed changed before the apply. Run the
  command again for a new review.
- Code 3: a `map` command that the saved plan, the mission or the records do
  not allow. Examples are no saved plan, or a restore without a saved
  original. The JSON error code is `INVALID_REQUEST`, also for a plan
  over a limit. A terrain family that is not supported has
  `UNSUPPORTED_SNAPSHOT_CONTENT`, and a running server has `CONTROL_CONFLICT`.
  A saved plan that is not at the `--expect-revision` number has
  `REVISION_CONFLICT`. All of these end with exit code 3.
- Code 4: `map apply` without a choice for a changed file or an unavailable
  encounter. `map import` or `map copy` without `--confirm-listed` for listed
  items. `map export` to a file that exists, without `--overwrite`.
- Code 2: for `map`, an ID or a path that the review does not list. Also a
  path in both `--keep-change` and `--replace-file`, and a point that is not
  `X,Z`. Also a mode other than `replace`, `merge`, `original`, or `previous`.
  An export path that cannot be used ends with code 2 too.
- Code 6: `map recover` when an interrupted mission map change still blocks
  changes.

### JSON error codes

`error.code` tells the reasons of one exit code apart. It is the code of the
window's request, for example `EXTERNAL_PROCESS`, `CONTROL_CONFLICT`,
`REVISION_CONFLICT`, or `RECOVERY_REQUIRED`. It can also be one of the ten
codes of the command line:

| Code | Exit code | Meaning |
|---|---|---|
| `USAGE` | 2 | The command line is not valid. |
| `INSTANCE_ACTIVE` | 3 | Another DayZ-ServerMan is active for this manager folder. |
| `SETUP_REQUIRED` | 3 | `server start` needs the folders; run `settings set` first. |
| `NOTHING_TO_CONVERT` | 3 | `tweaks convert-loadout` found no older starter loadout. |
| `CONFIRMATION_REQUIRED` | 4 | A question needs `--yes` (no terminal, `--json`, or a missing `--overwrite`). |
| `NOT_INTERACTIVE` | 4 | `steam login` needs a terminal and text output. |
| `CANCELLED` | 5 | Ctrl+C cancelled the command at a safe point. |
| `INSTANCE_LOCK_UNSUPPORTED` | 1 | The manager folder cannot hold the lock; move it to a local drive. |
| `CHECK_NOT_FINISHED` | 1 | `updates check` did not finish in 5 minutes. |
| `NOT_READY` | 1 | The server was started but was not ready in time, or it stopped running. |

For `migrate apply`, `NOT_FOUND` (exit code 1) means that the legacy folder,
or a named item, has nothing to import. `MIGRATION_CONFLICT` (exit code 3)
means that the data is there, but it conflicts with the current profiles.
`migrate preview` shows the reason for each item.

### One manager that changes things

Only one DayZ-ServerMan changes things in a manager folder at a time. This is
the window or one command.

- A command that reads runs at any time, also while the window is open. It
  writes no file and no log line. The exception is `map export`: it writes
  only the file that you name with `--output`, and no log line. The file cannot
  be in the DayZ server folder, the DayZ-ServerMan folder or the backup folder.
  It replaces an existing file only with `--overwrite`.
- A command that changes something refuses while the window or another such
  command is active: exit code 3, `INSTANCE_ACTIVE`. Close the window, or
  wait for the other command, then run it again.
- The window does not open while another window or a command that changes
  something is active. Its message names the other one.
- A command that changes something starts like the window. It first finishes
  or undoes interrupted work, as the window does at start.

A read can meet a change of the server folders. While the window applies mods,
restores, or deletes a profile, a command that reads waits up to about 30
seconds. Then it ends with exit code 3 and **The server files are being
changed by another DayZ-ServerMan. Try again in a minute.**

The other direction is rare. A window step that replaces server folders waits
up to about 5 seconds for a command that reads. Then it refuses, and nothing
changes. Try the step again.

### Who controls the server

- A server that `server start` started keeps running when the command ends or
  the terminal closes.
- A later command or the window can stop or restart that server. The same is
  true for a server that the window started.
- After a crash of the window, the next DayZ-ServerMan finds the running server
  and can control it.
- The window can close while a server runs that it did not start itself.
- A server that you started by hand stays **Running outside the manager**.
  `server stop` and `server restart` refuse it with exit code 3.

### Progress and Ctrl+C

A long operation shows its name, its step, and its progress on stderr. On a
terminal, this is one line that updates. Otherwise, it is one line with the
time for each step. The command returns only when the operation ends.

- Ctrl+C asks the operation to stop at the next safe point. The command
  says **Cancelling at the next safe point.** and keeps waiting. A cancelled
  operation ends with exit code 5. If the operation still finished, the code
  is 0.
- Some steps cannot stop. Then the command says **This step cannot be
  cancelled. DayZ-ServerMan waits until it is finished or undone.** Press
  Ctrl+C again later to ask again.
- Ctrl+C at the question changes nothing: exit code 4.
- Ctrl+C during a read ends it with exit code 5.
- `updates check` cannot be cancelled after it starts. It waits up to 5
  minutes for the result, then ends with exit code 1.
- `server start --wait-ready` waits until the server answers Steam queries,
  by default up to 300 seconds. `--ready-timeout` sets 10 to 3600 seconds.
  When the time ends, the exit code is 1 and the server keeps running.
- Ctrl+C during that wait ends the wait with exit code 0. The server keeps
  running. With `--json`, `value.ready_wait_stopped` is `true`.

A closed terminal or a stopped process acts like a crash. The next start of
DayZ-ServerMan finishes or undoes the interrupted work.

### Restart without the window

Daily schedules run only while the window is open. The command line has no
background mode. For a restart without the window, let Windows Task Scheduler
run:

```text
py D:\Tools\DayZ-ServerMan\DayZ-ServerMan.py --cli server restart --profile chernarus_pve --yes
```

Use the path of your copy and your profile ID. The command refuses with exit
code 3 while the window is open.

### Folders in Settings

- `settings check-path` reports the result of the check with exit code 0,
  also for a folder that does not exist (**Not found**) or a file. A script
  reads `value.status`. Only `READY` means that the folder can be used.
- `settings check-path` checks the folder only. It shows the derived paths,
  such as `steamcmd.exe`, but it does not check them. After `settings set`,
  run `settings show` to see their checks.
- `settings set` needs a folder that exists. A missing folder or a file ends
  with exit code 2. Create a new backup folder first.

### Examples

```text
py DayZ-ServerMan.py --cli status
py DayZ-ServerMan.py --cli server players --names
py DayZ-ServerMan.py --cli server stop --profile chernarus_pve --backup-after-stop
py DayZ-ServerMan.py --cli backup list --json
py DayZ-ServerMan.py --cli config set --profile chernarus_pve --target server --set maxPlayers=40
py DayZ-ServerMan.py --cli mods update --profile chernarus_pve --restart --yes
```

Map commands:

```text
py DayZ-ServerMan.py --cli map show --profile chernarus_pve
py DayZ-ServerMan.py --cli map inspect --profile chernarus_pve --point 7500,7500
py DayZ-ServerMan.py --cli map inspect --profile chernarus_pve --point=-5,3
py DayZ-ServerMan.py --cli map preview --profile chernarus_pve
py DayZ-ServerMan.py --cli map apply --profile chernarus_pve
py DayZ-ServerMan.py --cli map restore original --profile chernarus_pve
py DayZ-ServerMan.py --cli map export --profile chernarus_pve --output plan.json
py DayZ-ServerMan.py --cli map import --profile chernarus_pve --from-file plan.json --mode merge
py DayZ-ServerMan.py --cli map copy --profile chernarus_pve --from-profile livonia_pve --mode replace
```

If `map apply` lists a changed file, run it again with `--keep-change PATH` or
`--replace-file PATH` for that file. If it lists an unavailable encounter, add
`--exclude-encounter ID`. If `map import` or `map copy` lists items that are
not imported, read the list, then add `--confirm-listed`.

A PowerShell script that checks the result:

```powershell
py DayZ-ServerMan.py --cli server start --profile chernarus_pve --yes --json > start.json
if ($LASTEXITCODE -eq 0) {
    (Get-Content start.json | ConvertFrom-Json).value.result.state
}
```

### Known limitations

- **Medical loot settings, loot zones.** The loot zones change
  `mapgroupproto.xml`. When this file and its stored original do not match,
  `tweaks medical show` ends with exit code 3, and the window shows the same
  text. The text gives the reason, then the way out.
  - A group that is not in the stored original: "mapgroupproto.xml differs
    from its stored original: group PlannerRich_00534 is not in the stored
    original. No file was changed."
  - A mixed state, or a group that is in neither form: "mapgroupproto.xml
    differs from its stored original and from the form that DayZ-ServerMan
    writes when medical loot zones are on. No file was changed."
  - The way out of both: "DayZ-ServerMan does not guess the original loot of
    these groups. If mapgroupproto.xml changed on purpose, for example after
    a game or mod update or by another tool, first make sure that its medical
    loot groups have the loot that you want when medical loot zones are off.
    Then move the stored original to another folder. The current file then
    becomes the original, which is the Off state. Otherwise, put back the
    mapgroupproto.xml that matches the stored original. Stored original:
    <path>"
  - A layout cause ends with "Correct this in mapgroupproto.xml, then try
    again."
  - When the map editor manages the mission and this profile has an applied
    map plan, the setting works. `tweaks medical show` ends with exit code 0.
    When this profile has no applied map plan,
    `tweaks medical set` ends with exit code 2. The text is "Medical loot
    zones cannot change because the map editor manages this mission, but this
    profile has no applied map plan for it. No file was changed. If another
    profile applied its map plan to this mission, change this setting in that
    profile. Otherwise apply a map plan of this profile in the map editor,
    then change this setting."
- **Medical loot settings, item spawns.** The `medical_item_spawns` setting
  can end with exit code 3, and the window shows the same text. This happens
  when its medical file matches neither its original form nor the form that
  DayZ-ServerMan writes. The text is **The profile or the settings changed in
  the meantime. Run the command again.** Running it again does not help, and
  this version has no way out for this setting.
- **A backup folder inside the DayZ server folder.** A mod apply, a profile
  restore, or a profile deletion can remove or replace the folder that holds
  it, and its backups with it. A backup of a mission can also include older
  backups. The window behaves the same way. Keep a custom backup folder
  outside the DayZ server folder.
- **Manager folder on FAT, exFAT, or a network share.** There, a command that
  reads can still make a save of the window fail. On a folder that cannot
  hold the lock, a command that changes something ends with exit code 1.
- **Synced folders.** No lock protects a manager folder that a sync service
  shares between two computers. Use each copy on one computer only.

## Local application data

- `config/manager.json` stores configured external paths and Steam settings.
- `data/profiles/` stores server profiles.
- `data/ui-preferences.json` stores interface preferences, including the
  update-check switch.
- `data/schedules.json` stores daily stop and restart schedules.
- `data/update-check.json` stores the last answers from Steam about mods.
- `data/server-build-check.json` stores the last answer about the DayZ server
  build.
- `data/content-proofs.json` stores the recorded content of downloaded mods
  and their server copies.
- `data/logs/` stores manager and DayZ output.
- `data/operations/` stores operation records and recovery journals.
- `data/server-ownership.json` records the DayZ server that this copy started,
  so that the window and commands can control it.
- `data/instance.lock`, `data/instance-holder.json`, and
  `data/server-folders.lock` let only one DayZ-ServerMan change things at a
  time.
- `backups/` stores portable-default backup files.

The two check files are disposable. Without them, DayZ-ServerMan checks again.
Without `data/content-proofs.json`, the next update can read each mod once in
full.

Keep `config`, `data`, and `backups` when moving an existing configured copy.
A clean repository checkout contains only safe placeholders and
`config/manager.example.json`.

## Troubleshooting

- If setup fails, confirm that Python can run `-m venv` and `-m pip`.
- Confirm that PyPI is reachable during dependency installation.
- If the window cannot open, install or repair Microsoft Edge WebView2 Runtime.
- If a check reads **Could not check**, read the reason in the row. The last
  successful result stays visible.
- Review **Manager activity** on the Logs page when an operation fails. Use
  **Manager diagnostics** or `data/logs/manager.jsonl` only for technical detail.
- Do not delete `config`, `data`, or `backups` when preserving an installation.

## Before using a production server

Test Start, Save & Stop, Save & Restart, SteamCMD authentication, mod updates,
Update & restart, backup, and restore against a non-critical server copy. Keep
the previous manager and its data until this validation succeeds.

## License

DayZ-ServerMan uses the [MIT License](./LICENSE). Copy the `LICENSE` file with
this directory.

The license covers this project's source code only. DayZ server files, Steam
Workshop mods, and third-party assets keep their own terms.
