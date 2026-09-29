# DayZ-ServerMan

DayZ-ServerMan is a movable Windows desktop manager for local DayZ servers.
It uses Python and the Windows WebView2 runtime. It does not need a build step.

## Use the application

1. Copy the complete [`runnable/`](./runnable/) directory.
2. Run `DayZ-ServerMan.py` inside that directory.
3. Follow the [operator quick start](./runnable/README.md).

The target computer needs Python 3.12 or 3.13. The first launch creates a
project-local `.venv` and installs the packages in `requirements.txt`.

## Repository map

- [`runnable/`](./runnable/) — complete movable application
- [`docs/`](./docs/) — operations and development documentation
- [`tests/`](./tests/) — automated tests

Start from the repository root with:

```text
py runnable\DayZ-ServerMan.py
```

<!-- shot:root-01 -->
![The application window after the launcher starts DayZ-ServerMan](img/root-01.png)

See the [documentation index](./docs/README.md) for deeper guidance.

## License

DayZ-ServerMan uses the [MIT License](./LICENSE).

The license covers this project's source code only. DayZ server files, Steam
Workshop mods, and third-party assets keep their own terms.
