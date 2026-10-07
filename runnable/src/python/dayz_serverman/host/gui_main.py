"""Window start path of the starter and of `python -m dayz_serverman`, behind the instance lock (5.2)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..adapters.windows.instance_lock import InstanceActive, InstanceLockUnsupported
from ..session import GUI_DRAIN_SECONDS, GUI_UNSUPPORTED, gui_refusal_text, open_owner_session
from .runtime import launch_application
from .webview2 import show_native_error


# Exit codes of the two refusals before any window opens
EXIT_INSTANCE_ACTIVE = 3
EXIT_LOCK_UNSUPPORTED = 1


def run_window(
    manager_root: Path | None, *, smoke: bool = False,
    launch: Callable[..., int] | None = None, **launch_options: Any,
) -> int:
    """Take the instance lock as the window, then launch it; refuse a second instance (P6/OD2).

    `launch` defaults to the WebView2 host; a test passes a recorder.
    """
    try:
        session = open_owner_session(manager_root, "window", require_byte_range_lock=False)
    except InstanceActive as refusal:
        return _refuse("INSTANCE_ACTIVE", gui_refusal_text(refusal.holder), smoke, EXIT_INSTANCE_ACTIVE)
    except InstanceLockUnsupported:
        return _refuse("INSTANCE_LOCK_UNSUPPORTED", GUI_UNSUPPORTED, smoke, EXIT_LOCK_UNSUPPORTED)
    try:
        return (launch or launch_application)(session.composition, smoke=smoke, **launch_options)
    finally:
        # Release owned resources even when the host fails to start
        session.close(drain_seconds=GUI_DRAIN_SECONDS)


def _refuse(code: str, message: str, smoke: bool, exit_code: int) -> int:
    """Tell the operator why no window opens: one JSON line in smoke mode, else a native message box."""
    if smoke:
        print(json.dumps({"error": code, "message": message}))
    else:
        show_native_error(message)
    return exit_code
