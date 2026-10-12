"""Own one exact private job through preparation, admission and release."""
from __future__ import annotations

import ctypes
import time
import uuid
from ctypes import wintypes as W
from .process_bindings import Kernel, NativeError, ExtendedLimits
from .process_custody import Custody

# Preserve the finite query bound and accepted strict-probe commit ceiling.
MEMBER_CAPACITY = 256
STRICT_FLAGS = 0x2000 | 0x0200
STRICT_COMMIT = 4 * 1024 ** 3


def members(handle, kernel: Kernel | None = None) -> tuple[int, ...]:
    """Read complete bounded job membership; never truncate an oversized job."""
    kernel = kernel or Kernel()
    width = ctypes.sizeof(ctypes.c_size_t)
    # Refuse overflow rather than report an incomplete membership list.
    buffer = ctypes.create_string_buffer(8 + width * MEMBER_CAPACITY)
    returned = W.DWORD()
    kernel.checked("QueryInformationJobObject", handle, 3, buffer, len(buffer), ctypes.byref(returned))
    active = int.from_bytes(buffer.raw[4:8], "little")
    if active > MEMBER_CAPACITY:
        raise OSError("SteamCMD job membership exceeds the evidence bound")
    return tuple(int.from_bytes(buffer.raw[8 + index * width:8 + (index + 1) * width], "little")
                 for index in range(active))


class PrivateJob:
    """Expose custody before any fallible native job operation."""
    def __init__(self, custody: Custody, kernel: Kernel | None = None) -> None:
        """Reserve one noninheritable job reference before acquiring it."""
        self.custody, self.kernel = custody, kernel or Kernel()
        self.reference = custody.reserve("private-job", "new", self.kernel.close)
        self.name = "Local\\DayZServerMan-SteamCMD-" + uuid.uuid4().hex
        self.new_identity, self.prepared, self.admitted = False, False, False

    def create(self, name: str | None = None) -> None:
        """Acquire a new private identity; collisions grant no job authority."""
        if name is not None:
            self.name = name
        # The returned reference is held before name-collision or inheritance checks.
        self.kernel.acquire(self.reference, "CreateJobObjectW", None, self.name)
        if self.reference.last_error == 183:
            raise NativeError("CreateJobObjectW identity collision", 183)
        self.new_identity = True
        # Job authority stays in the parent and never enters the child handle list.
        flags = W.DWORD()
        self.kernel.checked("GetHandleInformation", self.reference.value(), ctypes.byref(flags))
        if flags.value & 1:
            raise OSError("private job handle is inheritable")

    def configure(self) -> None:
        """Check kill-on-close and the unchanged commit ceiling before launch."""
        if not self.new_identity:
            raise OSError("new private job identity unproved")
        value = ExtendedLimits()
        value.basic.flags, value.job_memory = STRICT_FLAGS, STRICT_COMMIT
        handle = self.reference.value()
        # Configure and independently read back limits before process creation.
        self.kernel.checked("SetInformationJobObject", handle, 9, ctypes.byref(value), ctypes.sizeof(value))
        observed, returned = ExtendedLimits(), W.DWORD()
        self.kernel.checked("QueryInformationJobObject", handle, 9, ctypes.byref(observed),
                            ctypes.sizeof(observed), ctypes.byref(returned))
        if observed.basic.flags != STRICT_FLAGS or observed.job_memory != STRICT_COMMIT:
            raise OSError("private job limit proof differs")
        self.prepared = True

    def configure_legacy(self) -> None:
        """Preserve adoption's kill-on-close without imposing strict-probe limits."""
        if not self.new_identity:
            raise OSError("new private job identity unproved")
        value = ExtendedLimits()
        value.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE in the legacy contract.
        self.kernel.checked("SetInformationJobObject", self.reference.value(), 9,
                            ctypes.byref(value), ctypes.sizeof(value))

    def assign(self, process_handle) -> None:
        """Retain legacy adoption authority separately from strict creation."""
        self.kernel.checked("AssignProcessToJobObject", self.reference.value(), process_handle)
        self.require_member(process_handle)
        self.admitted = True

    def require_member(self, process_handle) -> None:
        """Check exact membership without assigning a late runtime candidate."""
        inside = W.BOOL()
        self.kernel.checked("IsProcessInJob", process_handle, self.reference.value(), ctypes.byref(inside))
        if not inside.value:
            raise OSError("process is not a member of the owned private job")

    def pids(self) -> tuple[int, ...]:
        """Query only the exact held job with complete conservative evidence."""
        return members(self.reference.value(), self.kernel)

    def terminate(self) -> None:
        """Check containment request; success still requires separate absence."""
        if not self.new_identity:
            raise OSError("owned private job identity unproved")
        self.kernel.checked("TerminateJobObject", self.reference.value(), 1)

    def wait_absent(self, seconds: float) -> bool:
        """Use the existing finite polling bound and refuse unavailable proof."""
        deadline = time.monotonic() + seconds
        # Native query refusal leaves absence unproved within the original deadline.
        while True:
            try:
                if not self.pids():
                    return True
            except OSError as error:
                self.custody.secondary.append(("job-absence-query", error))
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def close(self) -> None:
        """Release only after actual job absence, or an unowned collision."""
        if self.reference.state == "reserved":
            return
        if self.new_identity and self.pids():
            raise OSError("private job absence unproved; handle retained")
        self.reference.close()
