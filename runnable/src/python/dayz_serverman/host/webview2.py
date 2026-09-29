"""Windows WebView2 prerequisite detection and native error display."""

from __future__ import annotations

import ctypes
import winreg


# Edge Update client identifier that names the WebView2 runtime
WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
# Registry locations checked in order: machine-wide views then per-user
REGISTRY_LOCATIONS = (
    (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
    (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_CURRENT_USER, 0),
)


def find_webview2_version() -> str | None:
    """Return the installed WebView2 runtime version, or None when absent."""
    subkey = rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"
    # Probe each supported registry view for a runtime version
    for hive, view in REGISTRY_LOCATIONS:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
        # Ignore registry misses and keep checking other views
        except OSError:
            continue
        # Reject placeholder or empty version values
        if isinstance(version, str) and version.strip() and version != "0.0.0.0":
            return version.strip()
    return None


def show_native_error(message: str) -> None:
    """Display a native error message box."""
    ctypes.windll.user32.MessageBoxW(0, message, "DayZ-ServerMan", 0x10)
