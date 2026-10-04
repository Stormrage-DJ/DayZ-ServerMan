"""Dynamic headless Edge test of the Mods update states, check header, and quiet refresh."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.host.assets import compose_shell_html  # noqa: E402
from tests.test_mods_ui_dynamic import EDGE, ROOT, _center_rgb, _read_stable_screenshot  # noqa: E402


# Browser harness that drives the update states through a fake host and paints pass or fail
HARNESS = r"""
<script>
(async () => {
  const ok = (value) => ({success: true, value});
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const compact = (value) => new Date(typeof value === "number" ? value * 1000 : value)
    .toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
  const exact = (value) => new Date(typeof value === "number" ? value * 1000 : value)
    .toLocaleString(undefined, {dateStyle: "full", timeStyle: "long"});
  const row = (order, state, extra = {}) => ({order, name: `Mod ${order}`, directory: `@mod${order}`,
    launch_scope: "client", source_kind: "workshop", workshop_id: String(100 + order), version: null,
    state, time_updated: 1771519119, remote_time_updated: null, remote_check: null,
    pending_reason: null, ...extra});
  const status = (revision, checkState, extra = {}, checking = false) => ({mods: {check_state: checkState,
    checked_at: null, last_success_at: null, error_code: null, update_count: 0,
    pending_apply_count: 0, ...extra}, server_build: null, checking, revision});
  // Fake host: mutable answers and a record of every update-check call.
  const host = {rows: [row(1, "INSTALLED", {remote_check: "NEVER"})],
    status: status(1, "NEVER", {}, true), automatic: true, requests: [], statusReads: [],
    inventoryReads: 0, onForce: null};
  window.pywebview = {api: {
    get_application_snapshot: async () => ok({settings: {revision: 2,
      steam_authentication_mode: "ACCOUNT", steam_account_name: "operator"},
      operations: [], operation_session_id: "qa"}),
    list_profiles: async () => ok(["primary", "second"].map((id) => ({profile_id: id,
      display_name: id, revision: 3, semantic_digest: "a".repeat(64)}))),
    get_ui_preferences: async () => ok({selected_profile_id: "primary",
      automatic_update_checks: host.automatic}),
    save_selected_profile: async (id) => ok({selected_profile_id: id}),
    list_mod_inventory: async () => { host.inventoryReads += 1; return ok(host.rows); },
    get_update_status: async (id) => { host.statusReads.push(id); return ok(host.status); },
    request_update_check: async (scope, force) => { host.requests.push([scope, force]);
      if (force && host.onForce) host.onForce();
      return ok({accepted: force, checking: host.status.checking}); },
    update_workshop_items: async () => ok({operation_id: "update-one", state: "QUEUED"}),
  }};
  const cells = () => [...document.querySelectorAll(".mods-table tbody .mods-status")];
  const text = (id) => document.getElementById(id).textContent;
  // Land a new host state and run one shell poll after the idle interval.
  const land = async (next, rows) => { host.status = next; if (rows) host.rows = rows;
    updateStatusState.readAt -= 6000; await window.ServerManUpdateStatus.poll(true); await wait(20); };
  try {
    shellState.hostReady = true;
    commitSection("mods");
    await wait(100);

    // Start: one non-forced request, and a running first check reads "Checking", not a failure.
    assert(JSON.stringify(host.requests) === '[["mods",false]]', "non-forced request on open");
    assert(cells()[0].textContent === "Checking…", "checking start state");
    assert(text("mods-update-summary") === "Checking for updates…", "checking summary");
    assert(text("mods-last-checked") === "Not checked yet", "not checked yet");
    assert(!document.getElementById("mods-update-busy").hidden, "busy mark while checking");
    assert(document.getElementById("check-updates-now").disabled, "check now locked while checking");
    assert(document.querySelector("#mods-update-header [role=status]"), "polite status region");

    // The operator opens the sign-in form and types an account name while the check lands.
    document.getElementById("mods-signin-change").click();
    const account = document.getElementById("steam-account-name");
    account.value = "typed-name"; account.focus();
    const header = document.getElementById("mods-update-header");
    const success = "2026-10-03T13:41:07.120Z";
    const readsBefore = host.inventoryReads;
    await land(status(2, "OK", {checked_at: success, last_success_at: success,
      update_count: 1, pending_apply_count: 2}), [
      row(1, "CURRENT", {remote_check: "OK", remote_time_updated: 1771519119}),
      row(2, "UPDATE_AVAILABLE", {remote_check: "OK", remote_time_updated: 1790802598}),
      row(3, "PENDING_APPLY", {remote_check: "OK", pending_reason: "TARGET_MISSING"}),
      row(4, "PENDING_APPLY", {remote_check: "OK", pending_reason: "TARGET_UNPROVEN"}),
      row(5, "INSTALLED", {remote_check: "UNKNOWN_ITEM"}),
      row(6, "INSTALLED", {remote_check: "OK"}),
      row(7, "INSTALLED"),
      row(8, "NOT_DOWNLOADED", {remote_check: "NOT_APPLICABLE"}),
      row(9, "LOCAL", {remote_check: "NOT_APPLICABLE"}),
      row(10, "UNAVAILABLE", {remote_check: "NOT_APPLICABLE"}),
    ]);
    assert(host.inventoryReads === readsBefore + 1, "inventory re-read on revision change");
    assert(document.getElementById("steam-account-name") === account
      && account.value === "typed-name", "typed account name kept");
    assert(document.activeElement === account, "focus kept");
    assert(document.getElementById("mods-update-header") === header, "header kept in place");

    // Each label row of the presentation table.
    const labels = cells().map((cell) => cell.textContent);
    assert(labels[0] === "Current", "current");
    assert(labels[1] === `Update availableInstalled ${compact(1771519119)}Steam ${compact(1790802598)}`,
      "update available with both dates");
    assert(cells()[1].querySelectorAll("[title]")[1].title === exact(1790802598), "exact Steam date");
    assert(cells()[1].closest("tr").dataset.state === "UPDATE_AVAILABLE", "row state exposed");
    assert(labels[2] === "Downloaded - not applied", "pending apply, target missing");
    assert(labels[3] === "Downloaded - not applied"
      + "Folder exists but was never verified. Run Verify files or Update.", "unproven hint");
    assert(labels[4] === `Could not checkSteam does not list this itemLast checked ${compact(success)}`,
      "unknown item reason and last success");
    assert(labels[5] === "Installed" && labels[6] === "Installed", "installed kept");
    assert(labels[7] === "Not downloaded" && labels[8] === "Local"
      && labels[9] === "Unavailable", "existing labels kept");
    assert(text("mods-update-summary") === "1 update available · 2 downloaded - not applied",
      "summary uses the status counts");
    assert(text("mods-last-checked") === `Last checked ${compact(success)}`, "last checked");
    assert(document.getElementById("mods-last-checked").title === exact(success), "exact check time");
    assert(document.getElementById("mods-update-busy").hidden, "busy mark cleared");

    // Polling: no read inside the idle interval, no redraw without a change.
    const statusReads = host.statusReads.length;
    await window.ServerManUpdateStatus.poll(true);
    assert(host.statusReads.length === statusReads, "status read at most every five seconds");
    const body = document.querySelector(".mods-table tbody");
    const idleReads = host.inventoryReads;
    await land(host.status);
    assert(host.statusReads.length === statusReads + 1 && host.inventoryReads === idleReads
      && document.querySelector(".mods-table tbody") === body, "unchanged state leaves the table");

    // A failed check keeps the older success time and names the reason.
    const older = "2026-10-02T07:12:44.000Z";
    await land(status(3, "FAILED", {last_success_at: older, error_code: "NETWORK_UNREACHABLE"}),
      [row(1, "INSTALLED", {remote_check: "FAILED", remote_time_updated: 1771519119})]);
    assert(cells()[0].textContent
      === `Could not checkSteam could not be reachedLast checked ${compact(older)}`, "failed reason");
    assert(text("mods-update-summary") === "Could not check: Steam could not be reached",
      "failed summary");
    assert(text("mods-last-checked") === `Last checked ${compact(older)}`, "older success shown");
    await land(status(4, "STALE", {last_success_at: older}), [row(1, "INSTALLED", {remote_check: "STALE"})]);
    assert(cells()[0].textContent.includes("The last check is too old"), "stale reason");
    await land(status(5, "NEVER"), [row(1, "INSTALLED", {remote_check: "NEVER"})]);
    assert(cells()[0].textContent === "Could not checkNot checked yetNo successful check yet",
      "never reason");

    // A pending workshop operation keeps its feedback and its locked controls across a refresh.
    document.getElementById("update-workshop").click();
    await wait(0);
    const update = document.getElementById("update-workshop");
    window.scrollTo(0, 40);
    const scrolled = window.scrollY;
    await land(status(6, "STALE", {last_success_at: older}), [row(1, "INSTALLED", {remote_check: "STALE"})]);
    assert(text("mods-feedback") === "Downloading or updating mods", "operation feedback kept");
    assert(document.getElementById("update-workshop") === update && update.disabled,
      "pending operation kept");
    assert(window.scrollY === scrolled, "scroll kept");
    assert(account.value === "typed-name", "typed account name kept during an operation");
    modsState.pending = null;

    // Automatic checks off: the missing or old check names the switch.
    host.automatic = false; host.status = status(7, "NEVER");
    host.rows = [row(1, "INSTALLED", {remote_check: "NEVER"})];
    await window.ServerManMods.open();
    assert(cells()[0].textContent.includes("Automatic checks are off"), "automatic off row reason");
    assert(text("mods-update-summary") === "Could not check: automatic checks are off",
      "automatic off summary");
    await land(status(8, "STALE", {last_success_at: older}), [row(1, "INSTALLED", {remote_check: "STALE"})]);
    assert(cells()[0].textContent.includes("Automatic checks are off"), "automatic off stale reason");

    // "Check now" sends a forced request and shows the running check.
    host.onForce = () => { host.status = status(9, "STALE", {last_success_at: older}, true); };
    const button = document.getElementById("check-updates-now");
    button.focus(); button.click();
    await wait(20);
    assert(JSON.stringify(host.requests.at(-1)) === '["mods",true]', "check now sends force=true");
    assert(button.isConnected && button.disabled
      && !document.getElementById("mods-update-busy").hidden, "check now shows the running check");
    await land(status(10, "OK", {checked_at: success, last_success_at: success}), [
      row(1, "CURRENT", {remote_check: "OK"}), row(2, "LOCAL", {remote_check: "NOT_APPLICABLE"})]);
    assert(text("mods-update-summary") === "All 2 mods are current", "all current summary");
    assert(!button.disabled && cells()[0].textContent === "Current", "check result shown");

    // A profile change sends a new non-forced request and reads the new profile's state.
    const requests = host.requests.length;
    window.ServerManProfileContext.select("second");
    await wait(100);
    assert(host.requests.length === requests + 1 && host.requests.at(-1)[1] === false,
      "non-forced request on profile change");
    assert(host.statusReads.at(-1) === "second", "status follows the selected profile");
    document.body.replaceChildren(); document.body.style.background = "rgb(0, 255, 0)";
  } catch (error) {
    document.body.replaceChildren(); document.body.style.background = "rgb(255, 0, 0)";
    document.body.textContent = error.message;
  }
})();
</script>
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ModsUpdateStatusDynamicTests(unittest.TestCase):
    """Contract: the Mods page shows every update state and refreshes without disturbing input."""
    def test_labels_reasons_header_check_now_and_quiet_refresh(self) -> None:
        """Drive each label row, the reasons, the header, Check now, and revision refresh."""
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
