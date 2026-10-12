"""Create the fixed launcher atomically inside its prepared limited job."""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes as W
from .process_bindings import Security, Startup, StartupEx, ProcessInfo, NativeError
from .process_child import NativeChild

# Microsoft job-list and explicit-handle-list attributes; no breakaway flags.
JOB_LIST = 0x2000D
HANDLE_LIST = 0x20002
CREATION_FLAGS = 0x80000 | 0x400 | 0x08000000


def environment_block(environment: dict[str, str]) -> str:
    """Preserve Unicode values and deterministic Windows key ordering."""
    for key, value in environment.items():
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise ValueError("invalid child environment")
    return "\0".join(key + "=" + value for key, value in sorted(environment.items(), key=lambda item: item[0].upper())) + "\0\0"


def duplicate(kernel, custody, handle, label, auxiliary):
    """Acquire one inheritable standard-handle reference into reserved custody."""
    reference = custody.reserve("standard-handle", label, kernel.close)
    auxiliary.append(reference)
    output = W.HANDLE()
    custody.anchors["duplicate-output-" + label] = output
    current = kernel.checked("GetCurrentProcess")
    try:
        kernel.checked("DuplicateHandle", current, handle, current, ctypes.byref(output), 0, True, 2)
    finally:
        # Out parameters remain available even if post-acquisition bookkeeping fails.
        if output.value:
            reference.handle, reference.state = output.value, "owned"
    return reference


def create(custody, job, command: list[str], cwd, environment: dict[str, str], stderr):
    """Retain all partial native outputs; unsupported atomic admission fails closed."""
    import msvcrt
    kernel = job.kernel
    if not job.prepared or not job.new_identity:
        raise OSError("prepared private job admission unavailable")
    if not command or not os.path.isabs(command[0]):
        raise ValueError("explicit absolute child executable required")
    information = ProcessInfo()
    child = NativeChild(custody, kernel, information)
    custody.anchors["native-child"] = child
    custody.anchors["process-information"] = information
    read_ref = custody.reserve("stdout-pipe", "parent-read", kernel.close)
    write_ref = custody.reserve("standard-handle", "child-write", kernel.close)
    null_ref = custody.reserve("stdin-descriptor", "null-input", os.close)
    attribute_ref = custody.reserve("attribute-list", "atomic-launch", lambda value: kernel.raw("DeleteProcThreadAttributeList", value))
    child.auxiliary.extend((write_ref, null_ref, attribute_ref))
    read_out, write_out = W.HANDLE(), W.HANDLE()
    custody.anchors["pipe-outputs"] = (read_out, write_out)
    try:
        # Acquire an explicit stdout pipe; only its child end is inherited.
        security = Security(ctypes.sizeof(Security), None, True)
        try:
            kernel.checked("CreatePipe", ctypes.byref(read_out), ctypes.byref(write_out), ctypes.byref(security), 0)
        finally:
            if read_out.value:
                read_ref.handle, read_ref.state = read_out.value, "owned"
            if write_out.value:
                write_ref.handle, write_ref.state = write_out.value, "owned"
        kernel.checked("SetHandleInformation", read_ref.value(), 1, 0)
        null_ref.acquire(lambda: os.open(os.devnull, os.O_RDONLY))
        stdin_ref = duplicate(kernel, custody, msvcrt.get_osfhandle(null_ref.value()), "stdin", child.auxiliary)
        stderr_ref = duplicate(kernel, custody, msvcrt.get_osfhandle(stderr.fileno()), "stderr", child.auxiliary)
        # Keep attribute values alive until the initialized list is destroyed.
        handles = (W.HANDLE * 3)(stdin_ref.value(), write_ref.value(), stderr_ref.value())
        jobs = (W.HANDLE * 1)(job.reference.value())
        size = ctypes.c_size_t()
        value, code = kernel.raw("InitializeProcThreadAttributeList", None, 2, 0, ctypes.byref(size))
        if value or code != 122 or not size.value:
            raise NativeError("InitializeProcThreadAttributeList size", code)
        attributes = ctypes.create_string_buffer(size.value)
        custody.anchors["attribute-values"] = (attributes, handles, jobs)
        attribute_ref.handle, attribute_ref.state = attributes, "initialization-unknown"
        kernel.checked("InitializeProcThreadAttributeList", attributes, 2, 0, ctypes.byref(size))
        attribute_ref.state = "owned"
        kernel.checked("UpdateProcThreadAttribute", attributes, 0, JOB_LIST, jobs, ctypes.sizeof(jobs), None, None)
        kernel.checked("UpdateProcThreadAttribute", attributes, 0, HANDLE_LIST, handles, ctypes.sizeof(handles), None, None)
        startup = StartupEx()
        startup.startup.cb, startup.startup.flags = ctypes.sizeof(StartupEx), 0x100
        startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
        startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
        arguments = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        environment_bytes = ctypes.create_unicode_buffer(environment_block(environment))
        custody.anchors["creation-inputs"] = (startup, arguments, environment_bytes)
        # The deadline originates at the actual creation call, never gate release.
        child.launch_monotonic = time.monotonic()
        try:
            kernel.checked("CreateProcessW", command[0], arguments, None, None, True,
                           CREATION_FLAGS, environment_bytes, str(cwd),
                           ctypes.cast(ctypes.byref(startup), ctypes.POINTER(Startup)), ctypes.byref(information))
        finally:
            child.capture()
        job.require_member(child.process_reference.value())
        job.admitted = True
        child.attach_stdout(read_ref)
    except BaseException as error:
        custody.fault(error)
    finally:
        child.release_auxiliary()
    custody.raise_failure()
    return child
