"""Measurement page, js_api and one timed transfer through real pywebview evaluate_js (plan leaf 3.14).

Everything runs in the measuring process: no child process, no job object. The only native call is
GetProcessMemoryInfo, which reads the working set of this process.
"""

from __future__ import annotations

import ctypes
import re
import threading
import time
from ctypes import wintypes
from pathlib import Path

from dayz_serverman.adapters.windows.shared_files import read_text_shared
from dayz_serverman.host.assets import source_frontend_root


# Page template beside this script; its marker receives the editor content security policy
PAGE_TEMPLATE = Path(__file__).resolve().with_name("bridge_transfer_page.html")
CSP_MARKER = "<!-- EDITOR_CSP -->"
# The policy meta element of the editor document, copied unchanged
CSP_PATTERN = re.compile(r'<meta http-equiv="Content-Security-Policy" content="[^"]*">')
# Interval of the working-set sampler, in seconds
SAMPLE_SECONDS = 0.01


def compose_page() -> str:
    """Return the measurement page with the content security policy of frontend/editor.html."""
    editor = read_text_shared(source_frontend_root() / "editor.html", "utf-8")
    policy = CSP_PATTERN.search(editor)
    if policy is None:
        raise RuntimeError("frontend/editor.html has no Content-Security-Policy meta element")
    return read_text_shared(PAGE_TEMPLATE, "utf-8").replace(CSP_MARKER, policy.group(0))


class _MemoryCounters(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS of psapi.h."""

    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]


def working_set() -> tuple[int, int]:
    """Return the current and the peak working set of this process, in bytes."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD)
    kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.WorkingSetSize, counters.PeakWorkingSetSize


class MemorySampler:
    """Record the highest working set of this process while one transfer runs."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.peak = working_set()[0]
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        """Poll the working set until the transfer ends."""
        while not self._stop.wait(SAMPLE_SECONDS):
            self.peak = max(self.peak, working_set()[0])

    def __enter__(self) -> MemorySampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, working_set()[0])


class MeasureApi:
    """js_api of the measurement page; pywebview exposes only the public methods ready and done."""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._user_agent = ""
        self._acks: dict[str, tuple[float, dict]] = {}
        self._arrived = threading.Condition()

    def ready(self, user_agent: str) -> None:
        """Record that the page runs and which WebView2 user agent it has."""
        self._user_agent = str(user_agent)
        self._ready.set()

    def done(self, report: dict) -> None:
        """Record the arrival time and the report of one decoded (or failed) payload."""
        self._post(str(report.get("id")), dict(report))

    def _post(self, ident: str, report: dict) -> None:
        """Store an acknowledgement and wake the waiting measurement."""
        with self._arrived:
            self._acks[ident] = (time.perf_counter(), report)
            self._arrived.notify_all()

    def _wait(self, ident: str, deadline: float) -> tuple[float, dict] | None:
        """Return the acknowledgement of one payload, or None when the deadline passes first."""
        with self._arrived:
            while ident not in self._acks:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                self._arrived.wait(remaining)
            return self._acks.pop(ident)


def send_payload(window: object, api: MeasureApi, ident: str, parts: list[str], keep: bool,
                 deadline: float) -> dict:
    """Send one base64 text in one call or in parts, and wait for the page acknowledgement.

    The evaluate_js calls run in a helper thread, so a call that never returns still ends at the deadline.
    Returns call_s (slowest evaluate_js call that carries text), finish_s (the call without text that joins
    the parts and starts the decode; 0 for one call), ack_s (first call to acknowledgement) and the report.
    """
    calls: list[float] = []
    started = time.perf_counter()

    # Run the bridge calls; a JavaScript or bridge error becomes a failed acknowledgement
    def deliver() -> None:
        try:
            scripts = [f"measureReceive('{ident}', '{parts[0]}', {str(keep).lower()})"] if len(parts) == 1 else (
                [f"measurePart('{part}')" for part in parts] + [f"measureFinish('{ident}', {str(keep).lower()})"])
            for script in scripts:
                begun = time.perf_counter()
                window.evaluate_js(script)
                calls.append(time.perf_counter() - begun)
        except Exception as error:  # every failure of the bridge call is a measured outcome
            api._post(ident, {"id": ident, "ok": False, "error": f"{type(error).__name__}: {error}"})

    sender = threading.Thread(target=deliver, daemon=True)
    sender.start()
    ack = api._wait(ident, deadline)
    if ack is None:
        return {"ok": False, "timeout": True, "error": "no acknowledgement before the timeout"}
    # Let the last evaluate_js return, because the page can acknowledge before it does
    sender.join(max(0.0, deadline - time.perf_counter()))
    arrived, report = ack
    complete = report.get("chars") == sum(map(len, parts))
    error = report.get("error") or (None if complete else "the page received a different text length")
    # The last call of a chunked transfer is the finish call; it carries no text
    texts, finish = (calls[:-1], calls[-1]) if len(parts) > 1 and len(calls) == len(parts) + 1 else (calls, 0.0)
    return {"ok": bool(report.get("ok")) and complete, "timeout": False,
            "call_s": max(texts, default=arrived - started), "finish_s": finish, "ack_s": arrived - started,
            "report": report, "error": error}
