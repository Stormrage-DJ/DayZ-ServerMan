"""Narrow child interface over exact native process and stream custody."""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes as W
from .process_bindings import NativeError
from .process_custody import Custody


class OwnedStream:
    """Expose the pipe interface while retaining its exact release evidence."""
    def __init__(self, reference) -> None:
        """Bind an already reserved stream ownership slot."""
        self.reference = reference

    def fileno(self) -> int:
        """Use only the still-owned exact stream descriptor."""
        return self.reference.value().fileno()

    def read(self, size: int = -1):
        """Read the retained child stdout without creating another stream."""
        return self.reference.value().read(size)

    def close(self) -> None:
        """Record release through the same shared custody reference."""
        self.reference.close()


class NativeChild:
    """Retain the returned process/thread handles without Popen internals."""
    def __init__(self, custody: Custody, kernel, information) -> None:
        """Reserve all ownership slots before CreateProcess can fill them."""
        self.custody, self.kernel, self.information = custody, kernel, information
        self.process_reference = custody.reserve("process", "atomic-launch", kernel.close)
        self.thread_reference = custody.reserve("initial-thread", "atomic-launch", kernel.close)
        self.stream_reference = custody.reserve("stdout-stream", "atomic-launch", lambda stream: stream.close())
        self.auxiliary = [self.thread_reference]
        self.stdout = None
        self.returncode = None
        self.launch_monotonic = None

    @property
    def pid(self) -> int:
        """Return only the id supplied by the exact CreateProcess result."""
        return self.information.pid

    def capture(self) -> None:
        """Retain every returned native reference even after failed creation."""
        # Both out parameters can be valid even when later creation bookkeeping failed.
        if self.information.process and self.process_reference.state == "reserved":
            self.process_reference.handle = self.information.process
            self.process_reference.state = "owned"
        if self.information.thread and self.thread_reference.state == "reserved":
            self.thread_reference.handle = self.information.thread
            self.thread_reference.state = "owned"

    def attach_stdout(self, pipe_reference) -> None:
        """Transfer the exact pipe handle through a retained CRT descriptor."""
        import msvcrt
        descriptor = self.custody.reserve("stdout-descriptor", "atomic-launch", os.close)
        self.auxiliary.append(descriptor)
        # Both owners exist before the native-to-CRT transfer can occur.
        descriptor.handle = msvcrt.open_osfhandle(pipe_reference.value(), os.O_RDONLY | os.O_BINARY)
        descriptor.state = "owned"
        pipe_reference.transfer()
        # Stream custody replaces descriptor custody only after successful construction.
        self.stream_reference.handle = os.fdopen(descriptor.value(), "rb", buffering=0)
        self.stream_reference.state = "owned"
        descriptor.transfer()
        self.stdout = OwnedStream(self.stream_reference)

    def poll(self) -> int | None:
        """Prove held-process completion before reading its exit code."""
        if self.returncode is not None:
            return self.returncode
        # A signalled held process proves completion independently of its exit-code value.
        result, code = self.kernel.raw("WaitForSingleObject", self.process_reference.value(), 0)
        if result == 258:
            return None
        if result != 0:
            raise NativeError("WaitForSingleObject", code)
        exit_code = W.DWORD()
        # Read the terminal code only after the process handle signalled.
        self.kernel.checked("GetExitCodeProcess", self.process_reference.value(), ctypes.byref(exit_code))
        self.returncode = exit_code.value
        return self.returncode

    def wait(self, timeout: float) -> int:
        """Wait finitely on the same held process; never substitute a PID query."""
        if self.returncode is None:
            # Bound the exact held wait without looking up or assigning a PID.
            result, code = self.kernel.raw("WaitForSingleObject", self.process_reference.value(), int(timeout * 1000))
            if result == 258:
                raise subprocess.TimeoutExpired("exact native child", timeout)
            if result != 0:
                raise NativeError("WaitForSingleObject", code)
        return self.poll()

    def terminate(self) -> None:
        """Contain only the exact returned process handle."""
        self.kernel.checked("TerminateProcess", self.process_reference.value(), 1)

    def kill(self) -> None:
        """Preserve the narrow force-stop interface with checked containment."""
        self.terminate()

    def close_handle(self) -> None:
        """Release process authority only after its actual absence is proved."""
        if self.poll() is None:
            raise OSError("held process absence unproved")
        self.process_reference.close()

    def release_auxiliary(self) -> None:
        """Attempt every independent acquired thread/pipe/attribute release."""
        # Caller-owned references and ambiguous prior closes remain outside this release.
        for reference in self.auxiliary:
            if reference.state == "owned":
                self.custody.release(reference)
