"""Headless Edge checks of the Mods sign-in line, the two feedback regions, and the refusal dialog."""
from __future__ import annotations

import unittest

try:
    from tests.mods_update_harness import MODS_HOST
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from mods_update_harness import MODS_HOST
    from ui_harness_support import EDGE, run_shell_harness


# Sign-in fakes over the Mods host: settable saved settings and recorded sign-in requests
HEAD = MODS_HOST + r"""
const signin = {mode: "ACCOUNT", account: "operator", saved: [], logins: 0, checks: 0};
window.pywebview.api.get_application_snapshot = async () => { const value = snapshot(host.snapshotOperations);
  value.settings.steam_authentication_mode = signin.mode; value.settings.steam_account_name = signin.account;
  return ok(value); };
window.pywebview.api.save_steam_settings = async (...args) => { signin.saved.push(args);
  return queued("save", "SAVE_STEAM_SETTINGS"); };
window.pywebview.api.authenticate_steamcmd = async () => { signin.logins += 1;
  return queued(`login-${signin.logins}`, "AUTHENTICATE_STEAMCMD"); };
const baseCheck = window.pywebview.api.request_update_check;
window.pywebview.api.request_update_check = async (...args) => { signin.checks += 1; return baseCheck(...args); };
const panel = () => document.querySelector(".mods-signin");
const form = () => byId("mods-signin-form");
const signinFeedback = () => byId("mods-signin-feedback").textContent;
const labels = (root) => [...root.querySelectorAll("button")].map((button) => button.textContent).join("|");
"""

# Specification 5.1 section 6: the line, "Change", the form, "Cancel", and the two feedback regions
SIGNIN = HEAD + r"""
await start("mods"); await wait(60);
// Configured: one line with "Change"; the form is closed; the panel names no server.
same(panel().querySelector(".mods-signin-summary").textContent, "Steam sign-in: account operator", "line");
const change = byId("mods-signin-change");
check(change.textContent === "Change" && change.getAttribute("aria-label") === "Change Steam sign-in"
  && change.getAttribute("aria-expanded") === "false" && change.getAttribute("aria-controls") === "mods-signin-form",
  "Change button");
check(form().hidden && getComputedStyle(form()).display === "none", "the form is open for a configured sign-in");
check(!panel().querySelector("h2") && !panel().textContent.includes("Alpha")
  && !byId("content-region").textContent.includes("Steam access"), "the panel still has its old heading or a server name");
check(document.querySelector(".mods-panel") === panel() && panel().nextElementSibling
  === document.querySelector(".mods-inventory-panel"), "panel order");
// "Change" opens the form in place: fields, the credential notice directly after them, then the actions.
change.focus(); change.click();
check(!form().hidden && change.getAttribute("aria-expanded") === "true" && document.activeElement === change,
  "Change did not open the form, or moved the focus");
const parts = [...form().children];
check(parts[0].classList.contains("mods-access-row") && parts[1].textContent.startsWith("Credential safety")
  && parts[2].classList.contains("mods-actions"), "form order");
same(labels(form()), "Save sign-in settings|Open SteamCMD login|Cancel", "form actions");
check(form().contains(byId("authenticate-steamcmd")) && panel().querySelectorAll("#authenticate-steamcmd").length === 1,
  "the login action is outside the form");
// Every sign-in setting is editable; "Cancel" restores the saved values and returns the focus.
const mode = byId("steam-auth-mode"); const account = byId("steam-account-name");
same([...mode.options].map((option) => option.textContent).join("|"), "Choose mode|Steam account|Anonymous", "modes");
check(account.maxLength === 64 && !account.disabled && !mode.disabled, "fields");
mode.value = "ANONYMOUS"; mode.dispatchEvent(new Event("change", {bubbles: true}));
check(account.value === "" && account.disabled, "anonymous did not clear and lock the account name");
byId("mods-signin-cancel").click();
check(form().hidden && mode.value === "ACCOUNT" && account.value === "operator" && !account.disabled
  && document.activeElement === change && change.getAttribute("aria-expanded") === "false", "Cancel");
// The login reports in the sign-in panel, also its cancellation and its failure.
change.click();
byId("authenticate-steamcmd").click(); await wait(20);
same(signinFeedback(), "Opening visible SteamCMD authentication", "login start line");
same(feedback(), "", "the sign-in start line was written under the check header");
await push(record("login-1", "AUTHENTICATE_STEAMCMD", "CANCELLING", {target_profile_id: null})); await wait(20);
check(signinFeedback().startsWith("Cancelling."), `cancelling line: ${signinFeedback()}`);
await finish("login-1", "AUTHENTICATE_STEAMCMD", null, "FAILED", {progress_phase: "failed",
  terminal_error: {code: "AUTHENTICATION_FAILED", message: "x"}});
check(byId("mods-signin-feedback").querySelector("[role=alert]")
  && signinFeedback().includes("SteamCMD closed before the sign-in was confirmed."), `login failure: ${signinFeedback()}`);
byId("authenticate-steamcmd").click(); await wait(20);
await finish("login-2", "AUTHENTICATE_STEAMCMD", {authenticated: true});
same(signinFeedback(), "Steam sign-in completed. Credentials remain owned by SteamCMD.", "login result");
same(feedback(), "", "a sign-in result was written under the check header");
// Saving sends the edited values; after the save the page reopens with the form closed and the new line.
mode.value = "ANONYMOUS"; mode.dispatchEvent(new Event("change", {bubbles: true}));
byId("save-steam-settings").click(); await wait(20);
same(JSON.stringify(signin.saved), '[[2,"ANONYMOUS",null]]', "saved values");
same(signinFeedback(), "Saving Steam authentication settings", "save start line");
signin.mode = "ANONYMOUS"; signin.account = null;
await finish("save", "SAVE_STEAM_SETTINGS", {}); await wait(80);
same(panel().querySelector(".mods-signin-summary").textContent, "Steam sign-in: anonymous", "line after the save");
check(form().hidden, "the form stayed open after the save");
// Update results are shown under the check header, above the table.
check(byId("mods-update-header").nextElementSibling === byId("mods-feedback")
  && byId("mods-feedback").nextElementSibling.classList.contains("mods-inventory-body"), "feedback place");
const sent = signin.checks;
const first = await pressUpdate(false);
same(feedback(), "Downloading or updating mods", "update start line");
same(signinFeedback(), "", "the update start line was written into the sign-in panel");
// A sign-in problem of an update opens the form; the focus stays where it is.
byId("check-updates-now").focus();
await finish(first, "UPDATE_WORKSHOP_ITEMS", updateResult({download_state: "FAILED",
  items: [item("111", "AUTHENTICATION_FAILED", null, "AUTHENTICATION_FAILED"), item("222", "NOT_ATTEMPTED", null)]}));
check(feedback().includes("Workshop 111: Steam sign-in failed") && feedback().includes("Workshop 222: Not tried"),
  `per-mod list: ${feedback()}`);
check(!form().hidden && byId("mods-signin-change").getAttribute("aria-expanded") === "true"
  && document.activeElement === byId("check-updates-now"), "the sign-in problem did not open the form quietly");
// One finished operation of the page sends one check request, shared with the shell.
same(signin.checks, sent + 1, "check requests after the page's own update");
"""

# Not configured: the form is open with its heading and guidance, and without "Change" and "Cancel"
UNCONFIGURED = HEAD + r"""
signin.mode = null; signin.account = null;
await start("mods"); await wait(60);
same(panel().querySelector("h2").textContent, "Steam sign-in", "heading");
check(!byId("mods-signin-change") && !panel().querySelector(".mods-signin-line") && !form().hidden, "open form");
same(form().firstElementChild.textContent, "Choose how SteamCMD signs in before the first download. "
  + "Use “Steam account” if Steam refuses anonymous downloads for your mods.", "guidance");
same(labels(form()), "Save sign-in settings|Open SteamCMD login", "actions without Cancel");
check(form().querySelector(".mods-access-row").nextElementSibling.textContent.startsWith("Credential safety"),
  "the credential notice does not follow the fields");
same(byId("steam-auth-mode").value, "", "mode");
// An account mode without a name is not a configured sign-in either.
signin.mode = "ACCOUNT";
await window.ServerManMods.open(); await wait(60);
check(!byId("mods-signin-change") && !form().hidden, "an account without a name was shown as configured");
"""

# D11 on Mods, and the review that offers no action: a statement, not a question
REFUSAL = HEAD + r"""
await start("mods"); await wait(60);
const title = () => byId("mod-publication-title").textContent;
const lines = () => [...dialog().querySelectorAll("p")].map((node) => node.textContent);
const plan = (guarded) => () => ok({profile_id: "alpha", publication_fingerprint: "b".repeat(64), key_count: 2,
  missing_key_count: 0, plain_apply_guarded: guarded,
  targets: [{workshop_id: "111", target_relative: "mods\\alpha", current: false}]});
// A review that offers an action keeps its question and its lead sentence.
previewAnswer = plan(true);
await runUpdate(false, updateResult);
same(title(), "Apply downloaded mods and keys?", "title of an offered apply");
same(lines()[0], "Review the server folders that will be updated. Changes use verified rollback protection.", "lead");
dialogButton("Cancel").click();
// A refused apply states what happened, then shows the plan; it asks nothing.
await serverState("RUNNING_MANAGED");
await runUpdate(false, updateResult);
same(title(), "Mods were not applied", "refusal title");
check(!dialog().textContent.includes("?") && !dialog().textContent.includes("will be updated"),
  `the refusal still asks or promises: ${dialog().textContent}`);
same(JSON.stringify(lines()), JSON.stringify(["The server is running. Mods cannot be applied to the server folder now."
  + " Use Update & restart, or stop the server first.", "The reviewed plan was not applied:"]), "refusal sentences");
check(dialog().textContent.includes("Workshop 111: mods\\alpha") && labels(dialog()) === "Close", "plan and Close");
same(dialog().getAttribute("aria-labelledby"), "mod-publication-title", "dialog name");
dialogButton("Close").click();
// Another profile runs: the restart is not offered, with the reason of Overview, and no advice names it.
host.status = {...host.status, state: "RUNNING_MANAGED", profile_id: "bravo"};
await window.ServerManServerState.refresh(); await wait(20);
const restart = byId("update-start");
check(restart.textContent === "Update & restart" && restart.disabled
  && restart.title === "Bravo is running. Select it to stop or restart it.", `restart lock: ${restart.title}`);
check(!byId("update-workshop").disabled, "Update all is locked");
restart.click(); await wait(20);
same(calls.updates.length, 2, "a locked restart was submitted");
await runUpdate(false, updateResult);
same(lines()[0], "The server is running. Mods cannot be applied to the server folder now."
  + " Stop the server first, then update again.", "advice while another profile runs");
dialogButton("Close").click();
// The running profile itself gets the restart back.
host.status = {...host.status, profile_id: "alpha"};
await window.ServerManServerState.refresh(); await wait(20);
check(!byId("update-start").disabled && byId("update-start").title === "", "restart for the running profile");
// A state that changed after a start was requested is also told as a statement.
await runUpdate(true, updateResult);
same(title(), "Apply downloaded mods and keys?", "restart review title");
dialogButton("Cancel").click();
await serverState("STOPPED");
const id = await pressUpdate(true); await serverState("STARTING");
await finish(id, "UPDATE_WORKSHOP_ITEMS", updateResult());
same(title(), "Mods were not applied", "changed-state title");
same(lines()[0], "The server state changed. The mods are downloaded; nothing was applied.", "changed-state sentence");
dialogButton("Close").click();
check(calls.published.length === 0 && calls.restarted.length === 0, "a review without an action submitted");
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ModsSigninDynamicTests(unittest.TestCase):
    """Criterion 13 and decision D6: one sign-in line with "Change"; feedback beside its actions."""

    def test_line_change_form_cancel_and_feedback_regions(self) -> None:
        """Configured sign-in: line, form in place, Cancel, both feedback regions, one check request."""
        self.assertEqual(run_shell_harness(SIGNIN, budget=20000), "PASS")

    def test_form_is_open_while_no_sign_in_is_configured(self) -> None:
        """No sign-in: heading, guidance, open form without Change and Cancel."""
        self.assertEqual(run_shell_harness(UNCONFIGURED, budget=8000), "PASS")

    def test_restart_lock_and_review_without_an_action(self) -> None:
        """D11 on Mods; a refused or outdated review states its result and asks nothing."""
        self.assertEqual(run_shell_harness(REFUSAL, budget=30000), "PASS")


if __name__ == "__main__":
    unittest.main()
