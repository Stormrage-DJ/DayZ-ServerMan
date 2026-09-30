# Development guide

## Repository layout

```text
DayZ-ServerMan/
|-- README.md
|-- docs/
|-- runnable/
|   |-- DayZ-ServerMan.py
|   |-- requirements.txt
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

Start the application once to create its local environment. Then run:

```text
.\runnable\.venv\Scripts\python.exe -m unittest discover -s tests
```

Run a focused module with:

```text
.\runnable\.venv\Scripts\python.exe -m unittest tests.test_lifecycle_bridge
```

Compile-check the Python source with:

```text
.\runnable\.venv\Scripts\python.exe -m compileall -q runnable\src\python
```

Some dynamic UI tests start Microsoft Edge with a temporary user-data
directory. An Edge process that retains that directory can cause a Windows
lock-file cleanup error. Treat this as an environment failure unless the test
also reports a functional assertion failure.

## Source boundaries

The Python package follows these broad responsibilities:

- `application/` coordinates user operations and domain services.
- `domain/` defines validated values and behavior rules.
- `repositories/` owns local persistence and file transactions.
- `adapters/` contains Windows and external-process integration.
- `bridge/` validates calls between JavaScript and Python.
- `host/` exposes the named WebView2 API and composes frontend assets.

The frontend uses small page-specific JavaScript files and shared context
modules. Python remains authoritative for filesystem access, process control,
validation, and persistent mutations.

### Frontend control styling

`frontend/tokens.css` is the canonical source for form-control colors, borders,
height, padding, choice size, disabled opacity, and focus color.
`frontend/styles.css` applies that contract to text inputs, number inputs,
selects, text areas, checkboxes, and radio buttons. Page-specific style files
may change layout, width, or an explicitly compact height, but must not redefine
the control surface, border, text color, or interaction states.

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

## Change checklist

1. Preserve the movable `runnable/` boundary.
2. Keep generated state outside tracked source files.
3. Validate every JavaScript-to-Python call through a named bridge method.
4. Add focused tests for changed behavior.
5. Run the complete test suite before release-oriented work.
6. Update the operator guide when visible behavior changes.
