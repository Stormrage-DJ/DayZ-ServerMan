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

## Lifecycle safety

DayZ-ServerMan reconciles the configured executable with running Windows
processes before it enables lifecycle actions.

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

Each archive contains a manifest and the selected profile's managed data. The
manager verifies the published archive before it reports success.

The portable destination is `runnable/backups/`. A custom local destination can
be selected in Settings. Existing backup files remain external user data and
must not be committed to Git.

## Restore behavior

Restore is a reviewed, transactional operation. The manager verifies the
selected archive, shows the intended targets, and requires confirmation before
it changes DayZ files.

Recovery journals are stored under `data/operations/`. If an interrupted
restore cannot be classified safely, the manager blocks conflicting mutations
until recovery is resolved.

## Logs and operation records

- `data/logs/manager.jsonl` contains structured manager events.
- `data/logs/dayz-server.log` contains captured DayZ output.
- `data/operations/` contains durable operation state and recovery journals.

The Logs page refreshes periodically. It preserves the reader's scroll position
unless the view already follows the end of the log.

## Controlled adoption

Use a non-critical server copy for the first validation. Test paths, profile
launch arguments, graceful stop, restart, SteamCMD authentication, mod updates,
backup, and restore.

Keep the previous manager and its data unchanged until the new manager passes
all checks needed for that server.
