"""Dynamic headless Edge test of the Mods "Verify files" control, progress, and per-mod result."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.host.assets import compose_shell_html  # noqa: E402
from tests.test_mods_ui_dynamic import EDGE, ROOT, _center_rgb, _read_stable_screenshot  # noqa: E402


# Browser harness that drives a verification through a fake host and paints pass or fail
HARNESS = r"""
<script>
(async () => {
  const ok = (value) => ({success: true, value});
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const row = (order, extra = {}) => ({order, name: `Mod ${order}`, directory: `@mod${order}`,
    launch_scope: "client", source_kind: "workshop", workshop_id: String(100 + order), version: null,
    state: "CURRENT", time_updated: 1771519119, remote_time_updated: 1771519119, remote_check: "OK",
    pending_reason: null, ...extra});
  const local = (order) => row(order, {source_kind: "local", workshop_id: null, state: "LOCAL",
    remote_check: "NOT_APPLICABLE"});
  // Fake host: mutable rows and a record of every verification, cancel, and read call.
  const host = {rows: [1, 2, 3, 4, 5].map((order) => row(order)).concat([local(6)]),
    verifyCalls: [], cancels: [], requests: [], statusReads: 0, inventoryReads: 0};
  window.pywebview = {api: {
    get_application_snapshot: async () => ok({settings: {revision: 2,
      steam_authentication_mode: "ACCOUNT", steam_account_name: "operator"},
      operations: [], operation_session_id: "qa"}),
    list_profiles: async () => ok([{profile_id: "primary", display_name: "Primary",
      revision: 3, semantic_digest: "a".repeat(64)}]),
    get_ui_preferences: async () => ok({selected_profile_id: "primary",
      automatic_update_checks: true}),
    save_selected_profile: async (id) => ok({selected_profile_id: id}),
    list_mod_inventory: async () => { host.inventoryReads += 1; return ok(host.rows); },
    get_update_status: async () => { host.statusReads += 1; return ok({mods: {check_state: "OK",
      checked_at: null, last_success_at: "2026-10-03T13:41:07.120Z", error_code: null,
      update_count: 0, pending_apply_count: 0}, server_build: null, checking: false, revision: 1}); },
    request_update_check: async (scope, force) => { host.requests.push([scope, force]);
      return ok({accepted: false, checking: false}); },
    authenticate_steamcmd: async () => ok({operation_id: "auth-one", state: "QUEUED"}),
    verify_mod_files: async (...values) => { host.verifyCalls.push(values);
      return ok({operation_id: `verify-${host.verifyCalls.length}`, state: "QUEUED"}); },
    request_operation_cancellation: async (id) => { host.cancels.push(id);
      return ok({operation_id: id, state: "CANCELLING"}); },
  }};
  const cells = () => [...document.querySelectorAll(".mods-table tbody .mods-status")];
  const feedback = () => document.getElementById("mods-feedback");
  const verify = () => document.getElementById("verify-mod-files");
  // Build one operation record of the running or finished verification.
  const operation = (state, extra = {}) => ({operation_id: `verify-${host.verifyCalls.length}`,
    kind: "VERIFY_WORKSHOP_FILES", state, cancellable: true, progress_phase: null,
    progress_percent: 0, progress_detail: null, result: null, terminal_error: null, ...extra});
  const item = (order, source, target) => ({workshop_id: String(100 + order), source_state: source,
    target_state: target, verified_at: "2026-10-03T13:41:07.120+00:00", error_code: null});
  const detail = (phases) => ({items: phases.map((phase, index) => ({workshop_id: String(101 + index),
    phase, done_bytes: null, total_bytes: null}))});
  // Click "Verify files" and wait until the host call was accepted.
  const start = async () => { verify().click(); await wait(20); };
  try {
    shellState.hostReady = true;
    commitSection("mods");
    await wait(100);

    // The button sits beside "Check now", is secondary, and describes its cost.
    const actions = document.querySelector("#mods-update-header .mods-header-actions");
    assert(actions.children[0].id === "check-updates-now" && actions.children[1] === verify(),
      "verify button next to check now");
    assert(verify().textContent === "Verify files" && verify().className === "button"
      && verify().type === "button", "secondary button");
    const description = document.getElementById(verify().getAttribute("aria-describedby"));
    assert(description.textContent.includes("Reads all mod files")
      && description.textContent.includes("can take minutes"), "accessible description");
    assert(!verify().disabled, "enabled for a profile with Workshop mods");

    // Disabled while another workshop operation is pending, enabled again when it ends.
    document.getElementById("authenticate-steamcmd").click();
    await wait(20);
    assert(verify().disabled, "disabled while an operation is pending");
    window.ServerManMods.operationFinished({operation_id: "auth-one", state: "SUCCEEDED",
      kind: "AUTHENTICATE_STEAMCMD"});
    await wait(50);
    assert(!verify().disabled, "enabled after the operation");

    // The operator types an account name; it must survive the whole verification.
    const account = document.getElementById("steam-account-name");
    account.value = "typed-name";

    // Start: the call carries profile, profile revision, and settings revision.
    await start();
    assert(JSON.stringify(host.verifyCalls) === '[["primary",3,2]]', "call arguments");
    assert(feedback().textContent === "Verifying downloaded files", "start wording");
    assert(!document.getElementById("cancel-workshop-operation") && verify().disabled
      && document.getElementById("update-workshop").disabled, "busy state through the pending flow");

    // Progress: operator wording per phase and the row in progress is marked.
    window.ServerManMods.operationFinished(operation("RUNNING", {progress_phase: "verify_source",
      progress_percent: 5, progress_detail: detail(["verify_source", "queued"])}));
    assert(feedback().textContent === "Verifying downloaded files", "the page prints no percent");
    assert(cells()[0].textContent === "CurrentVerifying…" && cells()[1].textContent === "Current",
      "first row marked");
    window.ServerManMods.operationFinished(operation("RUNNING", {progress_phase: "verify_target",
      progress_percent: 41, progress_detail: detail(["done", "verify_target"])}));
    window.ServerManOperationBar.sync(operation("RUNNING", {revision: 3, cancellable: true,
      progress_phase: "verify_target", progress_percent: 41}));
    const barText = document.getElementById("operation-bar").textContent;
    assert(barText.includes("Verifying server folder copies") && barText.includes("41%"),
      "target phase wording and percent in the operation bar");
    assert(cells()[0].textContent === "Current" && cells()[1].textContent === "CurrentVerifying…",
      "mark moved to the second row");
    assert(!feedback().textContent.includes("verify_"), "no raw phase name");

    // Cancel goes through the operation bar.
    [...document.querySelectorAll("#operation-bar button")]
      .find((button) => button.textContent === "Cancel").click();
    await wait(20);
    assert(JSON.stringify(host.cancels) === '["verify-1"]', "cancel request for the verification");
    // The event poll gives the end of the operation to the bar first; that unlocks the page.
    window.ServerManOperationBar.sync(operation("CANCELLED", {revision: 9}));
    window.ServerManMods.operationFinished(operation("CANCELLED",
      {terminal_error: {code: "CANCELLED", message: "file verification was cancelled"}}));
    assert(feedback().textContent === "Verification cancelled. Finished mods stay recorded.",
      "cancelled wording");
    assert(feedback().querySelector("[role=status]") && !feedback().querySelector("[role=alert]")
      && !feedback().querySelector(".notice-error"), "cancellation is not an error");
    assert(!verify().disabled && cells()[1].textContent === "Current",
      "page unlocked and mark removed after the cancellation");

    // Success with problems: one summary line and each problem wording in its row.
    await start();
    const reads = [host.inventoryReads, host.statusReads];
    window.ServerManMods.operationFinished(operation("SUCCEEDED", {result: {items: [
      item(1, "VERIFIED", "MATCHES_SOURCE"), item(2, "MISSING", "NOT_APPLIED"),
      item(3, "CHANGED", "DIFFERS"), item(4, "FAILED", "FAILED"), item(5, "VERIFIED", "FAILED")]}}));
    assert(feedback().textContent === "4 problems found", "problem summary counts the mods");
    assert(feedback().querySelector("[role=status]") && !feedback().querySelector(".notice-error"),
      "problems are a result, not a host error");
    const labels = cells().map((cell) => cell.textContent);
    assert(labels[0] === "Current", "verified row has no line");
    assert(labels[1] === "CurrentDownload missingNot applied to the server folder",
      "missing download and no server copy");
    assert(labels[2] === "CurrentDownload changed since it was recorded"
      + "Server copy differs from the download", "changed download and other copy");
    assert(labels[3] === "CurrentDownload could not be read", "unreadable download");
    assert(labels[4] === "CurrentServer copy could not be read", "unreadable server copy");
    assert(labels[5] === "Local", "local row untouched");
    assert(cells()[1].querySelectorAll(".mods-status-detail-problem").length === 2, "problem tone");
    // Status and inventory are read again, because target proofs changed.
    await wait(50);
    assert(host.inventoryReads > reads[0] && host.statusReads > reads[1], "re-read after success");
    assert(labels.join() === cells().map((cell) => cell.textContent).join(),
      "problem lines survive the re-read");
    assert(document.getElementById("steam-account-name") === account
      && account.value === "typed-name", "typed account name kept");

    // A new verification clears the old lines; a clean result names all mods.
    await start();
    assert(cells()[1].textContent === "Current", "old problem lines cleared at the start");
    window.ServerManMods.operationFinished(operation("SUCCEEDED", {result: {items:
      [1, 2, 3, 4, 5].map((order) => item(order, "VERIFIED", "MATCHES_SOURCE"))}}));
    assert(feedback().textContent === "All 5 mods verified; server copies match", "clean summary");
    await start();
    window.ServerManMods.operationFinished(operation("SUCCEEDED", {result: {items:
      [item(1, "VERIFIED", "DIFFERS")]}}));
    assert(feedback().textContent === "1 problem found", "singular summary");

    // A failed verification is an error with the failure sentence and the operator wording of the code.
    await start();
    window.ServerManMods.operationFinished(operation("FAILED",
      {terminal_error: {code: "REVISION_CONFLICT", message: "The selected profile changed."}}));
    assert(feedback().querySelector("[role=alert]").textContent === "The mod files could not be verified. "
      + "The profile or the settings changed in the meantime. Open another page, return to this one, and try again.",
      "failure message");
    assert(cells()[0].textContent === "Current", "no line after a failure");

    // Without a Workshop mod there is nothing to verify.
    await wait(50);
    host.rows = [local(1)];
    await window.ServerManMods.open();
    assert(verify().disabled && !document.getElementById("check-updates-now").disabled,
      "disabled without a Workshop mod");
    document.body.replaceChildren(); document.body.style.background = "rgb(0, 255, 0)";
  } catch (error) {
    document.body.replaceChildren(); document.body.style.background = "rgb(255, 0, 0)";
    document.body.textContent = error.message;
  }
})();
</script>
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ModsVerifyDynamicTests(unittest.TestCase):
    """Contract: the operator can run "Verify files" and read its result per mod."""
    def test_button_progress_result_cancel_and_reread(self) -> None:
        """Drive the button rules, the call, progress, each problem wording, and a cancel."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Compose the shell page with the browser harness appended
            page = root / "mods.html"
            html = compose_shell_html(ROOT / "runnable" / "src" / "frontend").replace(
                "</body>", HARNESS + "</body>"
            )
            page.write_text(html, encoding="utf-8")
            screenshot = root / "result.png"
            profile = root / "edge-data"
            # Execute the harness in headless Edge
            completed = subprocess.run([
                str(EDGE), "--headless", "--disable-gpu", "--no-first-run",
                "--hide-scrollbars", "--window-size=800,560", "--virtual-time-budget=4000",
                f"--user-data-dir={profile}", f"--screenshot={screenshot}", page.as_uri(),
            ], capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            # The harness paints the body green only when every check passed
            red, green, blue = _center_rgb(_read_stable_screenshot(screenshot, profile))
            self.assertGreater(green, 240, "the harness reported a failed check")
            self.assertLess(red, 15)
            self.assertLess(blue, 15)


if __name__ == "__main__":
    unittest.main()
