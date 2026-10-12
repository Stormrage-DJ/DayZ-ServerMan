"""Accepted editor bounds and the primary-monitor native fallback."""

from __future__ import annotations

import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dayz_serverman.adapters.windows.window_work_area import (
    WindowGeometryError, _logical_work_area, editor_geometry, window_work_area,
)


def native_fixture(area=(0, 0, 1920, 1080), dpi=96, process=1, thread=1, main=1):
    """Inject native monitor, awareness and DPI results without changing host context."""
    user32 = SimpleNamespace(
        MonitorFromWindow=Mock(return_value=77), GetMonitorInfoW=Mock(),
        GetThreadDpiAwarenessContext=Mock(return_value=2),
        GetWindowDpiAwarenessContext=Mock(return_value=3),
        GetAwarenessFromDpiAwarenessContext=Mock(side_effect=lambda context: {2: thread, 3: main}.get(context, -1)),
        GetDpiForSystem=Mock(return_value=dpi), GetDpiForWindow=Mock(return_value=dpi))
    def fill_work_area(_, pointer):
        """Return the physical work rectangle, excluding any taskbar."""
        work = pointer._obj.rcWork
        work.left, work.top, work.right, work.bottom = area
        return True
    def fill_awareness(_, pointer):
        """Prove or refuse the current process context."""
        pointer._obj.value = process
        return 0
    user32.GetMonitorInfoW.side_effect = fill_work_area
    shcore = SimpleNamespace(GetProcessDpiAwareness=Mock(side_effect=fill_awareness))
    known = SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 42)))
    return user32, shcore, known


def read_area(user32, shcore, window):
    """Run the actual adapter against the injected native boundary."""
    with patch.object(ctypes.windll, "user32", user32), patch.object(ctypes.windll, "shcore", shcore):
        return window_work_area(window)


class EditorGeometryTests(unittest.TestCase):
    """Cover nominal, clamped and minimum dimensions on offset monitors."""

    def test_size_partitions_and_centring(self):
        """Respect nominal bounds, work-area clamps and the accepted minimum."""
        cases = [((0, 0, 1920, 1080), (1280, 800, 320, 140)),
                 ((-1200, 50, 0, 800), (1200, 750, -1200, 50)),
                 ((0, 0, 960, 600), (960, 600, 0, 0))]
        for area, expected in cases:
            with self.subTest(area=area):
                geometry = editor_geometry(area)
                self.assertEqual(tuple(geometry[k] for k in ("width", "height", "x", "y")), expected)
        for area in ((0, 0, 800, 500), (0, 0, 959, 600), (0, 0, 960, 599)):
            with self.subTest(area=area), self.assertRaises(WindowGeometryError):
                editor_geometry(area)

    def test_native_handle_and_unknown_handle_use_work_rectangle(self):
        """Use rcWork and choose the primary monitor when no handle is available."""
        user32, shcore, known = native_fixture(area=(-1920, 0, 0, 1040))
        for window, handle, flag in ((known, 42, 2), (SimpleNamespace(native=None), None, 1)):
            self.assertEqual(read_area(user32, shcore, window), (-1920, 0, 0, 1040))
            user32.MonitorFromWindow.assert_called_with(handle, flag)

    def test_native_lookup_failure_is_explicit(self):
        """Do not invent a screen rectangle when Windows cannot resolve it."""
        user32, shcore, known = native_fixture()
        for failure in ("MonitorFromWindow", "GetMonitorInfoW"):
            with self.subTest(failure=failure):
                query = getattr(user32, failure)
                previous = query.side_effect
                query.side_effect = None
                query.return_value = 0
                with self.assertRaises(OSError):
                    read_area(user32, shcore, known)
                query.side_effect = previous
                query.return_value = 77 if failure == "MonitorFromWindow" else True

    def test_scaled_signed_and_mixed_monitor_geometry_stays_physically_contained(self):
        """Use system scale on every monitor and convert both origins and extents inward."""
        cases = [((0, 0, 1920, 1080), 96, (1280, 800, 320, 140)),
                 ((0, 0, 2400, 1350), 120, (1280, 800, 320, 140)),
                 ((0, 0, 2880, 1620), 144, (1280, 800, 320, 140)),
                 ((-2400, 125, 0, 1475), 120, (1280, 800, -1600, 240)),
                 ((1920, 0, 3840, 1080), 144, (1280, 720, 1280, 0)),
                 ((0, 0, 1440, 900), 144, (960, 600, 0, 0))]
        for physical, dpi, expected in cases:
            with self.subTest(physical=physical, dpi=dpi):
                user32, shcore, known = native_fixture(area=physical, dpi=dpi)
                geometry = editor_geometry(read_area(user32, shcore, known))
                self.assertEqual(tuple(geometry[k] for k in ("width", "height", "x", "y")), expected)
                scale = dpi / 96
                x, y = int(geometry["x"] * scale), int(geometry["y"] * scale)
                self.assertGreaterEqual(x, physical[0])
                self.assertGreaterEqual(y, physical[1])
                self.assertLessEqual(x + int(geometry["width"] * scale), physical[2])
                self.assertLessEqual(y + int(geometry["height"] * scale), physical[3])
        # A monitor advertised at 150 percent still uses the established system 125 percent scale
        user32, shcore, known = native_fixture(area=(-2400, 125, 0, 1475), dpi=120)
        self.assertEqual(read_area(user32, shcore, known), (-1920, 100, 0, 1180))
        user32.GetDpiForSystem.assert_called_once_with()

    def test_fractional_signed_endpoint_rounding_and_minimum_refusal(self):
        """Inward rounding must contain physical edges even around negative origins."""
        cases = [((-2401, -3, -1, 1348), 1.25, (-1920, -2, -1, 1078)),
                 ((-2881, 1, -1, 1622), 1.5, (-1920, 1, -1, 1081))]
        for physical, scale, expected in cases:
            logical = _logical_work_area(physical, scale)
            self.assertEqual(logical, expected)
            geometry = editor_geometry(logical)
            x, y = int(geometry["x"] * scale), int(geometry["y"] * scale)
            self.assertGreaterEqual(x, physical[0])
            self.assertGreaterEqual(y, physical[1])
            self.assertLessEqual(x + int(geometry["width"] * scale), physical[2])
            self.assertLessEqual(y + int(geometry["height"] * scale), physical[3])
        with self.assertRaises(WindowGeometryError):
            editor_geometry(_logical_work_area((0, 0, 1439, 900), 1.5))

    def test_only_actual_constructor_fallback_or_proven_main_dpi_is_used(self):
        """Missing native functions have the exact installed fallback, never a guessed 96 DPI."""
        user32, shcore, known = native_fixture(dpi=120)
        del user32.GetDpiForWindow
        self.assertEqual(read_area(user32, shcore, known), (0, 0, 1920, 1080))
        user32.GetDpiForSystem.assert_not_called()
        user32, shcore, known = native_fixture(dpi=120)
        del user32.GetDpiForSystem
        self.assertEqual(read_area(user32, shcore, known), (0, 0, 1536, 864))
        with self.assertRaises(WindowGeometryError):
            read_area(user32, shcore, SimpleNamespace(native=None))

    def test_zero_query_mismatch_unknown_failure_and_overridden_context_refuse(self):
        """No missing proof may silently select a different constructor scale."""
        for field, value in (("GetDpiForSystem", 0), ("GetDpiForWindow", 0),
                             ("GetDpiForWindow", 144), ("GetDpiForSystem", float("nan"))):
            with self.subTest(field=field, value=value):
                user32, shcore, known = native_fixture(dpi=120)
                getattr(user32, field).return_value = value
                with self.assertRaises(WindowGeometryError):
                    read_area(user32, shcore, known)
        for context in ({"process": 2}, {"thread": 2}, {"main": 2}, {"process": 0}, {"thread": -1}):
            with self.subTest(context=context):
                user32, shcore, known = native_fixture(**context)
                with self.assertRaises(WindowGeometryError):
                    read_area(user32, shcore, known)
        for missing in ("GetThreadDpiAwarenessContext", "GetAwarenessFromDpiAwarenessContext"):
            user32, shcore, known = native_fixture()
            delattr(user32, missing)
            with self.assertRaises(WindowGeometryError):
                read_area(user32, shcore, known)
        user32, shcore, known = native_fixture()
        user32.GetDpiForSystem.side_effect = OSError("query unavailable")
        with self.assertRaises(WindowGeometryError):
            read_area(user32, shcore, known)
        user32, shcore, known = native_fixture()
        shcore.GetProcessDpiAwareness.side_effect = None
        shcore.GetProcessDpiAwareness.return_value = 5
        with self.assertRaises(WindowGeometryError):
            read_area(user32, shcore, known)
