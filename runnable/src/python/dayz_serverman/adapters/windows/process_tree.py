"""Owned Windows child-process identity and bounded tree control."""

from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any


# Variables SteamCMD and Windows need to start; everything else is withheld from the child
SAFE_ENVIRONMENT_NAMES = frozenset({
    "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
    "COMSPEC", "PATH", "PATHEXT", "PROGRAMDATA",
})


class ProcessIdentityUnavailable(OSError):
    """Raised when a process identity cannot be read from the system."""

    def __init__(self, error_code: int) -> None:
        """Record the Windows error code that hid the process identity."""
        self.error_code = error_code
        super().__init__("SteamCMD process identity is unavailable")


def safe_child_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of the environment limited to allowlisted variable names."""
    # Fall back to the current process environment when no override is given
    values = source if source is not None else dict(os.environ)
    # Keep only entries whose names SteamCMD expects to find
    return {
        name: value
        for name, value in values.items()
        if name.upper() in SAFE_ENVIRONMENT_NAMES
    }


@dataclass(frozen=True)
class ProcessIdentity:
    """Identity pair used to detect process-id reuse across observations."""
    process_id: int
    creation_identity: str

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-compatible identity mapping."""
        return {"process_id": self.process_id, "creation_identity": self.creation_identity}


@dataclass(frozen=True)
class ChildEvidence:
    """Evidence of an owned process tree: root identity, members, and job name."""
    process_id: int
    creation_identity: str
    members: tuple[ProcessIdentity, ...] = ()
    job_name: str | None = None

    @property
    def normalized_members(self) -> tuple[ProcessIdentity, ...]:
        """Return members with the recorded root guaranteed to be present."""
        # Treat the recorded root as a member even when membership was empty
        root = ProcessIdentity(self.process_id, self.creation_identity)
        values = self.members or (root,)
        # Keep the root first so member ordering stays deterministic
        return values if root in values else (root, *values)

    def to_dict(self) -> dict[str, object]:
        """Return the versioned JSON-compatible evidence mapping."""
        # Schema version 1 pins the evidence shape consumed by recovery records
        return {
            "schema_version": 1,
            "process_id": self.process_id,
            "creation_identity": self.creation_identity,
            "job_name": self.job_name,
            "members": [value.to_dict() for value in self.normalized_members],
        }


class OwnedProcessTree:
    """Control only the process that this object created and its assigned job."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        """Adopt the freshly started process and assign it to a private job."""
        self.process = process
        self._job: Any = None
        self._job_name: str | None = None
        # Capture the creation identity immediately, before the process can change
        self._creation_identity = self._read_creation_identity(process)
        # Job objects exist only on Windows; elsewhere only the raw process is tracked
        if os.name == "nt":
            self._job_name = f"Local\\DayZServerMan-SteamCMD-{uuid.uuid4().hex}"
            self._job = self._assign_job(process, self._job_name)

    @property
    def evidence(self) -> ChildEvidence:
        """Return the current root, member, and job evidence snapshot."""
        root = ProcessIdentity(self.process.pid, self._creation_identity)
        # Without a job the root process is the whole tree
        if self._job is None:
            return ChildEvidence(root.process_id, root.creation_identity, (root,))
        # Record every live job member together with its creation identity
        members: list[ProcessIdentity] = []
        for process_id in self._job_pids(self._job):
            identity = self._identity_for_pid(process_id)
            members.append(ProcessIdentity(process_id, identity))
        # Sort members so evidence comparisons stay stable
        members.sort(key=lambda value: value.process_id)
        return ChildEvidence(root.process_id, root.creation_identity, tuple(members), self._job_name)

    def request_close(self) -> None:
        """Ask the child to close, falling back to termination when unsupported."""
        # Prefer the polite console signal so SteamCMD can exit cleanly
        try:
            self.process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        except (AttributeError, OSError, ValueError):
            self.process.terminate()

    def terminate_tree(self) -> None:
        """Terminate every process assigned to the owned job or the raw child."""
        # Terminating the job covers the whole tree in one kernel call
        if self._job is not None:
            import ctypes
            ctypes.windll.kernel32.TerminateJobObject(self._job, 1)
        else:
            self.process.terminate()

    def kill_tree(self) -> None:
        """Force-kill the tree after termination did not finish it."""
        self.terminate_tree() if self._job is not None else self.process.kill()

    def wait_absent(self, seconds: float) -> bool:
        """Wait up to the given seconds for the whole tree to become absent."""
        # Poll membership until the deadline passes
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._all_absent():
                return True
            time.sleep(0.05)
        # One final check settles processes that exited at the deadline edge
        return self._all_absent()

    def _all_absent(self) -> bool:
        """Report whether every process in the tree is gone."""
        # An empty job membership means every assigned process exited
        if self._job is not None:
            try:
                return not self._job_pids(self._job)
            except OSError:
                return False
        return self.process.poll() is not None

    @staticmethod
    def _read_creation_identity(process: subprocess.Popen[str]) -> str:
        """Return the creation identity read from the live process handle."""
        # Outside Windows no creation time exists; a sentinel keeps the shape stable
        if os.name != "nt":
            return f"pid:{process.pid}:owned"
        return OwnedProcessTree._identity_from_handle(process._handle)

    @staticmethod
    def _identity_for_pid(process_id: int) -> str:
        """Read the creation identity of an arbitrary process id."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        # 0x1000 is PROCESS_QUERY_LIMITED_INFORMATION, enough to read process timings
        process = kernel32.OpenProcess(0x1000, False, process_id)
        if not process:
            raise ProcessIdentityUnavailable(kernel32.GetLastError())
        try:
            return OwnedProcessTree._identity_from_handle(process)
        finally:
            # Always release the borrowed handle
            kernel32.CloseHandle(process)

    @staticmethod
    def _identity_from_handle(handle: Any) -> str:
        """Convert a process handle's creation time into a stable identity string."""
        import ctypes
        from ctypes import wintypes
        # Read all four FILETIME values; only creation time is used
        values = [wintypes.FILETIME() for _ in range(4)]
        kernel32 = ctypes.windll.kernel32
        kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (
            ctypes.POINTER(wintypes.FILETIME),) * 4
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        if not kernel32.GetProcessTimes(wintypes.HANDLE(handle), *values):
            raise OSError("SteamCMD child creation identity is unavailable")
        creation = values[0]
        # FILETIME stores its tick count split across two 32-bit halves
        return f"windows-filetime:{(creation.dwHighDateTime << 32) | creation.dwLowDateTime}"

    @staticmethod
    def _assign_job(process: subprocess.Popen[str], name: str) -> Any:
        """Create the named job object and assign the child process to it."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        # One job per child keeps ownership provable and termination contained
        job = kernel32.CreateJobObjectW(None, name)
        if not job or not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
            # A failed assignment closes the job so no handle leaks
            if job:
                kernel32.CloseHandle(job)
            raise OSError("SteamCMD process could not be assigned to its job")
        return job

    @staticmethod
    def _job_pids(job: Any) -> tuple[int, ...]:
        """Return the process ids currently assigned to the job."""
        import ctypes
        from ctypes import wintypes
        # A fixed 256-entry buffer bounds the evidence read; larger jobs are refused below
        capacity = 256
        buffer = ctypes.create_string_buffer(8 + ctypes.sizeof(ctypes.c_size_t) * capacity)
        kernel32 = ctypes.windll.kernel32
        kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        returned = wintypes.DWORD()
        # Information class 3 queries the job's active process list
        if not kernel32.QueryInformationJobObject(job, 3, buffer, len(buffer), returned):
            raise OSError("SteamCMD job membership could not be inspected")
        active = int.from_bytes(buffer.raw[4:8], "little")
        # More members than the buffer holds would misreport membership
        if active > capacity:
            raise OSError("SteamCMD job membership exceeds the evidence bound")
        width = ctypes.sizeof(ctypes.c_size_t)
        # Decode the returned array of process ids
        return tuple(int.from_bytes(buffer.raw[8 + index * width:8 + (index + 1) * width],
                                    "little") for index in range(active))


class WindowsChildProbe:
    """Prove that a recorded owned job and every recorded member are absent."""

    def is_absent(self, evidence: ChildEvidence) -> bool:
        """Report whether the recorded job and every recorded member are gone."""
        # A live job name is proof the owned tree still exists
        if evidence.job_name is not None and not self._job_absent(evidence.job_name):
            return False
        # Each member must be gone or its process id reused by something unrelated
        return all(self._identity_absent(member) for member in evidence.normalized_members)

    def _job_absent(self, job_name: str) -> bool:
        """Report whether a job name no longer exists or holds no members."""
        # Job objects are Windows-only; other hosts report absent
        if os.name != "nt":
            return True
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.OpenJobObjectW.restype = wintypes.HANDLE
        # 0x0004 is JOB_OBJECT_QUERY, the right needed to read job membership
        job = kernel32.OpenJobObjectW(0x0004, False, job_name)
        if not job:
            # Error 2 (file not found) means the named job no longer exists
            return kernel32.GetLastError() == 2
        try:
            return not OwnedProcessTree._job_pids(job)
        except OSError:
            return False
        finally:
            kernel32.CloseHandle(job)

    def _identity_absent(self, identity: ProcessIdentity) -> bool:
        """Report whether a recorded process identity no longer matches."""
        # Without Windows only the process id is tracked
        if os.name != "nt":
            return True
        try:
            current = OwnedProcessTree._identity_for_pid(identity.process_id)
        except ProcessIdentityUnavailable as error:
            # Error 87 (invalid parameter) means no such process exists
            return error.error_code == 87
        except OSError:
            return False
        # A reused process id with a different creation time counts as absent
        return current != identity.creation_identity
