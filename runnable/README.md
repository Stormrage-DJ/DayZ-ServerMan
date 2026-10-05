# DayZ-ServerMan operator guide

This directory is the complete movable application. Copy the whole directory.
Do not copy only the starter or `src` directory.

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
and the Overview is visible. If the server reports no names, the count stays
and the list says that names are not available.

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

Schedules are stored separately for each profile. They run only while
DayZ-ServerMan is open. If the manager starts after today's scheduled time, it
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
custom destination can be selected in Settings.

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
