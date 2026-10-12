"""Pointer-safe Windows process, job and creation layouts and bindings."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W


class NativeError(OSError):
    """Retain the failing native operation and its immediate last-error code."""
    def __init__(self, operation: str, code: int) -> None:
        """Preserve native failure details through catch-compatible errors."""
        super().__init__(code, operation + " failed")
        self.operation, self.error_code, self.winerror = operation, code, code


class Security(ctypes.Structure):
    """SECURITY_ATTRIBUTES controls inheritance of an exact acquired handle."""
    _fields_ = [("length", W.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", W.BOOL)]


class Startup(ctypes.Structure):
    """STARTUPINFOW carries only explicit standard handles for the child."""
    _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR),
                ("title", W.LPWSTR), ("x", W.DWORD), ("y", W.DWORD),
                ("xsize", W.DWORD), ("ysize", W.DWORD), ("xchars", W.DWORD),
                ("ychars", W.DWORD), ("fill", W.DWORD), ("flags", W.DWORD),
                ("show", W.WORD), ("reserved_size", W.WORD),
                ("reserved_bytes", ctypes.c_void_p), ("stdin", W.HANDLE),
                ("stdout", W.HANDLE), ("stderr", W.HANDLE)]


class StartupEx(ctypes.Structure):
    """STARTUPINFOEXW retains the attribute list through process creation."""
    _fields_ = [("startup", Startup), ("attributes", ctypes.c_void_p)]


class ProcessInfo(ctypes.Structure):
    """PROCESS_INFORMATION retains both returned native handle references."""
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE),
                ("pid", W.DWORD), ("tid", W.DWORD)]


class BasicLimits(ctypes.Structure):
    """JOBOBJECT_BASIC_LIMIT_INFORMATION preserves pointer-width fields."""
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", W.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                ("active", W.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", W.DWORD), ("scheduling", W.DWORD)]


class ExtendedLimits(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION includes the job commit ceiling."""
    _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


# Every pointer-valued result must remain wide enough for a native handle.
SIGNATURES = {
    "CloseHandle": (W.BOOL, [W.HANDLE]),
    "GetCurrentProcess": (W.HANDLE, []),
    "GetProcessTimes": (W.BOOL, [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4),
    "OpenProcess": (W.HANDLE, [W.DWORD, W.BOOL, W.DWORD]),
    "OpenThread": (W.HANDLE, [W.DWORD, W.BOOL, W.DWORD]),
    "CancelSynchronousIo": (W.BOOL, [W.HANDLE]),
    "OpenJobObjectW": (W.HANDLE, [W.DWORD, W.BOOL, W.LPCWSTR]),
    "CreateJobObjectW": (W.HANDLE, [ctypes.c_void_p, W.LPCWSTR]),
    "AssignProcessToJobObject": (W.BOOL, [W.HANDLE, W.HANDLE]),
    "TerminateJobObject": (W.BOOL, [W.HANDLE, W.UINT]),
    "TerminateProcess": (W.BOOL, [W.HANDLE, W.UINT]),
    "QueryInformationJobObject": (W.BOOL, [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]),
    "SetInformationJobObject": (W.BOOL, [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]),
    "IsProcessInJob": (W.BOOL, [W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)]),
    "GetHandleInformation": (W.BOOL, [W.HANDLE, ctypes.POINTER(W.DWORD)]),
    "SetHandleInformation": (W.BOOL, [W.HANDLE, W.DWORD, W.DWORD]),
    "CreatePipe": (W.BOOL, [ctypes.POINTER(W.HANDLE), ctypes.POINTER(W.HANDLE), ctypes.POINTER(Security), W.DWORD]),
    "DuplicateHandle": (W.BOOL, [W.HANDLE, W.HANDLE, W.HANDLE, ctypes.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD]),
    "InitializeProcThreadAttributeList": (W.BOOL, [ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.c_size_t)]),
    "UpdateProcThreadAttribute": (W.BOOL, [ctypes.c_void_p, W.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]),
    "DeleteProcThreadAttributeList": (None, [ctypes.c_void_p]),
    "CreateProcessW": (W.BOOL, [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD, ctypes.c_void_p, W.LPCWSTR, ctypes.POINTER(Startup), ctypes.POINTER(ProcessInfo)]),
    "WaitForSingleObject": (W.DWORD, [W.HANDLE, W.DWORD]),
    "GetExitCodeProcess": (W.BOOL, [W.HANDLE, ctypes.POINTER(W.DWORD)]),
}


class Kernel:
    """Bind only reviewed operations and capture last error immediately."""
    def __init__(self) -> None:
        """Load kernel32 lazily so legacy non-Windows imports remain valid."""
        self.library = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, (result, arguments) in SIGNATURES.items():
            function = getattr(self.library, name)
            function.restype, function.argtypes = result, arguments

    def raw(self, name: str, *arguments):
        """Return the operation value and thread-local immediate error code."""
        ctypes.set_last_error(0)
        value = getattr(self.library, name)(*arguments)
        code = ctypes.get_last_error()
        return value, code

    def checked(self, name: str, *arguments):
        """Raise an explicit native error for a false BOOL or handle result."""
        value, code = self.raw(name, *arguments)
        if not value:
            raise NativeError(name, code)
        return value

    def acquire(self, reference, name: str, *arguments):
        """Store returned handle custody before error formatting or bookkeeping."""
        ctypes.set_last_error(0)
        reference.handle = getattr(self.library, name)(*arguments)
        code = ctypes.get_last_error()
        reference.last_error = code
        if reference.handle:
            reference.state = "owned"
        else:
            raise NativeError(name, code)
        return reference.handle

    def close(self, handle) -> None:
        """Check release of this acquired reference rather than assume success."""
        self.checked("CloseHandle", handle)
