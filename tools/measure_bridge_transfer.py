"""Measure bridge transfer budgets through real pywebview evaluate_js calls (plan leaf 3.14).

The script opens one pywebview window with the editor content security policy, sends synthetic PNG tier
masks and RGB images as base64 text, and measures payload size, encoding time, transfer and decode time
until the page acknowledges, and the working set of this process. It writes results.json and results.md
to the output folder after each case and proposes budgets with the rule in bridge_transfer_payloads.py.

Usage (repository root, project interpreter, in the foreground):
    runnable\\.venv\\Scripts\\python.exe tools\\measure_bridge_transfer.py [--out FOLDER] [--cases A,B]
Close the window to stop the run; the results so far stay in the output folder.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

# Make the tools folder and the application package importable when this file runs as a script
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "runnable" / "src" / "python")]

from dayz_serverman.adapters.windows.shared_files import write_bytes_atomically  # noqa: E402
from tools.bridge_transfer_payloads import (  # noqa: E402
    CALL_SECONDS, CASES, CHUNK_SIZES, IMAGE_SECONDS, IMAGE_SIDE_LIMIT, NOT_ATTEMPTED, RUNS, ZLIB_LEVEL, Case,
    chunks, encode_case, passed, propose_budgets, worst)
from tools.bridge_transfer_window import MeasureApi, MemorySampler, compose_page, send_payload  # noqa: E402
from tools.generate_ordinal_upcase import windows_build  # noqa: E402


# Longest wait for one run of a case (all its calls and decodes), in seconds (A180)
TIMEOUT_SECONDS = 60.0
# Registry key of the installed WebView2 Runtime (Evergreen, per-machine)
WEBVIEW2_KEY = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
MIB = 1 << 20


def webview2_version() -> str | None:
    """Return the installed WebView2 Runtime version, or None when the registry does not name one."""
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, WEBVIEW2_KEY) as key:
                return str(winreg.QueryValueEx(key, "pv")[0])
        except OSError:
            continue
    return None


class Measurement:
    """One measurement run: the case rows, the chunk comparison rows and the result files."""

    def __init__(self, out: Path, cases: tuple[Case, ...]) -> None:
        self.out = out
        self.cases = cases
        self.environment: dict = {}
        self.rows: list[dict] = []
        self.chunk_rows: list[dict] = []
        self.stopped: str | None = None

    def run(self, window: object, api: MeasureApi) -> None:
        """Measure every case, then the chunk comparison, and close the window (runs after webview.start)."""
        try:
            if not api._ready.wait(TIMEOUT_SECONDS):
                self.stopped = "the measurement page did not start"
                return
            self.environment = {
                "windows_build": windows_build(), "webview2_runtime": webview2_version(),
                "user_agent": api._user_agent, "pywebview": metadata.version("pywebview"),
                "python": platform.python_version(), "processor": platform.processor(),
                "started": time.strftime("%Y-%m-%d %H:%M:%S %z")}
            # Measure the single-call cases in order; a timeout skips the larger cases
            for case in self.cases:
                self.rows.append(self.measure(window, api, case, None) if not self.stopped
                                 else {"case": case.name, "status": f"skipped: {self.stopped}"})
                self.write()
            # Compare the chunk sizes on every RGB image that passed in one call and needs several chunks
            targets = [row["case"] for row in self.rows if row.get("kind") == "rgb" and passed(row)
                       and row["text_chars"] > max(CHUNK_SIZES)]
            for case in (case for case in self.cases if case.name in targets):
                for chunk in CHUNK_SIZES if not self.stopped else ():
                    self.chunk_rows.append(self.measure(window, api, case, chunk))
                    self.write()
        finally:
            self.write()
            # Close the window unless the operator closed it already
            if not window.events.closed.is_set():
                window.destroy()

    def measure(self, window: object, api: MeasureApi, case: Case, chunk: int | None) -> dict:
        """Encode one case and return its row with RUNS timed repeats."""
        pngs, texts, png_s, b64_s = encode_case(case)
        # Calls: the text calls of one run (one per tier mask, or one per chunk)
        calls = sum(len(chunks(text, chunk)) for text in texts) if chunk else case.calls
        row = {"case": case.name, "kind": case.kind, "side": case.side, "calls": calls,
               "mode": f"chunks of {chunk // MIB} MiB" if chunk else "one call", "chunk_chars": chunk,
               "call_chars": min(chunk, max(map(len, texts))) if chunk else max(map(len, texts)),
               "text_chars": sum(map(len, texts)), "png_bytes": sum(map(len, pngs)),
               "png_s": round(png_s, 3), "base64_s": round(b64_s, 3), "runs": [], "status": "ok"}
        del pngs
        for index in range(RUNS):
            if window.events.closed.is_set():
                self.stopped = "the window was closed"
            if self.stopped:
                row["status"] = f"stopped: {self.stopped}"
                break
            run = self.repeat(window, api, f"{case.name}-{chunk or 0}-{index}", texts, chunk)
            row["runs"].append(run)
            if run["timeout"]:
                row["status"], self.stopped = "timeout", f"timeout in {case.name}"
                break
            if not run["ok"]:
                row["status"] = "failed"
        del texts
        gc.collect()
        return row

    def repeat(self, window: object, api: MeasureApi, ident: str, texts: list[str], chunk: int | None) -> dict:
        """Send the payloads of one repeat and return its timings, memory and outcome."""
        window.evaluate_js("measureReset()")
        started = time.perf_counter()
        deadline = started + TIMEOUT_SECONDS
        results = []
        with MemorySampler() as memory:
            for index, text in enumerate(texts):
                parts = chunks(text, chunk) if chunk else [text]
                results.append(send_payload(window, api, f"{ident}-{index}", parts, len(texts) > 1, deadline))
                if not results[-1]["ok"]:
                    break
        reports = [result.get("report") or {} for result in results]
        # One call: the call lasts until the page acknowledges; chunks: the slowest call that carries text
        calls = [result["call_s"] if chunk else result["ack_s"] for result in results if "ack_s" in result]
        return {"ok": all(result["ok"] for result in results) and len(results) == len(texts),
                "timeout": any(result["timeout"] for result in results),
                "total_s": round(time.perf_counter() - started, 4),
                "worst_call_s": round(max(calls, default=TIMEOUT_SECONDS), 4),
                "finish_s": round(max((result.get("finish_s", 0.0) for result in results), default=0.0), 4),
                "decode_ms": round(max((report.get("decode_ms") or 0 for report in reports), default=0), 1),
                "js_heap": reports[-1].get("js_heap") if reports else None,
                "working_set_peak": memory.peak,
                "error": next((result["error"] for result in results if result.get("error")), None)}

    def result(self) -> dict:
        """Return the complete result document with the rule and the proposed budgets."""
        measured = [row for row in self.rows if "call_chars" in row]
        rows = self.rows + [{"case": name, "status": reason} for name, reason in NOT_ATTEMPTED.items()]
        return {"environment": self.environment, "stopped": self.stopped,
                "rule": {"runs": RUNS, "call_seconds": CALL_SECONDS, "image_seconds": IMAGE_SECONDS,
                         "image_side_limit": IMAGE_SIDE_LIMIT,
                         "timeout_seconds": TIMEOUT_SECONDS, "chunk_sizes": list(CHUNK_SIZES),
                         "zlib_level": ZLIB_LEVEL},
                "rows": rows, "chunk_rows": self.chunk_rows,
                "budgets": propose_budgets(measured, self.chunk_rows)}

    def write(self) -> None:
        """Write results.json and results.md to the output folder."""
        document = self.result()
        write_bytes_atomically(self.out / "results.json", json.dumps(document, indent=2).encode("utf-8"))
        write_bytes_atomically(self.out / "results.md", markdown(document).encode("utf-8"))


def markdown(document: dict) -> str:
    """Return the result table and the proposed budgets as Markdown."""
    lines = ["| Case | Mode | Calls | Base64 per call | PNG bytes | PNG s | Base64 s | Worst call s "
             "| Worst finish s | Worst total s | Peak WS MiB | JS heap MiB | Result |", "|" + "---|" * 13]
    for row in document["rows"] + document["chunk_rows"]:
        if not row.get("runs"):
            lines.append(f"| {row['case']} |" + "  |" * 11 + f" {row['status']} |")
            continue
        heap = max((run["js_heap"] or 0 for run in row["runs"]), default=0)
        result = f"{sum(run['ok'] for run in row['runs'])}/{RUNS} ok" if row["status"] in ("ok", "failed") else row["status"]
        error = next((f" ({run['error']})" for run in row["runs"] if run["error"]), "")
        lines.append(
            f"| {row['case']} | {row['mode']} | {row['calls']} | {row['call_chars']:,} | {row['png_bytes']:,} "
            f"| {row['png_s']} | {row['base64_s']} | {worst(row, 'worst_call_s'):.3f} "
            f"| {worst(row, 'finish_s'):.3f} | {worst(row, 'total_s'):.3f} "
            f"| {worst(row, 'working_set_peak') / MIB:,.0f} | {heap / MIB:,.1f} | {result}{error} |")
    lines += ["", "Proposed budgets:", "", "```json", json.dumps(document["budgets"], indent=2), "```", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the measurement in one pywebview window; return 0 when every case ran to its end."""
    parser = argparse.ArgumentParser(description="Measure bridge transfer budgets through pywebview evaluate_js.")
    parser.add_argument("--out", type=Path, help="output folder (default: a new temporary folder)")
    parser.add_argument("--cases", help="comma-separated case names (default: every case)")
    args = parser.parse_args(argv)
    names = set(args.cases.split(",")) if args.cases else {case.name for case in CASES}
    unknown = names - {case.name for case in CASES}
    if unknown:
        parser.error(f"unknown case names: {', '.join(sorted(unknown))}")
    out = (args.out or Path(tempfile.mkdtemp(prefix="bridge-transfer-"))).resolve()
    import webview

    # Use the window settings of the application host
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["SHOW_DEFAULT_MENUS"] = False
    api = MeasureApi()
    window = webview.create_window("Bridge transfer measurement", html=compose_page(), js_api=api,
                                   width=900, height=600)
    measurement = Measurement(out, tuple(case for case in CASES if case.name in names))
    webview.start(measurement.run, (window, api), gui="edgechromium", debug=False, http_server=False,
                  private_mode=True)
    print(markdown(measurement.result()))
    print(f"Results: {out}")
    return 1 if measurement.stopped else 0


if __name__ == "__main__":
    sys.exit(main())
