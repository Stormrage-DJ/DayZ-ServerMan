"""pywebview runtime binding for the operational desktop application."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..composition import ApplicationComposition
from .api import HostApi
from .assets import compose_shell_html, resolve_frontend_root
from .shutdown import SafeCloseController
from .webview2 import find_webview2_version, show_native_error


def _smoke_probe(
    window: Any,
    close_controller: SafeCloseController,
    version: str,
    hold_seconds: float,
) -> None:
    """Report smoke-test readiness and close the window after the hold time."""
    # Wait for the shell document to finish loading
    window.events.loaded.wait(15)
    # Cap the readiness wait so a broken shell fails quickly
    deadline = time.monotonic() + 10
    ready = False
    operational_ready = False
    # Poll until the shell exposes its markers or the deadline passes
    while time.monotonic() < deadline:
        ready = window.evaluate_js("document.body.dataset.shellReady === 'true'") is True
        operational_ready = window.evaluate_js(
            "Boolean(document.querySelector('.overview-controls, .operation-panel'))"
        ) is True
        if ready and operational_ready:
            break
        time.sleep(0.1)
    # Emit one JSON line that the smoke harness can parse
    print(
        json.dumps(
            {
                "pid": os.getpid(),
                "shell_ready": ready,
                "operational_ready": operational_ready,
                "http_server": False,
                "renderer": "edgechromium",
                "webview2_version": version,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    # Keep the window open for the requested hold time
    time.sleep(max(0.0, hold_seconds))
    # Drain the backend and close the window
    close_controller.close_after_shutdown(5.0)


def launch_application(
    composition: ApplicationComposition,
    *,
    frontend_root: Path | None = None,
    hidden: bool = False,
    smoke: bool = False,
    hold_seconds: float = 0.0,
    webview_module: Any | None = None,
) -> int:
    """Start the WebView2 desktop application and return its exit code."""
    version = find_webview2_version()
    # Require the Evergreen WebView2 runtime before creating a window
    if version is None:
        message = "Microsoft Edge WebView2 Runtime is required. Install Evergreen WebView2 and try again."
        if smoke:
            print(json.dumps({"error": "WEBVIEW2_MISSING", "message": message}))
        else:
            show_native_error(message)
        return 2

    webview = webview_module
    # Fall back to importing pywebview when none was injected
    if webview is None:
        import webview as imported_webview

        webview = imported_webview
    assets = frontend_root or resolve_frontend_root(composition.paths.root)
    # Disable downloads, file URLs, external links, and default menus
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["SHOW_DEFAULT_MENUS"] = False
    # Expose only the named host methods to the browser runtime
    host_api = HostApi(composition.host_bridge)
    # Create the single desktop window hosting the composed shell
    window = webview.create_window(
        "DayZ-ServerMan",
        html=compose_shell_html(assets),
        js_api=host_api,
        width=1180,
        height=760,
        min_size=(800, 560),
        hidden=hidden,
        confirm_close=False,
        text_select=True,
        zoomable=False,
    )
    if window is None:
        raise RuntimeError("pywebview did not create a window")
    # Resolve dialog constants for both pywebview API generations
    dialog_types = getattr(webview, "FileDialog", None)
    folder_dialog = (
        dialog_types.FOLDER if dialog_types is not None else webview.FOLDER_DIALOG
    )
    open_dialog = (
        dialog_types.OPEN if dialog_types is not None else webview.OPEN_DIALOG
    )
    def select_legacy_folder() -> str | None:
        """Ask the operator for a legacy DayZ root folder."""
        selected = window.create_file_dialog(folder_dialog, allow_multiple=False)
        # Treat a dismissed dialog as no selection
        if not selected:
            return None
        # Normalize sequence results to a single path
        if isinstance(selected, (tuple, list)):
            return str(selected[0]) if selected else None
        return str(selected)
    host_api._set_legacy_folder_selector(select_legacy_folder)
    def select_settings_path(_role: str, kind: str) -> str | None:
        """Ask the operator for a settings folder or executable file."""
        # Use the folder dialog unless an executable file is requested
        dialog = folder_dialog if kind == "folder" else open_dialog
        options: dict[str, Any] = {"allow_multiple": False}
        # Limit file dialog selections to executables
        if kind == "file":
            options["file_types"] = ("Executable files (*.exe)",)
        selected = window.create_file_dialog(dialog, **options)
        # Treat a dismissed dialog as no selection
        if not selected:
            return None
        # Normalize sequence results to a single path
        if isinstance(selected, (tuple, list)):
            return str(selected[0]) if selected else None
        return str(selected)
    host_api._set_settings_path_selector(select_settings_path)
    # Guard native closes so backend work can drain first
    close_controller = SafeCloseController(window, composition.shutdown)
    closing_event = getattr(getattr(window, "events", None), "closing", None)
    if closing_event is not None:
        closing_event += close_controller.on_closing
    # Run the smoke probe instead of the user shell when requested
    callback = (
        lambda: _smoke_probe(window, close_controller, version, hold_seconds)
    ) if smoke else None
    # Keep scheduled lifecycle actions running while the window lives
    composition.schedules.start()
    try:
        # Start the embedded Edge Chromium window and block until it closes
        webview.start(
            callback,
            gui="edgechromium",
            debug=False,
            http_server=False,
            private_mode=False,
            storage_path=str(composition.paths.webview2),
        )
    finally:
        # Stop scheduling after the window closes
        composition.schedules.stop()
    return 0
