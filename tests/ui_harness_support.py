"""Shared headless Edge runner for harness scripts that drive the composed shell."""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runnable" / "src" / "python"))

from dayz_serverman.host.assets import compose_shell_html  # noqa: E402


FRONTEND = ROOT / "runnable" / "src" / "frontend"
EDGE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / (
    "Microsoft/Edge/Application/msedge.exe"
)
# Result element that a harness fills with PASS or with its failure text
RESULT_PATTERN = re.compile(r'<pre id="harness-result"[^>]*>(.*?)</pre>', re.DOTALL)

# Helpers shared by every harness: host answers, waiting, checks, operation records, and a fake host
PRELUDE = r"""
const ok = (value) => ({success: true, value});
const fail = (code, message) => ({success: false, error: {code, message, retryable: false}});
const wait = (milliseconds = 0) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const check = (condition, message) => { if (!condition) throw new Error(message); };
const bar = () => document.getElementById("operation-bar");
const barText = () => bar().textContent;
const barButton = (label) => [...bar().querySelectorAll("button")]
  .find((button) => button.textContent === label);
const record = (id, kind, state, extra = {}) => ({schema_version: 1, revision: 1, operation_id: id,
  kind, state, accepted_at: "2026-10-03T10:00:00.000+00:00", started_at: null, finished_at: null,
  cancellation_requested: false, progress_percent: 0, progress_phase: "queued", result: null,
  terminal_error: null, progress_detail: null, target_profile_id: null, last_working_phase: null,
  cancellable: false, ...extra});
const snapshot = (operations = []) => ({settings: {revision: 2, dayz_root: "D:\\Server",
  dayz_executable: "D:\\Server\\DayZServer_x64.exe", steamcmd_root: null, steamcmd_executable: null,
  workshop_content_root: null, custom_backup_root: null, steam_authentication_mode: "ACCOUNT",
  steam_account_name: "operator"}, diagnostics: [], operations, operation_session_id: "harness",
  mutation_block: null, portable_backup_root: "D:\\Manager\\backups"});
const profileFields = {server_executable: "DayZServer_x64.exe", server_config: "serverDZ.cfg",
  mission_root: "mpmissions\\dayzOffline.chernarusplus", game_port: 2302, mods: [], extra_arguments: []};
const profiles = [{profile_id: "alpha", display_name: "Alpha", revision: 3,
  semantic_digest: "a".repeat(64), runtime_profile: "serverman\\alpha\\profile", ...profileFields},
  {profile_id: "bravo", display_name: "Bravo", revision: 1, semantic_digest: "b".repeat(64),
  runtime_profile: null, ...profileFields}];
// Fake host: tests change its fields; every read method has a neutral answer.
const host = {events: [], operations: new Map(), snapshotOperations: [], cancelRequests: [],
  cancelAnswer: null, revision: 1,
  status: {state: "STOPPED", process_id: null, diagnostic_code: null, readiness: null, query_port: null,
    profile_id: null, started_at: null},
  updates: {mods: {check_state: "OK", checked_at: null, last_success_at: null, error_code: null,
    update_count: 0, pending_apply_count: 0}, server_build: null, checking: false, revision: 1}};
window.pywebview = {api: {
  get_application_snapshot: async () => ok(snapshot(host.snapshotOperations)),
  list_profiles: async () => ok(profiles),
  get_ui_preferences: async () => ok({selected_profile_id: "alpha", backup_after_stop_profiles: [],
    automatic_update_checks: true}),
  save_selected_profile: async (id) => ok({selected_profile_id: id}),
  get_server_status: async () => ok(host.status),
  get_lifecycle_schedule: async () => ok({enabled: false, action: null, hour: 4, minute: 0,
    next_run_local: null, last_status: null}),
  read_operation_events: async () => { const batch = host.events; host.events = [];
    return ok({session_id: "harness", next_cursor: 1, events: batch}); },
  get_operation: async (id) => ok(host.operations.get(id)),
  request_operation_cancellation: async (id) => { host.cancelRequests.push(id);
    return host.cancelAnswer ? host.cancelAnswer(id) : ok({...host.operations.get(id), state: "CANCELLING",
      revision: (host.revision += 1)}); },
  read_log: async (source) => ok({source, lines: ["line"], truncated: false, revision: 1}),
  // The shell reads the update state in every section; the neutral answer shows no badge.
  get_update_status: async () => ok(host.updates),
  request_update_check: async () => ok({accepted: false, checking: false}),
}};
// Deliver one operation record through the real event poll; the timer is stopped so polls never overlap.
const push = async (operation) => {
  const value = {...operation, revision: (host.revision += 1)};
  host.operations.set(value.operation_id, value);
  host.events.push({operation_id: value.operation_id});
  await pollEvents(); window.clearTimeout(shellState.pollTimer);
  return value;
};
// Load the first snapshot like the host does, then open the requested section.
const start = async (section = "overview") => {
  await loadSnapshot(); window.clearTimeout(shellState.pollTimer); await wait(20);
  if (section !== "overview") { commitSection(section); await wait(20); }
};
"""


def run_shell_harness(body: str, *, window_size: str = "1100,800", budget: int = 4000) -> str:
    """Run one harness body inside the composed shell and return its result text.

    The body runs in an async function after the shared prelude. A thrown
    error becomes the failure text; a normal end becomes PASS.
    """
    harness = (
        "<pre id=\"harness-result\" hidden>PENDING</pre><script>\n(async () => {\n"
        "const harnessResult = document.getElementById(\"harness-result\");\n"
        + PRELUDE + "\ntry {\n" + body
        + "\nharnessResult.textContent = \"PASS\";\n} catch (error) {\n"
        "harnessResult.textContent = `FAIL: ${error.stack || error.message}`;\n}\n})();\n</script>"
    )
    # Edge may still hold its profile for a moment, so a cleanup error is not a test failure
    with tempfile.TemporaryDirectory(
            prefix="serverman_shell_harness_", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        page = root / "shell.html"
        page.write_text(
            compose_shell_html(FRONTEND).replace("</body>", harness + "</body>"), encoding="utf-8",
        )
        # Dump the final document after the virtual time budget ran every timer
        completed = subprocess.run(
            [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
             f"--window-size={window_size}", f"--virtual-time-budget={budget}",
             f"--user-data-dir={root / 'edge-data'}", "--dump-dom", page.as_uri()],
            capture_output=True, text=True, timeout=40, check=False, encoding="utf-8",
            errors="replace",
        )
    found = RESULT_PATTERN.findall(completed.stdout)
    # The last match is the live element; earlier matches are text inside the script source
    return found[-1] if found else f"NO RESULT (exit {completed.returncode}): {completed.stderr[-400:]}"


# Defines harnessVerdict(text) for a self-contained page harness; it writes the result element
VERDICT_SCRIPT = (
    "<script>\nwindow.harnessVerdict = (text) => { const result = document.createElement(\"pre\");\n"
    "  result.id = \"harness-result\"; result.hidden = true; result.textContent = text;\n"
    "  document.body.append(result); };\n</script>\n"
)


def run_page_harness(harness: str, *, window_size: str, budget: int, timeout: int = 40) -> str:
    """Run a self-contained `<script>` harness in the composed shell and return its verdict text.

    The harness calls `harnessVerdict("PASS")` or `harnessVerdict("FAIL: ...")` at its end. The verdict is
    read from the dumped document, so a failed check reports its own message (QF-082). A missing verdict
    reports PENDING with Edge's exit code and the end of its error output.
    """
    # Edge may still hold its profile for a moment, so a cleanup error is not a test failure
    with tempfile.TemporaryDirectory(
            prefix="serverman_page_harness_", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        page = root / "page.html"
        page.write_text(compose_shell_html(FRONTEND).replace(
            "</body>", VERDICT_SCRIPT + harness + "</body>"), encoding="utf-8")
        completed = subprocess.run(
            [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run",
             f"--window-size={window_size}", f"--virtual-time-budget={budget}",
             f"--user-data-dir={root / 'edge-data'}", "--dump-dom", page.as_uri()],
            capture_output=True, text=True, timeout=timeout, check=False, encoding="utf-8",
            errors="replace",
        )
    found = RESULT_PATTERN.findall(completed.stdout)
    if not found:
        return f"PENDING (exit {completed.returncode}): {completed.stderr[-400:]}"
    return html.unescape(found[-1])
