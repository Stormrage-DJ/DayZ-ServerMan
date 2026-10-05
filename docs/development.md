# Development guide

## Repository layout

```text
DayZ-ServerMan/
|-- README.md
|-- .github/workflows/tests.yml
|-- docs/
|-- img/
|-- runnable/
|   |-- DayZ-ServerMan.py
|   |-- requirements.txt
|   |-- img/
|   |-- src/
|   |   |-- frontend/
|   |   `-- python/dayz_serverman/
|   |-- config/
|   |-- data/
|   `-- backups/
`-- tests/
```

- `runnable/` is the complete application that users can copy elsewhere.
- `runnable/src/frontend/` contains the HTML, CSS, and JavaScript interface.
- `runnable/src/python/dayz_serverman/` contains the Python application code.
- `tests/` contains repository-level automated tests.
- `img/` and `runnable/img/` hold the README pictures.

The application loads source files directly. It does not compile or bundle the
frontend. The Python starter composes the local frontend assets into the
WebView2 document at runtime.

## Local setup

From the repository root, run:

```text
py runnable\DayZ-ServerMan.py
```

The starter creates `runnable/.venv` and installs
`runnable/requirements.txt`. The repository does not vendor Python packages in
`runnable/src/python`.

Generated runtime files are ignored by Git. This includes the local virtual
environment, application configuration, profiles, logs, operation records,
browser data, and backups.

## Run tests

Start the application once to create its local environment. Then, in
PowerShell at the repository root, set the source path so that every test
module can import `dayz_serverman`:

```powershell
$env:PYTHONPATH = (Resolve-Path .\runnable\src\python).Path
```

Run the complete suite:

```powershell
.\runnable\.venv\Scripts\python.exe -m unittest discover -s tests
```

Run a focused module:

```powershell
.\runnable\.venv\Scripts\python.exe -m unittest tests.test_lifecycle_bridge
```

Compile-check the Python source:

```powershell
.\runnable\.venv\Scripts\python.exe -m compileall -q runnable\src\python
```

No test opens a network connection, runs SteamCMD, or starts or stops a DayZ
server. The Steam transport, SteamCMD, the A2S socket, the clock, and the
lifecycle are replaced by fakes. No test reads a live DayZ installation or
`runnable/data`.

### Interface tests in headless Edge

About 30 test modules start Microsoft Edge in headless mode. They compose the
real shell with `compose_shell_html`, add a fake `window.pywebview.api`, and
read the result from the dumped document. `tests/ui_harness_support.py`
provides the shared runner (`run_shell_harness`) and the fake host
(`PRELUDE`). `tests/overview_harness.py` and `tests/mods_update_harness.py` add
the fake answers of the Overview and Mods pages.

- The harness runs Edge with a virtual time budget, so page timers run without
  real waiting.
- `tests/test_overview_fit_dynamic.py` measures the Overview at a viewport of
  1150 × 720 pixels. Each case must fit without scrolling and leave at least
  16 pixels free.
- Render README pictures the same way: the composed shell with the fake host
  in headless Edge. Use neutral sample data, such as the profile name
  “Chernarus PvE” and fictional player names. Never show a real account name
  or a real player name.

Each Edge test uses a temporary user-data directory. An Edge process that
retains that directory can cause a Windows lock-file cleanup error. Treat this
as an environment failure unless the test also reports a functional assertion
failure.

### Continuous integration

`.github/workflows/tests.yml` runs on every push and pull request, on
`windows-latest` with Python 3.12. It uses `actions/checkout@v5` and
`actions/setup-python@v6`. It has two jobs:

- **Unit and static tests** (time limit 20 minutes) compile-checks the Python
  source and runs every test module that does not start Edge. A failing test
  fails the run.
- **Edge-driven interface tests** (time limit 30 minutes) first records the
  Edge version as a notice, then runs the modules that start Edge. It is marked
  `continue-on-error`, because hosted runners can keep the temporary Edge
  profile locked.

A module counts as Edge-driven when its source matches the case-sensitive
pattern `msedge|\bEDGE\b`: the text `msedge`, or `EDGE` as a whole word.

Both test steps run `python -m tests.ci_run --label=Unit` or `--label=Edge`
with the selected modules. `tests/ci_run.py` runs `unittest` unchanged, with
the same output and exit code. After the run, it prints workflow annotations.

Each failed or erroring test gets one `::error`. Its title holds the test ID,
and its message holds the last exception line. A step shows at most 10 errors;
the last one then counts the rest. One `::notice` gives the counts and the IDs
of the skipped tests.

#### Read a failing hosted run without signing in

The job log needs a signed-in account, but the annotations are public:

1. List the runs:
   `https://api.github.com/repos/Stormrage-DJ/DayZ-ServerMan/actions/runs?per_page=5`.
2. List the jobs of a run and note the job `id`:
   `https://api.github.com/repos/Stormrage-DJ/DayZ-ServerMan/actions/runs/<run id>/jobs`.
3. Read its annotations:
   `https://api.github.com/repos/Stormrage-DJ/DayZ-ServerMan/check-runs/<job id>/annotations`.

The `::error` entries name the failing tests. The `::notice` entry gives the
counts, also for a green run.

## Source boundaries

The Python package follows these broad responsibilities:

- `application/` coordinates user operations and domain services.
- `domain/` defines validated values and behavior rules.
- `repositories/` owns local persistence and file transactions.
- `adapters/` contains Windows, network, and external-process integration.
- `bridge/` validates calls between JavaScript and Python.
- `host/` exposes the named WebView2 API and composes frontend assets.

The frontend uses small page-specific JavaScript files and shared context
modules. Python remains authoritative for filesystem access, process control,
network access, validation, and persistent mutations.

Bridge changes are additive: new methods and new response fields only.
`CONTRACT_VERSION` stays 1, and every handler requires the exact request field
set.

### Update checks and content records

- `adapters/steam_web_api.py` is the only module that connects to the
  internet. It sends the keyless Steam Web API request with the standard
  library and enforces the transport limits.
- Two more paths use the network. `adapters/a2s_transport.py` opens the UDP
  sockets of the A2S readers (`adapters/steam_query.py` for readiness and the
  count, `adapters/steam_player_query.py` for names). They send only to
  `127.0.0.1`. SteamCMD, started through `adapters/windows/steamcmd.py` and
  `adapters/windows/steamcmd_app.py`, connects to Steam itself.
- `application/update_check.py` and `update_check_scheduler.py` run the mod
  check on their own worker thread, never on the operation lane.
  `repositories/update_check_cache.py` stores the answers in
  `data/update-check.json`.
- `domain/mod_row_state.py` decides the row state.
  `application/mod_inventory.py` merges the local record, the answer, and the
  target record into each row.
- `application/workshop_decision.py` decides which mods go to SteamCMD.
  `application/workshop_verification.py` runs **Verify files**.
- `application/content_proofs.py` and `repositories/content_proofs.py` own the
  content records in `data/content-proofs.json`. Only lane operations write
  them.
- `application/installation_guard.py` is the one write guard for the server
  folder: installation mutex, then a proven `STOPPED` state.
  `PLAIN_APPLY_REQUIRES_GUARD` holds decision D10 (refuse).
- `application/mod_restart_coordinator.py` owns only the order of
  **Update & restart**. It calls the lifecycle stop, the backup, and the
  publication service.
- `application/server_build.py`, `adapters/windows/steamcmd_app.py`, and
  `repositories/server_build_manifest.py` run the server build check.
  `application/steamcmd_guard.py` allows one SteamCMD run of this manager at a
  time.
- `application/startup_recoveries.py` and
  `application/mod_publication_startup.py` run the startup recoveries inside
  the installation guard. One exception: without a configured DayZ server
  folder, the recovery of a profile creation runs without the guard, as before
  D14 (QF-044).

### Online players

- `adapters/a2s_codec.py` parses A2S answers. `adapters/a2s_transport.py`
  holds the shared rules: `127.0.0.1` only, the sender check, and the
  4096-byte cap.
- `adapters/steam_player_query.py` reads the player list.
  `application/online_players.py` answers `get_online_players`.
  `application/external_players.py` matches a server that runs outside the
  manager.
- `frontend/overview_players.js` and `overview_players_panel.js` draw the
  count and the list.

Player names exist only in the bridge answer and the window. Do not log them,
store them, or put them into an error text or a `repr`.
`tests/test_online_players_bridge.py` checks this.

### Guided profile provisioning

- `application/mission_catalog.py` discovers and validates installed missions.
- `application/profile_provisioning.py` owns preflight, staging, publication,
  rollback, recovery, and launch-readiness verification.
- `repositories/provisioning_journal.py` stores durable operation evidence.
- `profile_provisioning_composition.py` keeps provisioning wiring out of the
  main composition root.
- `frontend/profile_create.js` and `frontend/profiles.css` own the guided form.
- `frontend/profile_context.js` remains the sole owner of the active profile
  catalog and persisted selection.

Profile creation uses `provision_profile`; `save_profile` remains the edit path
for existing profiles. Do not add filesystem side effects to ordinary profile
save.

### Backup restoration

- New archives use schema 3 with complete profile reconstruction metadata and
  verified directory inventories. Earlier schemas remain identifiable.
- `frontend/backup_history.js` displays the selected profile's three newest
  archives. Its icons call the existing-profile review in `frontend/restore.js`.
- `frontend/profile_restore.js` owns ZIP browsing and direct reconstruction review.
- `host/runtime.py` wires the native ZIP picker. The host dispatches
  `inspect_backup_archive` to validate the selected source.
- `repositories/selected_backup.py` binds opaque session references to source
  paths and ZIP hashes. Renamed external ZIPs retain their internal identity.
- `application/profile_restores.py` revalidates sources and destination previews.
  Publication and recovery use the separate direct-restore storage and journal.

The browser does not read filesystem paths directly. Keep archive validation,
profile reconstruction and publication checks in Python. Direct restore uses
the shared mutation lane and installation guard, and requires a stopped server.

Run the focused selection, UI, mapping and transaction checks with:

```powershell
.\runnable\.venv\Scripts\python.exe -m unittest tests.test_backup_archive_selection tests.test_backup_reconstruction tests.test_profile_restore_mapping tests.test_profile_restore_service tests.test_profile_restore_bridge tests.test_profile_restore_ui tests.test_restore_ui_dynamic tests.test_ui_host_runtime
```

File hashes and engine readiness do not prove client persistence behavior.
Use a disposable populated server fixture to verify known player/object state
and representative mod persistence before accepting persistence portability.

## Frontend rules

### Section registry

`frontend/sections.js` holds one registration for each workspace section. A
registration names the title, description, navigation group, icon, and `open`
handler. Optional fields say whether the section needs the snapshot, reopens on
a profile change, keeps unsaved edits, or handles operation events.

- Registration order is navigation order.
- A second registration of the same ID is refused.
- The shell loop names no page module. It calls the registry.

The navigation groups and items are built from the registry. To add a section,
add one `registerSection` call for its page module.
`tests/test_section_registry.py` checks the registry.

### Composition and design rules

- `host/assets.py` concatenates every script into one document scope. Add a new
  script there. Declare each global name only once;
  `tests/test_operation_bar_static.py` checks this.
- Keep the content security policy in `frontend/index.html` unchanged. The
  application loads no external resource. `tests/test_ui_shell_static.py`
  checks this.
- Put every static look in the page style files as a class. Do not write a
  `style` attribute in `index.html` or in built markup.
- Set a style property from JavaScript only for a value that is computed at
  run time and that a class cannot hold. There are two cases now:
  - the width of the operation bar's progress fill (`fill.style.width` in
    `frontend/operation_bar_view.js`);
  - the column count of a tweaks table (`--table-columns` in
    `frontend/tweaks_render.js`).

  The content security
  policy allows this (`style-src 'unsafe-inline'`), and
  `tests/test_operation_bar_static.py` pins that policy.
  `tests/test_overview_players_dynamic.py` checks that the Overview with the
  player count holds no `style` attribute.
- `frontend/tokens.css` is the canonical source for form-control colors,
  borders, height, padding, choice size, disabled opacity, and focus color.
  `frontend/styles.css` applies that contract to text inputs, number inputs,
  selects, text areas, checkboxes, and radio buttons. Page-specific style files
  may change layout, width, or an explicitly compact height, but must not
  redefine the control surface, border, text color, or interaction states.
- Mark each control that changes the server or its files with
  `ServerManBusy.mark` when you build it. The shared helper locks marked
  controls while an operation runs or waits, and adds the reason.
- The operation bar is the only global progress indicator. A page does not draw
  a progress bar for a whole operation and does not replace the page content.
- The Overview must fit 1150 × 720 pixels without scrolling in every case of
  `tests/test_overview_fit_dynamic.py`.

### Wording catalogues

Operator-facing text never shows an internal identifier, enum name, raw phase,
or error code. Raw detail stays in **Logs → Manager diagnostics**.

The frontend catalogue is frozen data in four modules:

- `frontend/operation_labels.js`: operation names, phases, and result
  sentences;
- `frontend/operation_messages.js`: error and conflict messages;
- `frontend/diagnostic_labels.js`: state labels, path and process diagnostics,
  and per-mod outcomes;
- `frontend/host_sentences.js`: translation of identifiers inside host
  sentences, and the reasons of recovery blocks.

The backend composes **Manager activity** with
`application/activity_wording.py` and `application/log_activity.py`. Add every
new operation kind, phase, error code, and block reason to both sides.
`tests/test_operator_wording.py`, `tests/test_activity_wording.py`, and
`tests/test_block_reason_wording.py` (block reasons) fail when an entry is
missing, when the two sides differ, or when a text holds a raw identifier.

### File size

Keep each first-party source file at 300 lines or fewer. Split a file before it
grows past that limit. Tests enforce this for every frontend file. Some older
Python modules are still above 300 lines; split one before you add to it.

## Change checklist

1. Preserve the movable `runnable/` boundary.
2. Keep generated state outside tracked source files.
3. Validate every JavaScript-to-Python call through a named bridge method.
4. Keep bridge changes additive.
5. Add catalogue entries on both sides for new operator-facing values.
6. Add focused tests for changed behavior.
7. Run the complete test suite before release-oriented work.
8. Update the operator guide and its pictures when visible behavior changes.
