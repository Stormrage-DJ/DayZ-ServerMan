# Development guide

## Repository layout

```text
DayZ-ServerMan/
|-- README.md
|-- .github/workflows/tests.yml
|-- requirements-dev.txt
|-- docs/
|-- img/
|-- legal/
|-- tools/
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
- `requirements-dev.txt` pins the development tools. The application does not
  use them.
- `legal/THIRD-PARTY-NOTICES.md` lists third-party components with their
  licence evidence under `legal/third-party/`.
- `tools/` holds development scripts that are not shipped, such as the
  generator of the Windows ordinal case table.

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

Install the development tools into the same environment once:

```powershell
.\runnable\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

A new development tool needs a licence intake first. Add its licence file and
an entry to `legal/THIRD-PARTY-NOTICES.md` in the same change.

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

`tests/test_undefined_names.py` runs pyflakes over the application source,
the starter scripts, and the tests. It reports only names that a module uses
but never defines or imports, for example a missing import. Such a name fails
only when its line runs, so ordinary tests can miss it. A star import also
fails the check, because it hides such names. Without pyflakes the
test is skipped on a local machine and fails in CI.

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

Close Edge completely before you run the Edge modules. An Edge that keeps
running in the background, for example one started with
`--no-startup-window`, makes the headless tests fail. Check the Task Manager
for `msedge.exe`.

### Continuous integration

`.github/workflows/tests.yml` runs on every push and pull request, on
`windows-latest` with Python 3.12. It uses `actions/checkout@v5` and
`actions/setup-python@v6`. It has two jobs:

- **Unit and static tests** (time limit 20 minutes) installs
  `requirements-dev.txt` and compile-checks the Python source. Then it runs
  every test module that does not start Edge, including the undefined-name
  check. A failing test fails the run.
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
- `cli/` runs the same bridge calls from a terminal or a script.
- `session.py` and `session_observer.py` open the owner and observer
  sessions of the window and the commands.

The frontend uses small page-specific JavaScript files and shared context
modules. Python remains authoritative for filesystem access, process control,
network access, validation, and persistent mutations.

Bridge changes are additive: new methods and new response fields only.
`CONTRACT_VERSION` stays 1, and every handler requires the exact request field
set. The command line did not change this rule. It added no bridge method, and
the 60-method pins in `tests/test_readonly_host_api.py` and
`tests/test_bridge_contracts.py` stay unchanged. The JSON output of the
command line has its own `cli_version`, which also grows only by new fields.

### Command line and sessions

The command line is the package `cli/`. The starter routes a first argument
`--cli` to `cli.main` after the runtime setup; every other command line opens
the window as before. `python -m dayz_serverman --cli ...` does the same.

Dependency direction: starter → `cli` → `session.py` and `session_observer.py`
→ `composition.py` → application, repositories, adapters.

- `cli` never imports `host` or pywebview, and it needs no WebView2.
- `host` and `application` never import `cli`. Application code does not
  know whether a call came from the window or a command.
- `cli/`, the session modules, and the new Windows adapters import no network
  module. `tests/test_cli_network_imports.py` checks this.

Each run opens one session (`composition_model.SessionMode`):

- **Owner** (`session.open_owner_session`): the window and every writing
  command. It creates `data/` and takes the instance lock
  (`adapters/windows/instance_lock.py`). This lock is a byte lock on
  `data/instance.lock` and a named mutex for the root. Then the session writes
  `data/instance-holder.json`, creates `data/server-folders.lock`, and builds
  the composition with its recoveries.
  The window passes `require_byte_range_lock=False`; a command passes `True`.
- **Observer** (`session_observer.open_observer_session`): every read command.
  It takes no lock, creates no folder or file, skips the startup recoveries,
  and uses a null logger. Its facade holds only `OBSERVER_READ_METHODS`
  (`bridge_composition.py`), and its lane refuses every submit. Repositories
  ignore a staging file beside a record and read only the published record.
  Each call runs under the reader side of the server-folder lock, except the
  calls in `READER_EXEMPT_METHODS`.

Lock order inside a session: instance lock, operation lane, installation
mutex, server-folder writer side, SteamCMD guard. One exception: an apply and
restart takes the writer side before the installation mutex. Owner steps take
the writer side through `application/folder_writer_scope.py`.

The ownership record is `repositories/server_ownership.py` with
`application/lifecycle_ownership.py` (adoption). Only owner sessions write it.

Main modules of `cli/`:

- `parser.py`, `command_table.py`, and `registry.py`: the parser, the command
  table, and its types;
- `runner.py`: one command from the parse to the exit code;
- `exit_codes.py`: the exit code of each error code;
- `output.py`, `waiter.py`, `confirm.py`: the output, the progress, and the
  question;
- `cli/commands/`: one module or more for each noun.

The CLI calls every action through `BridgeFacade.dispatch`,
as the window does. So every check of a handler table reaches it unchanged.

### Shared file access

Every file read and every file replace or rename in the package goes through
`adapters/windows/shared_files.py`. Its opener lets other processes replace a
file while it is open. Its `replace_file` uses a POSIX-semantics rename, with a
short retry where the volume refuses it. Folder renames use
`rename_directory`, which retries for about one second. The module imports
nothing from the package.

Do not call `os.replace`, `os.rename`, `Path.replace`, `Path.rename`,
`shutil.move`, a `shutil` copy, a read-mode `open`, `read_bytes`, or
`read_text` elsewhere. `tests/test_shared_file_scan.py` scans the package and
compares the result with a reviewed allowlist. Each allowlist entry names a
line text and one of five reason classes. A new entry needs the Architect.

A test that patched `os.replace` or a read call moves its patch target to the
name that the module imports from `shared_files`. Its assertions do not
change.

### Add a command

1. Add the command to `cli/command_table.py` with `read(...)` or
   `write(...)`. Give its path, plan phase, bridge methods, options, question
   (`Confirm`), profile rule, and handler as `"module:function"`. A writing
   command whose arguments need data also names a `prestep`.
2. Add its help sentences to `cli/help_wording.py`.
3. Write the handler in `cli/commands/`. Use the flow helpers for the
   revision reads, the recovery gate, the question, and the waiter. Put every
   state refusal that the command can decide before the question.
4. Take operator text from the wording modules. A copy of a window text needs
   a test against its frontend literal. Text output shows no internal
   identifier; `tests/test_cli_input_names.py` checks the input names.
5. Update `tests/test_cli_parity_matrix.py`. Every bridge method must be in a
   command, in `INTERNAL_METHODS`, in `EXCLUSION_REASONS` with its reason, or
   in `PENDING_TASK_10` as "pending task 10: <planned command>"
   (`cli/registry.py`). A pending method is not an exclusion, and the test pins
   each pending entry. The "Confirm" set must match the window.
6. A read command may call only `OBSERVER_READ_METHODS`. A new read method
   needs a reader class. Exempt it from the reader side only with a test that
   proves it opens nothing inside a folder that an owner step renames or
   removes.
7. Map new error codes to the existing exit codes in `cli/exit_codes.py`. A
   new CLI error code name needs the Architect. A new exit code number, or a
   moved case, needs the Product Owner.
8. Add tests for the parser, each exit code, text and `--json`, `--yes`, and
   no terminal. No test uses the network, runs SteamCMD, or starts DayZ.

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

The frontend catalogue is frozen data in five modules:

- `frontend/operation_labels.js`: operation names, phases, and result
  sentences;
- `frontend/operation_messages.js`: error and conflict messages;
- `frontend/operation_failures.js`: failed and cancelled operation results,
  guard codes, and lifecycle and restart failure sentences;
- `frontend/diagnostic_labels.js`: state labels, path and process diagnostics,
  and per-mod outcomes;
- `frontend/host_sentences.js`: translation of identifiers inside host
  sentences, and the reasons of recovery blocks.

The backend composes **Manager activity** with
`application/activity_wording.py` and `application/log_activity.py`.
`application/host_sentence_wording.py` holds the role labels, the identifier
check, and the block reasons and their texts. Add every new operation kind,
phase, error code, and block reason to both sides.
`tests/test_operator_wording.py`, `tests/test_activity_wording.py`, and
`tests/test_block_reason_wording.py` (block reasons) fail when an entry is
missing, when the two sides differ, or when a text holds a raw identifier.

### File size

Keep each first-party source file at 300 lines or fewer. Split a file before it
grows past that limit. Tests enforce this for every frontend file, and
`tests/test_cli_sizes.py` for every file in `cli/` and for
`phase_wording.py` and `field_wording.py`. Other Python modules follow the
rule by review.

Some older Python modules are still above 300 lines. Such a file must
not grow: split it before you add to it. A change that keeps its line count
needs a real simplification, never removed comments or joined statements.

## Change checklist

1. Preserve the movable `runnable/` boundary.
2. Keep generated state outside tracked source files.
3. Validate every JavaScript-to-Python call through a named bridge method.
4. Keep bridge changes additive. Keep the command line on the same bridge
   calls, and keep the parity test complete.
5. Add catalogue entries on both sides for new operator-facing values.
6. Add focused tests for changed behavior. Route every file read, replace,
   and rename through `shared_files`.
7. Run the complete test suite before release-oriented work. Keep the
   undefined-name check green. Add the missing import; do not add an exemption
   or a star import.
8. Update the operator guide and its pictures when visible behavior changes,
   also the command line section and its exit codes.
