"""Resolve editor geometry from the main window's native monitor work area."""

from __future__ import annotations

import ctypes
import math
from ctypes import wintypes
from typing import Any


class _MonitorInfo(ctypes.Structure):
    """Carry the monitor and work rectangles from GetMonitorInfoW."""

    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class WindowGeometryError(OSError):
    """Refuse an unproved display context or an editor that cannot fit safely."""


def _system_scale(user32: Any, handle: int | None) -> float:
    """Prove the scale used by the installed system-aware Chromium constructor."""
    try:
        # The process and callback thread must agree with the GUI creation context
        query = ctypes.windll.shcore.GetProcessDpiAwareness
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)]
        query.restype = ctypes.c_long
        awareness = ctypes.c_int()
        if query(None, ctypes.byref(awareness)) != 0 or awareness.value != 1:
            raise WindowGeometryError("The map editor requires a system-aware display context.")
        thread_context = user32.GetThreadDpiAwarenessContext
        thread_context.argtypes = []
        thread_context.restype = wintypes.HANDLE
        read_awareness = user32.GetAwarenessFromDpiAwarenessContext
        read_awareness.argtypes = [wintypes.HANDLE]
        read_awareness.restype = ctypes.c_int
        if read_awareness(thread_context()) != 1:
            raise WindowGeometryError("The map editor requires a system-aware display context.")
        if handle:
            window_context = user32.GetWindowDpiAwarenessContext
            window_context.argtypes = [wintypes.HWND]
            window_context.restype = wintypes.HANDLE
            if read_awareness(window_context(handle)) != 1:
                raise WindowGeometryError("The map editor requires a system-aware display context.")
        # Missing GetDpiForWindow is the installed constructor's actual exception fallback
        window_dpi = getattr(user32, "GetDpiForWindow", None)
        if window_dpi is None:
            return 1.0
        window_dpi.argtypes = [wintypes.HWND]
        window_dpi.restype = wintypes.UINT
        main_dpi = window_dpi(handle) if handle else None
        system_dpi = getattr(user32, "GetDpiForSystem", None)
        if system_dpi is not None:
            system_dpi.argtypes = []
            system_dpi.restype = wintypes.UINT
            dpi = system_dpi()
        else:
            dpi = main_dpi
        # A present zero query or a scale mismatch is never an assumed 96 DPI
        if (dpi is None or not math.isfinite(dpi) or dpi <= 0 or
                (handle and (main_dpi is None or main_dpi <= 0 or main_dpi != dpi))):
            raise WindowGeometryError("The map editor could not verify the display scale.")
        # Windows logical coordinates use a 96-DPI baseline
        return dpi / 96.0
    except WindowGeometryError:
        raise
    except (AttributeError, OSError, TypeError, ValueError):
        raise WindowGeometryError("The map editor could not verify the display scale.") from None


def _logical_work_area(area: tuple[int, int, int, int], scale: float) -> tuple[int, int, int, int]:
    """Convert every signed endpoint inward to the proven constructor input units."""
    left, top, right, bottom = area
    return (math.ceil(left / scale), math.ceil(top / scale),
            math.floor(right / scale), math.floor(bottom / scale))


def window_work_area(window: Any) -> tuple[int, int, int, int]:
    """Return logical rcWork endpoints for the accepted system-aware Edge runtime."""
    # pywebview's Windows native object exposes a .NET window handle
    handle = None
    native = getattr(window, "native", None)
    if native is not None:
        try:
            handle = native.Handle.ToInt64()
        except (AttributeError, TypeError, ValueError):
            handle = None
    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    # Unknown handles select the primary work area; known handles select the nearest monitor
    monitor = user32.MonitorFromWindow(handle, 2 if handle else 1)
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise WindowGeometryError("The map editor could not read the monitor work area.")
    work = info.rcWork
    return _logical_work_area((work.left, work.top, work.right, work.bottom),
                              _system_scale(user32, handle))


def editor_geometry(work_area: tuple[int, int, int, int]) -> dict[str, int]:
    """Centre the accepted logical size, refusing space below its minimum."""
    left, top, right, bottom = work_area
    if right - left < 960 or bottom - top < 600:
        raise WindowGeometryError(
            "The map editor cannot fit in this monitor's work area at the current display scale.")
    width = min(1280, right - left)
    height = min(800, bottom - top)
    return {"width": width, "height": height,
            "x": left + (right - left - width) // 2,
            "y": top + (bottom - top - height) // 2}
