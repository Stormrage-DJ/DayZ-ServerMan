# DayZ-ServerMan

DayZ-ServerMan is a movable Windows desktop manager for local DayZ servers.
It uses Python and the Windows WebView2 runtime. It does not need a build step.

## What it does

- Starts, stops, and restarts the DayZ server, with a daily schedule and an
  optional backup after each stop.
- Shows the server state, the uptime, and the number of players online.
- Checks Steam for mod updates and for a newer DayZ server build, and shows the
  result without a click.
- Updates the mods in one action: **Update all**, **Update & start**, or
  **Update & restart**. A review comes before any change to the server folder.
- Creates verified backups and restores them, also into a deleted profile.

## Use the application

1. Copy the complete [`runnable/`](./runnable/) directory.
2. Run `DayZ-ServerMan.py` inside that directory.
3. Follow the [operator guide](./runnable/README.md).

The target computer needs Python 3.12 or 3.13. The first launch creates a
project-local `.venv` and installs the packages in `requirements.txt`.

Start from the repository root with:

```text
py runnable\DayZ-ServerMan.py
```

<!-- shot:root-01 -->
![DayZ-ServerMan window: Overview of a running server with 18 of 60 players, mod updates waiting, and a DayZ server update](img/root-01.png)

## What is sent to Steam

To check for mod updates, DayZ-ServerMan sends the Workshop item numbers of
your mods to the Steam Web API. The request carries no key, account name, or
sign-in. Steam also sees the IP address of this computer. For the DayZ server
build, SteamCMD signs in anonymously and reads the public app information.

These checks run automatically while the switch in **Settings** is on. With
the switch off, they run only when you select **Check now**. An update of the
mods also runs the mod check. See
[update checks](./runnable/README.md#update-checks).

## Repository map

- [`runnable/`](./runnable/) — complete movable application
- [`docs/`](./docs/) — operations and development documentation
- [`tests/`](./tests/) — automated tests
- [`.github/workflows/`](./.github/workflows/) — the test run on GitHub Actions

See the [documentation index](./docs/README.md) for deeper guidance.

## Backups and recovery

Full backups include the server configuration, runtime directory, complete
mission, world persistence and profile definition. Mod binaries are not included.

- **Latest backups** shows the selected server's three newest backups. Use a
  card's restore icon to review that backup for the existing profile.
- **Restore profile from backup…** opens a ZIP picker to recreate a deleted
  profile. It also works when no profiles exist. Select an archive, review the
  destination and ports, then confirm.

Older archives remain stored. Direct profile reconstruction requires complete
profile metadata; compatible older archives can still restore an existing profile.
Restore does not start the server. Check readiness and start it separately.

See the [operator guide](./runnable/README.md#backups-and-restores) for the steps
and [restore operations](./docs/operations.md#restore-behavior) for conflicts and recovery.

## License

DayZ-ServerMan uses the [MIT License](./LICENSE).

The license covers this project's source code only. DayZ server files, Steam
Workshop mods, and third-party assets keep their own terms.
