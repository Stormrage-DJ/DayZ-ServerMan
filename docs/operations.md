# Operations guide

## Portability model

`runnable/` is movable source, not a compiled or self-contained executable.
The destination computer must provide a supported Python installation and
Microsoft Edge WebView2 Runtime.

Copy the complete directory. The starter resolves all application-owned paths
from its own location. External DayZ, SteamCMD, and backup paths remain stored
as configured absolute paths.

For a clean installation, do not copy `.venv`, `config/manager.json`, generated
`data/`, or existing backup files. For an existing configured installation,
keep `config`, `data`, and `backups` together.

## Profile selection

The sidebar profile selector is the only profile selector. It controls all
profile-aware pages. Changing the selection does not navigate away from the
current page. The manager remembers the last selected profile.

The line under the selector shows the state of the DayZ server in the
configured installation. One DayZ server runs in this installation, whichever
profile is selected. If the running server was started with another profile,
the sidebar names that profile.

Profiles contain DayZ-relative paths, launch arguments, runtime profile data,
and ordered mod entries. They do not contain Steam passwords.

## Profile provisioning

**New profile** discovers installed missions from `<DayZ root>/mpmissions` and
also accepts an existing custom DayZ-relative mission directory. Creation is a
managed operation that publishes the profile record together with:

```text
<DayZ root>/serverman/<profile-id>/serverDZ.cfg
<DayZ root>/serverman/<profile-id>/profile/
```

The `serverman` directory contains DayZ operational data only. The movable
manager application remains outside the DayZ installation.

Creation reuses a pre-existing target only when it is an unambiguous generated
profile folder containing `serverDZ.cfg` and a `profile` directory. It never
overwrites those reused files. Other pre-existing targets are rejected with an
actionable error. New content is staged with a recovery journal under
`data/operations/profile-provisioning/`, and rollback removes only directories
carrying that operation's ownership marker. If ownership is ambiguous after an
interruption, the manager preserves the files and blocks mutations for review.

Deleting a manager-created profile permanently removes its generated
configuration and runtime folder, exclusive `storage_<instanceId>` world data,
schedule, and saved preferences. Backup ZIP archives remain in the configured
backup destination as retained recovery data. The manager blocks deletion when
DayZ is active or generated-folder ownership is ambiguous. Mission storage that
is shared with, or may belong to, another profile is preserved while the rest of
the selected profile is deleted. Imported profiles outside the generated layout
are preserved.

After success, the manager refreshes the shared profile catalog, selects the
new profile, and stores that selection. Existing profiles are not moved into
the generated layout.

## Lifecycle safety

DayZ-ServerMan compares the configured executable with running Windows
processes before it enables lifecycle actions.

Overview reports process control and server readiness separately:

- **Starting** means that the managed DayZ process exists, but it is not
  **Ready** yet.
- **Ready** means that the managed process answers a valid Steam server query.
  Also, a DayZ log file (`DayZServer_x64_*.RPT`) of this launch shows that
  the mission accepts players.
- **Not responding** means that the process still exists, but it is not
  **Ready** after the two-minute startup grace period.

The readiness warning does not remove process ownership. **Save & Stop** and
**Save & Restart** remain available so the operator can recover safely.

- **Start server** starts the selected profile only from a proven stopped state.
- **Save & Stop** requests a graceful close and waits for the managed process.
- **Save & Restart** completes the stop before it starts a new process.
- The manager does not expose a force-kill action.
- It does not stop an external, ambiguous, or unverified DayZ process.

If **Backup after stop** is enabled, backup creation begins only after the
managed process has stopped. A restart continues only after backup verification
succeeds.

**Save & Stop**, **Save & Restart**, and **Update & restart** act on the
selected profile. While the server runs with another profile, these actions are
locked with the reason. The Overview names the running profile. Select it in
the sidebar to stop or restart the server. Without this lock, a stop with
**Backup after stop** would back up the selected profile, not the running one.

## Overview layout

The Overview shows the server state once, in the server strip. While the
server runs, the strip shows the uptime, the process ID, and the Steam query
port. While it does not run, the same facts are behind **Process details**.
The strip also holds **Backup after stop** and the lifecycle buttons.

Below the strip, one status list has four rows:

- **Mods**: the counts of updates available, downloaded but not applied, and
  not downloaded, the time of the last check, **Check now**, and **Open Mods**.
- **DayZ server**: the result of the [server build check](#server-build-check).
- **Last backup**: the newest backup of the selected profile, with
  **Open Backups**.
- **Next scheduled action**: the daily schedule, with its editor.

**Check now** on the Overview checks the mods and the server build. **Check
now** on Mods checks the mods only. The badge on **Mods** in the sidebar uses
the same counts as the Mods row and the Mods table. The server build does not
add to the badge, because no Mods action can install it.

The Overview no longer shows a count of recent operations. Completed
operations are listed in **Logs → Manager activity**.

## Online players

While the server runs under the manager, the server strip shows the player
count, for example **Players 18 / 60**. The count comes from the A2S_INFO answer
that the readiness check already receives on `127.0.0.1`. No extra query is
sent for it. While the server is **Starting**, no count shows. **Players not
known** has three causes:

- the server is **Not responding**: it was not **Ready** within the two-minute
  startup grace period, or it stopped answering;
- the answer carried no valid count;
- a server outside the manager did not match the selected profile (see
  below).

Selecting the count opens a list of names with the connected time. The list
uses an A2S_PLAYER query to `127.0.0.1` on the same query port. It is read when
the list opens, then every 10 seconds while the list is open, the Overview is
shown, and the window is visible. The names are sorted by name. A player
without a name shows as **Connecting player**. If the server reports no names,
the list says that names are not available, and the count stays.

Player names are shown in the window only. They are never logged, saved in a
file, or written into an operation record or error text.

For a server that runs outside the manager, the manager sends an A2S_INFO
query to `127.0.0.1` on the query port of the selected profile. That port is
`steamQueryPort` in the profile's `serverDZ.cfg`, else 27016. If the server
asks for a challenge, the query takes two requests. The query repeats on each
status read.

The manager shows the count only when the answer carries exactly the server
name (`hostname`) and the game port of that profile. A match also lets the
names list open. Otherwise the strip shows **Players not known**. This match
changes nothing else: the server still counts as running outside the manager,
and its controls stay locked.

## Daily lifecycle schedule

The Overview page can schedule one daily lifecycle action for each profile.
The available actions are **Save & Stop** and **Save & Restart**. The schedule
uses the computer's local time.

The scheduler runs inside DayZ-ServerMan. Keep the manager open for the action
to run. The manager does not create a Windows service or Task Scheduler entry.

The scheduler follows these rules:

- Only one action can be selected for a profile.
- Clearing both actions disables the schedule but keeps the entered time.
- Starting the manager after today's time does not run a missed action.
- A due action runs at most once per local calendar day.
- The server must be running under the manager when the action becomes due.
- A skipped or failed action is recorded and is not retried that day.
- **Backup after stop** uses the saved setting for the scheduled profile.

Schedule state is stored in `data/schedules.json`. The Overview page shows the
next local run time and the latest result.

## Update checks

DayZ-ServerMan checks for updates without SteamCMD for the mods and without
the operator's account for the server build. Neither check downloads, applies,
or authorizes anything.

### What is sent to Steam

The mod check covers the Workshop mods of all profiles. It sends one HTTPS POST
request for each group of up to 200 mods:

- Host and path: `api.steampowered.com`,
  `/ISteamRemoteStorage/GetPublishedFileDetails/v1/`.
- Content: the item count and the numeric Workshop item IDs. No key, cookie,
  account name, or other field is sent. The user agent is `DayZ-ServerMan`.
- Steam receives the IP address of this computer, as with any request.

The request ignores proxy settings and follows no redirect. It verifies the
certificate. All requests of one check together stop after 10 seconds. The
name lookup of the host is not bound by this limit. Each request accepts at
most 4 MiB of answer.

One check sends at most 1,000 IDs. When it leaves IDs out, the manager logs a
warning, and those mods get no answer. From the answer, the manager keeps only
the update time, the size, and whether the item exists for DayZ. It ignores
all text fields.

For each request, the log records the number of IDs. For each answer, it
records the outcome, the failure code, the HTTP status, the number of bytes,
and the duration. For each check, it records the outcome, the failure code,
and the number of answers. It never records the body, the headers, or the
IDs.

The server build check runs one SteamCMD command with an anonymous sign-in:

```text
steamcmd.exe +login anonymous +app_info_update 1 +app_info_print 223350 +quit
```

It never uses the configured account, and it never downloads, validates, or
installs. SteamCMD writes only in its own folder. One of those files,
`config\config.vdf`, also holds the cached account sign-in. A real run
confirmed that a later account-mode mod update still signs in without a prompt.

### When the checks run

| Check | Automatic | On demand |
|---|---|---|
| Mods | A request at start, when **Mods** opens, on a profile change, when an operation of the Mods page ends, and when the switch is saved. It runs only when the last attempt is at least 5 minutes old or a mod has no answer yet. A timer also runs a check 30 minutes after the last successful attempt. | **Check now** on Mods or Overview; every update (**Update all**, **Update & start**, **Update & restart**) |
| DayZ server build | At start, unless an attempt is less than 10 minutes old; then 6 hours after the last attempt | **Check now** on Overview |

After a failed mod check, no automatic check runs for 1, 2, 4 minutes, and so
on, up to 30 minutes. A failed build check waits for the next 6-hour interval.
**Check now** is ignored while a check runs, and within 5 seconds (mods) or 60
seconds (build) of the last attempt.

The setting **Settings → Update checks** turns the automatic checks off. **Check
now** and the check inside each update still run and send the same request.
The setting is stored in `data/ui-preferences.json`.

The last answers are stored in `data/update-check.json` and
`data/server-build-check.json`. Both files are disposable. A missing or damaged
file means "never checked".

### When a check fails

**Could not check** means that no fresh answer exists. It shows with the
reason and the time of the last successful check. The last known updates stay
visible.

For a mod, the reason is one of these:

- the check failed;
- Steam does not list the item;
- the last check is too old;
- no check ran yet;
- automatic checks are off.

For the build, two more reasons exist: SteamCMD is not set up, or Steam shows
a non-public branch only after a sign-in.

A mod without a fresh, successful answer is never shown as **Current**. After
60 minutes without a successful mod check, the result counts as old. After 12
hours, the build result counts as old.

On the Overview, the **DayZ server** row then shows the head **Could not
check** with the reason in the line below it.

## Mod row states

The Mods table shows one state for each mod. The manager computes the state on
each read. It uses the local Steam record, the last answer from Steam, and the
record of the copy in the server folder. The first rule that matches wins.

| State | Rule |
|---|---|
| Local | The mod is not managed through the Workshop. |
| Unavailable | The Workshop folder is not set or cannot be read. |
| Not downloaded | No local Steam record exists, or the mod folder is missing. |
| Update available | Steam reports a newer update time than the local record, or the local record has a newer pending manifest. A stale or failed check keeps this state. |
| Downloaded - not applied | The server folder holds no proven copy of the installed version. A copy that exists but was never verified adds a hint to run **Verify files** or update. |
| Current | A fresh check confirms the local version, and the server copy is proven or cannot be checked. It cannot be checked when no DayZ server folder is set, or the folder cannot be read. |
| Installed | Any other downloaded mod, for example without a fresh check. Without a fresh answer, it shows as **Could not check** with the reason, or as **Checking…** while the first check runs. |

While an update runs, a row shows **Checking…** until the update reaches it.
Then it can show **Downloading** with a percentage, **Waiting for download**,
**Verifying download**, or **Failed**. During an apply, each mod that the apply
copies shows **Applying to the server folder…**. The download percentage
comes from the size of the item's download folder against the size that Steam
reported. It is only an indication.

Mods that were applied before content records existed can show **Downloaded -
not applied** until one update with apply, or **Verify files**, records them.

## Mod update flow

The Mods page separates the Steam sign-in from the configured mod list. Use
the visible SteamCMD window for passwords and Steam Guard codes. The manager
stores the Steam account name and authentication mode, but it does not request
or store the password.

**Update all** runs one update operation, then a review, then one apply
operation:

1. The update operation asks Steam for a fresh answer about the profile's
   mods. Cached answers are never used for this decision.
2. A mod counts as unchanged only when the fresh answer equals the local
   update time, Steam reports no pending work, and the mod folder exists.
3. SteamCMD receives only the other mods. If no mod changed, no SteamCMD
   process starts. If the check fails, every mod goes to SteamCMD.
4. The manager proves each downloaded mod. It accepts a stored content record
   or reads the mod in full. See [content records](#content-records).
5. The page shows a review of the mod folders and key files that the apply
   will copy. In one case no review opens, and the page says that all mods
   are current:
   - SteamCMD did not run;
   - every mod has a valid content record of its download and of its server
     copy;
   - no key file is missing.

   In every other case the review opens. If it copies nothing, it says
   **Nothing is written to the server folder**.
6. After confirmation, the apply operation stages the changed mod folders and
   keys, verifies the copies, and replaces the live folders.

Nothing is copied into the server folder without the review.

### Applying while the server runs

An apply that writes into the server folder takes the installation lock and
requires a proven stopped server. Otherwise it changes nothing and fails with
the reason:

- the server runs under this manager, starts, or stops;
- the server runs outside the manager;
- the server state is not known;
- another DayZ-ServerMan uses this installation.

The review shows the first three refusals before you confirm. It uses the
server state that the page read last, and it names what to do. It points to
**Update & restart** only when the server runs under this manager with the
selected profile. The review cannot see another DayZ-ServerMan: the apply
tests the installation lock only after you confirm. An apply that writes
nothing needs no lock and runs in any server state.

### Update & start and Update & restart

The button reads **Update & restart** while the server runs under the manager,
runs outside it, or starts. In every other state, it reads **Update & start**.
It is enabled in two states only. In any other state it is locked, with the
reason as its tooltip.

**Update & start** is enabled while the server is stopped. It runs the same
update and review. The apply then checks the server folder and starts the
server. The review opens also when all mods are current, to confirm the start.

**Update & restart** is enabled while the server runs under the manager with
the selected profile. The download and the review happen while the server
runs. After confirmation, one operation runs these steps:

1. Check that the reviewed plan is still valid.
2. **Save & Stop**.
3. Create a verified backup, if **Backup after stop** is set for the profile.
4. Apply the mods inside the installation lock.
5. Check the server folder before the start.
6. Start the server.

The operation continues when you leave the page. **Cancel** stops it at the
next safe point:

- before the stop;
- during the backup;
- during the apply, before the new folders replace the live folders;
- after the apply, before the start.

The stop itself cannot be cancelled. If the reviewed plan copies nothing, the
operation ends after step 1, and the server keeps running.

| Point of failure | Server | Server folder |
|---|---|---|
| Download failed or cancelled | Keeps running | Unchanged |
| Review cancelled | Keeps running | Unchanged |
| Plan out of date at step 1 | Keeps running | Unchanged |
| Cancelled before the stop | Keeps running | Unchanged |
| Stop failed | See the Overview | Unchanged |
| Server was already stopped at step 2 | Stopped; not started | Unchanged |
| Backup failed | Stopped | Unchanged |
| Cancelled during backup or apply | Stopped | Unchanged |
| Server state changed after the stop, so the apply was refused | See the Overview | Unchanged |
| Plan or server folder changed after the stop, or apply failed and rolled back | Stopped | As before |
| Apply interrupted; changes blocked | Stopped | Not confirmed; see [recovery blocks](#recovery-blocks) |
| Server folder changed before the start | Stopped | Mods applied |
| Cancelled after the apply, before the start | Stopped | Mods applied |
| Start failed | Stopped, or see the Overview | Mods applied |
| Success | Running | Mods applied |

The result sentence on the Mods page and in the bar says which row applies.
For example, a refused apply after the stop reads **The server was stopped,
but its state changed before the mods could be applied.** An interrupted apply
reads **Server stopped, but the mods could not be applied. Changes are
blocked.**

No journal resumes the sequence after a crash. The server then stays stopped,
and the existing recovery of the apply runs at the next start of
DayZ-ServerMan.

### Content records

`data/content-proofs.json` records, for each Workshop mod and each copy in a
server folder, the Steam manifest ID, a content digest, and a metadata
fingerprint. The fingerprint covers the file paths, sizes, and modification
times. One record serves all profiles and survives profile edits. Deleting a
profile does not remove it.

A matching fingerprint is accepted in place of a full content hash. A full
hash runs only in these cases:

- the manifest ID or the fingerprint changed;
- no record exists yet;
- the apply copies a mod (every copied file is hashed and verified);
- the pre-start check finds a copied folder, the keys folder, or a miss;
- the operator selects **Verify files**.

Accepted risk: a change that keeps every size and modification time, such as
silent disk damage, is found only by **Verify files**. The older file
`data/applied-mod-state.json` is still read when no record exists. It is never
written again and never deleted.

Hashing reads each file in 1 MiB parts. It never loads a whole file into
memory.

### Verify files

**Verify files** is an operation on the operation lane. It reads every file of
each Workshop mod of the profile and of its server copy, and compares their
content digests. It needs no stopped server and does not change any mod or
server file. It replaces the content records with what it measured and never
repairs a file.

| Result in the row | Meaning |
|---|---|
| Download missing | The mod is not downloaded. |
| Download could not be read | The download has an unsafe entry or a read error. |
| Download changed since it was recorded | The content differs from the record of the same Steam version. |
| Not applied to the server folder | The server folder has no copy. |
| Server copy differs from the download | The server copy does not match. |
| Server copy could not be read | The server copy has a read error. |

Cancel stops the verification between files. Mods that finished stay recorded.

## Server build check

The **DayZ server** row of the Overview compares two builds:

- The installed build comes from `appmanifest_223350.acf`. The manager looks
  for it in `<DayZ folder>\steamapps\` (SteamCMD layout) and two folders above
  the DayZ folder (Steam library layout).
- The newest build comes from the anonymous SteamCMD command above. The manager
  compares the build ID of the installed branch.

The check runs off the operation lane, so it never locks a page. It starts only
when no operation runs or waits, and never beside another SteamCMD run of this
manager. A mod update or a SteamCMD sign-in that arrives during a check waits
for it, at most 120 seconds. The command stops after 60 seconds. A cached
answer from an offline SteamCMD never counts.

| Row text | Meaning |
|---|---|
| DayZ server is up to date. | A check from the last 12 hours confirms the installed build. |
| DayZ server update available: build N. | Steam lists a higher build for the installed branch. |
| Steam lists a different DayZ server build: N. | Steam lists a lower build for the installed branch. |
| Steam has a DayZ server update queued: build N. | The Steam manifest names a target build other than the installed one. |
| Steam is updating the DayZ server. | The Steam manifest flags show update work, for example a running or paused update. |
| Steam has a DayZ server update queued. | The Steam manifest flags an update as required. |
| Steam reports missing or damaged DayZ server files. | The Steam manifest flags missing or damaged files. |
| Steam has a change of the DayZ server branch queued. | The branch selected in Steam differs from the installed branch. |
| **Could not check**, with the reason below it | No current answer; the reason names the cause. |
| The installed DayZ server build is not known: reason. | No readable installation record was found. |

With an update, the row adds guidance. It depends on who installed the
server:

- A folder in a Steam client library: stop the server, then update “DayZ
  Server” through Steam.
- A SteamCMD installation: stop the server, then update it with SteamCMD.
- Unknown or conflicting signals: update it with the program that installed
  it.

This classification chooses wording only. It never authorizes a write.
DayZ-ServerMan does not update the server build.

If a SteamCMD run does not close cleanly, build checks pause for the rest of
the session. Later mod updates and sign-ins fail at once with a sentence. Close
SteamCMD, then restart DayZ-ServerMan.

## Operation bar

Long operations show in a bar above the page. The page stays visible and
usable. The bar shows the operation name, a readable step, the progress, and
**Cancel** where the operation has a safe point. More waiting operations show
as **+N waiting**.

- The bar keeps one result that is not a success. It stays until you select
  **Dismiss** or a newer result that is not a success replaces it. A
  cancelled result, or a result
  with a warning, does not replace a failed or blocked one.
- A success result stays until you dismiss it or another operation starts.
- **View in Logs** opens **Manager activity**.
- If the operation belongs to another profile, the bar names that profile.

While an operation runs or waits, controls that change the server or its files
are locked. A locked control keeps its place and names the running operation
as its reason. Navigation, the profile selector, and **Check now** stay
available. In a mod review, **Cancel** or **Close** stays available, but the
confirm button is locked. The backend still refuses a conflicting request.

No page prints internal identifiers, phase names, or error codes. The raw
records stay in **Logs → Manager diagnostics**.

## Backup behavior

Backups are ZIP archives named with the profile ID and local creation time:

```text
<profile>_YYYY-MM-DD_HH-MM-SS.zip
```

Each archive contains a manifest, the server configuration, the complete
mission directory (including `storage_<instanceId>` world and mod persistence),
and the runtime profile directory. The manager verifies the published archive
before it reports success.

The portable destination is `runnable/backups/`. A custom local destination can
be selected in Settings. Existing backup files remain external user data and
must not be committed to Git. Deleting a profile does not delete its backup
archives.

The selected profile's **Latest backups** section shows its three newest backups.
Use a card's restore icon to review that exact backup. Unavailable restores have
a disabled icon. Older archives remain stored; this display limit does not delete them.

## Restore behavior

Restore is a reviewed, transactional operation. Stop all DayZ servers in the
configured installation before preparing a restore. The manager verifies the
archive and shows the targets before requesting confirmation.

Choose the entry point for your task:

| Task | Entry point | Result |
|---|---|---|
| Restore files for an existing profile | Select the profile, then a restore icon under **Latest backups** | Review and restore that card's archive into the existing profile |
| Recreate a deleted profile | **Restore profile from backup…** | Browse for a ZIP and reconstruct a new profile from its metadata |

### Restore an existing profile

The card's restore icon prepares that specific backup. Review the targets and
recovery plan, select **Restore this backup**, then confirm **Restore now**.
There is no archive dropdown. An incompatible archive has a disabled icon and
an explanation. This flow keeps the existing profile definition.

### Recreate a deleted profile

The ZIP picker starts in the configured backup folder. Archives elsewhere and
renamed ZIPs are supported. Selection verifies only the chosen archive and
shows its filename, profile, date and size. Dismissal leaves the workspace
unchanged. The source ZIP is not imported, renamed or modified.

This flow works even when no profiles exist. New full backups contain the
complete profile definition, ordered mods and launch arguments. Review the
name, ID, mission, storage ID and ports, then select **Review restore**.
Check the final mapping before selecting **Restore profile**.

Selecting an older archive without complete metadata shows one explanation.
Such archives remain intact and can still restore an existing compatible
profile. The manager does not infer or migrate missing metadata. Create a new
full backup to enable direct reconstruction.

The restored profile is selected and remembered after success. Install missing
mods, check readiness and start the server separately. Neither restore flow
starts DayZ automatically. Mod binaries, manager locations and manager
preferences are not restored from a server ZIP.

A selected ZIP is bound to the current application session and its byte hash.
If the file changes, becomes unavailable or the selection expires, browse again
and prepare a new review. Changing the destination choices invalidates an
existing preview and any replacement confirmation.

### Conflicts and recovery

An occupied profile ID or generated folder requires a free ID. An occupied
mission defaults to a new isolated mission and storage ID. Restoration copies
only the selected world, although the ZIP can contain other worlds from the
complete mission. Game and query ports are preserved when free; otherwise the
preview suggests alternatives. Missing mods are listed for installation from
**Mods** and do not prevent file recovery.

Explicit world replacement requires matching common mission files and known
registered consumers. The review names every affected profile and requires
confirmation. Replacement swaps the whole selected storage tree, preserving
common mission files, other worlds and existing profile definitions. A verified
recovery copy is retained beneath the backup recovery directory after success.
If the selected world was absent in the archive, an explicit replacement
restores that absence. The review warns that the existing selected world will
be removed, and its recovery copy is retained.

Direct restoration publishes files and a revision-zero profile record through
one durable journal. Startup rolls back an interrupted uncommitted operation,
but only while the server is proven stopped (see
[recovery blocks](#recovery-blocks)). If files have changed independently,
recovery blocks mutations and preserves those changes. A committed operation
can report pending cleanup. Keep its recovery copies until they are no longer
needed.

Deleting a restored profile removes its isolated mission only when the
ownership marker matches and no other profile references that mission. Shared
missions and retained archives remain preserved.

Recovery journals are stored under `data/operations/`. If an interrupted
restore cannot be classified safely, the manager blocks conflicting mutations
until recovery is resolved.

## Recovery blocks

Four operations write into the DayZ installation through a journal:

- a backup restore;
- a profile restore from a ZIP;
- a profile creation;
- a mod apply.

If such an operation is interrupted, the manager finishes or undoes it at the
next start.

Each recovery that can write runs inside the installation guard. It takes the
installation lock, then reads the server state under that lock. It writes only
when the server is proven stopped. One exception: without a configured DayZ
server folder, no managed server can run, and the recovery of a profile
creation runs without the guard.

In any other state, or when another DayZ-ServerMan holds the lock, it writes
nothing. The journal and the folders stay as they are, and the manager sets a
recovery block. The next start tries again. The **Backups** page also checks an
unfinished backup restore again when it opens, with the same guard. That check
clears only its own block.

While a block is set, the manager refuses changes. The Overview shows
**Recovery required** with one sentence, then **Changes are blocked until this
is resolved. Details are in Logs, Manager diagnostics.** The same reason is
written to the manager log.

| Overview sentence starts with | What to do |
|---|---|
| A backup restore was interrupted and must be finished. | Stop the server, then open **Backups** again or restart DayZ-ServerMan. |
| A profile restore from a backup archive was interrupted and must be finished. | Stop the server, then restart DayZ-ServerMan. |
| Creating a profile was interrupted and must be finished. | Stop the server, then restart DayZ-ServerMan. |
| Applying mods to the server folder was interrupted and must be finished. | Stop the server, then restart DayZ-ServerMan. |
| A profile restore from a backup archive did not finish, and its restore records cannot be read | Restart DayZ-ServerMan to read them again. |
| A profile restore from a backup archive did not finish, and it cannot be checked because no DayZ server folder is set. | In **Settings**, set the DayZ server folder that the restore used, change nothing else, and save. Then restart DayZ-ServerMan. See [No DayZ server folder is set](#no-dayz-server-folder-is-set). |
| A profile restore from a backup archive did not finish, and DayZ-ServerMan cannot finish or undo it safely | If a drive or folder was unavailable, make it available again, then restart DayZ-ServerMan. |
| A profile restore from a backup archive did not finish. Stop the DayZ server | Stop the DayZ server, then restart DayZ-ServerMan. |
| A backup restore did not finish, and it cannot be checked because no DayZ server folder is set. | In **Settings**, set the DayZ server folder that the restore used, change nothing else, and save. Then open **Backups** or restart DayZ-ServerMan. See [No DayZ server folder is set](#no-dayz-server-folder-is-set). |
| A backup restore did not finish cleanly. | Open **Backups**; the manager checks the restore again there. |
| Applying mods to the server folder was interrupted and could not be undone safely. | Restart DayZ-ServerMan; it checks the server folder again. |
| Applying mods to the server folder was interrupted, and it cannot be checked because no DayZ server folder is set. | In **Settings**, set the DayZ server folder that the apply used, change nothing else, and save. Then restart DayZ-ServerMan. See [No DayZ server folder is set](#no-dayz-server-folder-is-set). |
| Creating a profile was interrupted and could not be undone safely. | Restart DayZ-ServerMan; it checks the unfinished profile again. |
| A legacy import was interrupted and could not be undone safely. | Restart DayZ-ServerMan; it checks the import again. |
| A mod update was interrupted, so its result is not known. | Restart DayZ-ServerMan, then update the mods again. |
| SteamCMD did not close cleanly after the Steam sign-in | Close SteamCMD, restart DayZ-ServerMan, then sign in again. |
| SteamCMD did not close cleanly, so the mod update cannot be confirmed. | Close SteamCMD, restart DayZ-ServerMan, then update the mods again. |

The first four cases need a stopped server and no other DayZ-ServerMan on the
same installation. Stop a server that runs outside the manager where you
started it. Close any other DayZ-ServerMan that uses this installation.

A reason that is not in this table shows as the sentence that the manager
wrote, if it holds no internal identifier. Otherwise it shows as **An earlier
operation did not finish cleanly.** Then the Overview adds **Open Backups to
finish the restore.** for a block that a backup restore set, and **Restart
DayZ-ServerMan to check again.** for any other block.

### No DayZ server folder is set

Three blocks name a missing DayZ server folder: an interrupted mod apply, an
interrupted profile restore from a ZIP, and an interrupted backup restore. A
restart alone does not help, because the manager finds the same journal
without a folder and sets the block again. Use a repair save instead:

1. Open **Settings**.
2. Select the DayZ server folder that the interrupted work used. Do not change
   the SteamCMD folder or the backup destination.
3. Select **Save locations**. The Settings page then says **Restart
   DayZ-ServerMan. It then checks the interrupted work in this DayZ server
   folder.**
4. Restart DayZ-ServerMan. For a backup restore, opening **Backups** also
   works.

The block lets this save pass only while every active block is one of these
three. The save must change the DayZ server folder and nothing else.
A save that changes another folder is refused with the block sentence. Any
other block, for example a not-stopped block, also refuses the save.

The repair save changes only the DayZ server folder and its program path in
`config/manager.json`. Before it saves, it checks the folder:

- The folder must exist and hold `DayZServer_x64.exe`. Otherwise the save
  fails with **The DayZ server folder must exist and hold the DayZ server
  program.**
- An interrupted backup restore or profile restore records its DayZ server
  folder. If the selected folder is another one, the save fails with **This is
  not the DayZ server folder that the interrupted work used. Choose that
  folder.**

Both messages follow **The application locations could not be saved.** A
refused save writes nothing, and you can try again.

The repair save writes nothing into the DayZ installation and clears no block.
Other changes stay blocked until the restart. **Manager activity** records the
save as **Saving the DayZ server folder was allowed while changes are blocked.
Other changes stay blocked.**

At the next start, each recovery runs as usual, inside the installation guard.
If the server is not proven stopped, the "Stop the server, then restart" block
appears instead.

An interrupted mod apply does not record its DayZ server folder, so the save
cannot check it. Select the folder that the apply used. Another DayZ
installation can be saved. The recovery after the restart then usually blocks
with **Applying mods to the server folder was interrupted and could not be undone
safely.** That block refuses the repair save. The way out is then to set the
correct folder by hand:

1. Close DayZ-ServerMan.
2. Keep a copy of `config/manager.json`, then open it in a text editor.
3. Set `dayz_root` to the correct DayZ server folder, and `dayz_executable` to
   `DayZServer_x64.exe` in that folder. Write each backslash twice, for
   example `"D:\\Servers\\DayZServer"`.
4. Start DayZ-ServerMan. It checks the unfinished work again.

If the file is not valid JSON after the edit, DayZ-ServerMan cannot read its
settings.

## Logs and operation records

- `data/logs/manager.jsonl` contains structured manager events. Routine bridge
  polling and successful read-only requests are not stored.
- `data/logs/manager.jsonl.1` is the single retained previous manager-log
  segment. The active manager log rotates at 5 MiB.
- `data/logs/dayz-server.log` contains captured DayZ output.
- `data/operations/` contains durable operation state and recovery journals.

The Logs page opens on **Manager activity**, which summarizes completed
operations, warnings, errors, and scheduled actions. **Manager diagnostics**
exposes raw structured records when troubleshooting requires them. The page
checks periodically, replaces content only after the selected log changes, and
preserves the reader's scroll position unless the view already follows the end.

Manager activity also has one line for each finished update check and each
server build check, and one for each recovery block. The mod-check records
hold counts, outcomes, failure codes, HTTP status, byte counts, and durations
(see [What is sent to Steam](#what-is-sent-to-steam)). They never hold the
body of the answer, the Workshop IDs, paths, or the account name.

The build check record holds these fields: the outcome, the failure code, the
SteamCMD exit code, the duration, and the number of branches. It also holds
whether the branch is public, the state, the installed build, and the build
that Steam lists. It never holds SteamCMD output, the branch name, paths, or
the account name. Player names are never logged.

Other files under `data/`:

- `data/update-check.json`: the last answers about mods (disposable).
- `data/server-build-check.json`: the last answer about the server build
  (disposable).
- `data/content-proofs.json`: the content records of mods and server copies.
- `data/applied-mod-state.json`: the older applied-mod record, read only.
- `data/ui-preferences.json`: the selected profile, **Backup after stop**, and
  the automatic update-check switch.

## Controlled adoption

Use a non-critical server copy for the first validation. Test paths, profile
launch arguments, graceful stop, restart, SteamCMD authentication, mod updates,
**Update & restart**, backup, and restore.

Keep the previous manager and its data unchanged until the new manager passes
all checks needed for that server.
