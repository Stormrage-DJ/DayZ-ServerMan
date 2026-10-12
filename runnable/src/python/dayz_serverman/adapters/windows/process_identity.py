"""Read exact held identities and retain borrowed-query handle custody."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as W
from .process_bindings import Kernel, NativeError
from .process_custody import Custody
from .process_evidence import ProcessIdentityUnavailable


def from_handle(handle, kernel: Kernel | None = None) -> str:
    """Read creation identity without acquiring or closing the caller's handle."""
    kernel = kernel or Kernel()
    values = [W.FILETIME() for _ in range(4)]
    # Creation time binds evidence to this exact held process reference.
    kernel.checked("GetProcessTimes", handle, *(ctypes.byref(value) for value in values))
    creation = values[0]
    return f"windows-filetime:{(creation.dwHighDateTime << 32) | creation.dwLowDateTime}"


def held(process, kernel: Kernel | None = None) -> str:
    """Preserve the legacy non-Windows sentinel and exact held Windows query."""
    if os.name != "nt":
        return f"pid:{process.pid}:owned"
    handle = process.process_reference.value() if hasattr(process, "process_reference") else process._handle
    return from_handle(handle, kernel)


def for_pid(pid: int, custody: Custody | None = None, kernel: Kernel | None = None) -> str:
    """Borrow query-only authority, then check its particular reference release."""
    custody, kernel = custody or Custody(), kernel or Kernel()
    reference = custody.reserve("process-query", str(pid), kernel.close)
    marker, failed = len(custody.secondary), False
    value = None
    # Query-only access cannot grant process containment authority.
    try:
        try:
            handle = kernel.acquire(reference, "OpenProcess", 0x1000, False, pid)
        except NativeError as error:
            raise ProcessIdentityUnavailable(error.error_code) from error
        value = from_handle(handle, kernel)
    except BaseException as error:
        failed = True
        custody.fault(error)
    finally:
        # Release this borrowed reference even when identity acquisition failed.
        custody.release(reference)
    if failed or len(custody.secondary) != marker:
        custody.raise_failure()
    return value
