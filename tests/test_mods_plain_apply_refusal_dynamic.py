"""Headless Edge checks of decision D10 on the Mods page: a writing plain apply needs a stopped server."""
from __future__ import annotations

import unittest

try:
    from tests.mods_update_harness import MODS_HOST
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from mods_update_harness import MODS_HOST
    from ui_harness_support import EDGE, run_shell_harness

# Shared helpers: the review after "Update all" in a given server state, with the host policy "refuse"
HEAD = MODS_HOST + r"""
await start("mods"); await wait(50);
const answer = (current, missing = 0) => () => ok({profile_id: "alpha", publication_fingerprint: "b".repeat(64),
  key_count: 2, missing_key_count: missing, plain_apply_guarded: true,
  targets: [{workshop_id: "111", target_relative: "mods\\alpha", current}]});
const sentence = () => dialog().querySelector(".mod-publication-variant").textContent;
const buttons = () => [...dialog().querySelectorAll("button")].map((button) => button.textContent).join("|");
const review = async (state) => { await serverState("STOPPED");
  const id = await pressUpdate(false); await serverState(state);
  await finish(id, "UPDATE_WORKSHOP_ITEMS", updateResult()); };
const refusal = " Mods cannot be applied to the server folder now.";
"""

# The refusal variant per state: the state as it was read, the refusal, advice that is true, and only "Close"
REFUSAL = HEAD + r"""
previewAnswer = answer(false);
for (const [state, text] of [
  ["RUNNING_MANAGED", `The server is running.${refusal} Use Update & restart, or stop the server first.`],
  ["RUNNING_EXTERNAL", `The server is running outside DayZ-ServerMan.${refusal} Stop the server first, then update again.`],
  ["STARTING", `The server is starting.${refusal} Wait until the server runs, then use Update & restart.`],
  ["STOPPING", `The server is stopping.${refusal} Wait until the server is stopped, then update again.`],
  ["UNKNOWN", `The server state could not be confirmed.${refusal} Check the server state on Overview.`],
  ["AMBIGUOUS", `The server state could not be confirmed.${refusal} Check the server state on Overview.`]]) {
  await review(state);
  same(sentence(), text, `refusal, ${state}`);
  same(buttons(), "Close", `refusal buttons, ${state}`);
  dialogButton("Close").click();
}
// A failed read of the state claims no state and sends the operator to Overview.
window.pywebview.api.get_server_status = async () => fail("PROCESS_STATE_UNKNOWN", "x");
await review("STOPPED");
same(sentence(), `The server state could not be confirmed.${refusal} Check the server state on Overview.`, "failed read");
check(!sentence().includes("not stopped") && !sentence().includes("is running"), "a failed read claims a state");
same(buttons(), "Close", "failed read buttons");
dialogButton("Close").click();
window.pywebview.api.get_server_status = async () => ok(host.status);
check(calls.published.length === 0 && calls.restarted.length === 0, "a refused review submitted something");
// Only advice that the page can keep: the restart action is named for a managed server alone.
check([...Object.values(modReviewRefusedAdvice)].filter((text) => text.includes("Update & restart, or")).length === 1,
  "the restart advice is offered for more than one state");
// A missing key file also writes, so it is refused too.
previewAnswer = answer(true, 1);
await review("RUNNING_MANAGED");
check(sentence().startsWith(`The server is running.${refusal}`) && buttons() === "Close", "a missing key is not refused");
dialogButton("Close").click();
// An apply that writes nothing is not refused in any state.
previewAnswer = answer(true);
for (const state of ["RUNNING_MANAGED", "RUNNING_EXTERNAL", "STARTING", "UNKNOWN"]) {
  await review(state);
  check(sentence() === "No server start was requested." && buttons() === "Cancel|Apply mods and keys",
    `no-write apply, ${state}: ${sentence()} ${buttons()}`);
  dialogButton("Cancel").click();
}
// A stopped server applies as before.
previewAnswer = answer(false);
await review("STOPPED");
same(sentence(), "No server start was requested.", "stopped");
dialogButton("Apply mods and keys").click(); await wait(30);
same(calls.published.length, 1, "stopped apply submitted");
await finish("publish-1", "PUBLISH_MODS_AND_KEYS", {profile_id: "alpha", start_state: "NOT_REQUESTED"});
same(feedback(), "Mods and keys applied. Server start was not requested.", "stopped result");
"""

# A state that changes after the review: the page redraws at confirm, and a host refusal is told as it is
CHANGED = HEAD + r"""
previewAnswer = answer(false);
// The state changes between the review and the confirm: nothing is submitted, and the refusal is shown.
await review("STOPPED");
host.status = {...host.status, state: "STARTING"};
dialogButton("Apply mods and keys").click(); await wait(30);
same(calls.published.length, 0, "a changed state was submitted");
same(sentence(), `The server is starting.${refusal} Wait until the server runs, then use Update & restart.`, "redrawn");
same(buttons(), "Close", "redrawn buttons");
dialogButton("Close").click();
// The state changes after the confirm: the host refuses, and the page says so without a code.
const refusedByHost = async (code, message) => { await review("STOPPED");
  dialogButton("Apply mods and keys").click(); await wait(30);
  await finish(`publish-${calls.published.length}`, "PUBLISH_MODS_AND_KEYS", null, "FAILED",
    {last_working_phase: "CACHE_PROOF_RECHECK", progress_phase: "failed", terminal_error: {code, message}});
  check(document.querySelector("#mods-feedback [role=alert]"), `${code}: the refusal is not an alert`);
  check(!/[A-Z]{2,}_[A-Z_]+/.test(feedback()), `${code}: raw identifier in ${feedback()}`);
  check(!statusCells().some((node) => node.textContent.includes("Applying")), `${code}: a row is still marked`);
  return feedback(); };
same(await refusedByHost("CONTROL_CONFLICT", "Applying mods requires STOPPED; current state is STARTING."),
  "The mods could not be applied. This cannot be done while the server is starting.", "host refusal, starting");
same(await refusedByHost("CONTROL_CONFLICT", "Applying mods requires STOPPED; current state is RUNNING_MANAGED."),
  "The mods could not be applied. This cannot be done while the server is running.", "host refusal, running");
same(await refusedByHost("EXTERNAL_PROCESS", "Applying mods requires STOPPED; current state is RUNNING_EXTERNAL."),
  "The mods could not be applied. This cannot be done while the server is running outside DayZ-ServerMan.",
  "host refusal, external");
same(await refusedByHost("PROCESS_STATE_UNKNOWN", "Applying mods requires STOPPED; current state is UNKNOWN."),
  "The mods could not be applied. This cannot be done while the server is in an unknown state.", "host refusal, unknown");
same(await refusedByHost("CONTROL_CONFLICT", "Another manager controls this DayZ installation."),
  "The mods could not be applied. Another DayZ-ServerMan is using this DayZ installation. Close it and try again.",
  "host refusal, busy installation");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ModsPlainApplyRefusalDynamicTests(unittest.TestCase):
    """Decision D10 (refuse) in the running page."""

    def test_refusal_variant_per_server_state(self) -> None:
        """Each state that is not stopped: true sentence, only "Close"; a no-write apply is offered."""
        self.assertEqual(run_shell_harness(REFUSAL, budget=30000), "PASS")

    def test_state_change_after_the_review_is_told_truthfully(self) -> None:
        """A change before the confirm redraws the review; a host refusal is worded by its state."""
        self.assertEqual(run_shell_harness(CHANGED, budget=30000), "PASS")


if __name__ == "__main__":
    unittest.main()
