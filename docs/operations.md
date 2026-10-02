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

The sidebar profile selector controls all profile-aware pages. Changing the
selection does not navigate away from the current page. The manager remembers
the last selected profile.

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

- **Starting** means that the managed DayZ process exists, but its local Steam
  query endpoint does not answer yet.
- **Ready** means that the managed process answers a valid Steam server query.
- **Not responding** means that the process still exists, but the query
  endpoint did not answer within the two-minute startup grace period.

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

## Mod update flow

The Mods page separates SteamCMD access from the configured mod list.

1. SteamCMD downloads or updates each Workshop item for the selected profile.
2. The manager verifies the SteamCMD result and Workshop cache evidence.
3. It compares recorded source and destination state.
4. It stages and applies only changed mod directories and keys.
5. It records the applied state for the next update.

Use the visible SteamCMD window for passwords and Steam Guard codes. The manager
stores the Steam account name and authentication mode, but it does not request
or store the password.

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
one durable journal. Startup rolls back an interrupted uncommitted operation.
If files have changed independently, recovery blocks mutations and preserves
those changes. A committed operation can report pending cleanup. Keep its
recovery copies until they are no longer needed.

Deleting a restored profile removes its isolated mission only when the
ownership marker matches and no other profile references that mission. Shared
missions and retained archives remain preserved.

Recovery journals are stored under `data/operations/`. If an interrupted
restore cannot be classified safely, the manager blocks conflicting mutations
until recovery is resolved.

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

## Controlled adoption

Use a non-critical server copy for the first validation. Test paths, profile
launch arguments, graceful stop, restart, SteamCMD authentication, mod updates,
backup, and restore.

Keep the previous manager and its data unchanged until the new manager passes
all checks needed for that server.
