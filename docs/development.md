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

## Change checklist

1. Preserve the movable `runnable/` boundary.
2. Keep generated state outside tracked source files.
3. Validate every JavaScript-to-Python call through a named bridge method.
4. Add focused tests for changed behavior.
5. Run the complete test suite before release-oriented work.
6. Update the operator guide when visible behavior changes.
