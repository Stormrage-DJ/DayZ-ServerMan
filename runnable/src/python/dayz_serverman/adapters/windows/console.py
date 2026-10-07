"""Whether a stream is an interactive console (QF-30).

On Windows `isatty` is also true for the NUL device (`< NUL`, `subprocess.DEVNULL`), so a
command would ask a question that nobody can answer. A stream counts as a terminal only when
`GetConsoleMode` succeeds on its handle. A stream without a file descriptor (a test double)
keeps the answer of its own `isatty`.
"""

from __future__ import annotations

import ctypes
import io
import os
from typing import Any


def is_terminal(stream: Any) -> bool:
    """Report whether a stream is an interactive console; a missing stream or method is not."""
    check = getattr(stream, "isatty", None)
    try:
        if check is None or not check():
            return False
    except (OSError, ValueError):
        return False
    if os.name != "nt":
        return True
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        # No descriptor: an in-memory stream, whose own isatty answer stands
        return True
    return is_console_descriptor(descriptor)


def is_console_descriptor(descriptor: int) -> bool:
    """Report whether GetConsoleMode succeeds on the handle of a file descriptor (Windows only)."""
    try:
        import msvcrt

        handle = msvcrt.get_osfhandle(descriptor)
        mode = ctypes.c_uint32()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetConsoleMode.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        kernel32.GetConsoleMode.restype = ctypes.c_int
        return bool(kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))
    except (ImportError, OSError, ValueError, AttributeError):
        return False
