"""Headless Edge checks of the single profile selector: every page follows the sidebar selection."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# Fake host of the profile-scoped pages, with a record of the profile that each call was made for
HOST = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const byId = (id) => document.getElementById(id);
const region = () => byId("content-region");
const button = (label, root = region()) => [...root.querySelectorAll("button")]
  .find((item) => item.textContent === label);
const seen = {backups: [], previews: [], created: [], configurations: [], started: [], archives: 0};
Object.assign(window.pywebview.api, {
  list_backups: async (profileId) => { seen.backups.push(profileId);
    return ok({profile_id: profileId, profile_revision: 3, settings_revision: 2,
      backups: [{backup_id: `${profileId}-b1`, created_at: "2026-10-02T08:00:00Z", entry_count: 4, total_size: 4096,
        restore_compatibility: "COMPATIBLE", restore_compatibility_reason: null}],
      diagnostics: [], legacy_backups: [], destination_kind: "portable",
      runtime_profile: "serverman\\profile"}); },
  inspect_restore_recovery: async () => ok({blocked: false, diagnostics: []}),
  preview_restore: async (profileId, backupId) => { seen.previews.push(profileId);
    return ok({profile_id: profileId, backup_id: backupId, created_at: "2026-10-02T08:00:00Z", profile_revision: 3,
      settings_revision: 2, manifest_digest: "a".repeat(64), fingerprint: "b".repeat(64), replacement_count: 1,
      creation_count: 0, recovery_plan: "Verified recovery copies are kept.",
      targets: [{action: "REPLACE", target_relative: "serverDZ.cfg", target_kind: "DAYZ_CONFIGURATION"}]}); },
  create_backup: async (profileId) => { seen.created.push(profileId);
    return ok({operation_id: "backup", state: "QUEUED"}); },
  load_configuration: async (profileId) => { seen.configurations.push(profileId);
    return ok({profile_id: profileId, target: "server", relative_path: `${profileId}.cfg`, profile_revision: 3,
      settings_revision: 2, digest: "d", server_config_digest: null,
      fields: [{key: "hostname", kind: "string", present: true, value: "Old name", secret: false}]}); },
  start_server: async (profileId) => { seen.started.push(profileId);
    return ok({operation_id: "start", state: "QUEUED"}); },
  list_profile_missions: async () => ok({settings_revision: 2, missions: []}),
  select_backup_archive: async () => { seen.archives += 1; return ok({cancelled: true}); },
  list_mod_inventory: async () => ok([{order: 1, name: "Example Mod", directory: "@Example", launch_scope: "client",
    source_kind: "workshop", workshop_id: "111", version: "1.0", state: "CURRENT", time_updated: 1,
    remote_time_updated: 1, remote_check: "OK", pending_reason: null}]),
});
host.operations.set("start", record("start", "START_SERVER", "QUEUED"));
host.operations.set("backup", record("backup", "CREATE_BACKUP", "QUEUED"));
// Change the selection as the operator does: through the one selector in the sidebar.
const choose = async (profileId) => { const select = byId("global-profile"); select.value = profileId;
  select.dispatchEvent(new Event("change", {bubbles: true})); await wait(80); };
const noPageSelector = (page) => check(!region().querySelector(
  "#overview-profile, #configuration-profile, #backup-profile, #profile-workspace-selector, .overview-profile-bar"),
  `${page}: a page profile selector is still drawn`);
const serverLine = () => byId("page-server").textContent;
"""

# Each profile-scoped page reads the sidebar selection and names the server in its heading
PAGES = HOST + r"""
await start(); await wait(40);
same(document.querySelectorAll("#sidebar select").length, 1, "selectors in the sidebar");
// Overview: no profile bar; the lifecycle request names the selected profile.
noPageSelector("Overview");
check(serverLine().startsWith("Server: Alpha"), `Overview context line: ${serverLine()}`);
await choose("bravo");
check(serverLine().startsWith("Server: Bravo") && byId("global-profile").value === "bravo", "Overview follows");
button("Start server").click(); await wait(10);
button("Start server", byId("lifecycle-confirmation")).click(); await wait(30);
same(JSON.stringify(seen.started), '["bravo"]', "start request profile");
await push(record("start", "START_SERVER", "SUCCEEDED", {progress_phase: "complete", result: {}})); await wait(30);
// Profiles: the heading keeps its title and "New profile"; the form shows the selected profile.
commitSection("profiles"); await wait(60);
noPageSelector("Profiles");
const heading = region().querySelector(".profile-panel > .panel-heading");
check(heading.querySelector("h2").textContent === "Edit profile" && button("New profile", heading)
  && !heading.querySelector("select"), "Profiles heading");
same(region().querySelector('[name="profile_id"]').value, "bravo", "profile form");
await choose("alpha");
same(region().querySelector('[name="profile_id"]').value, "alpha", "profile form after the selection");
button("New profile").click(); await wait(40);
check(region().querySelector(".profile-create-panel") && serverLine() === "New profile", `creation line: ${serverLine()}`);
button("Cancel").click(); await wait(40);
check(serverLine().startsWith("Server: Alpha"), `context line after Cancel: ${serverLine()}`);
// Configuration: loads the selected profile; an unsaved edit asks before the selection changes.
commitSection("configuration"); await wait(60);
noPageSelector("Configuration");
check(seen.configurations.at(-1) === "alpha" && byId("configuration-path").textContent.includes("alpha.cfg"),
  "Configuration target");
await choose("bravo");
check(seen.configurations.at(-1) === "bravo" && byId("configuration-path").textContent.includes("bravo.cfg"),
  "Configuration did not follow the selection");
const field = region().querySelector("[data-configuration-field]");
field.value = "New name"; field.dispatchEvent(new Event("input", {bubbles: true}));
const loads = seen.configurations.length;
await choose("alpha");
const guard = document.querySelector(".unsaved-guard");
check(guard && guard.textContent.includes("Discard unsaved changes to switch server profiles."), "guard did not ask");
button("Stay", guard).click(); await wait(40);
check(byId("global-profile").value === "bravo" && seen.configurations.length === loads
  && region().querySelector("[data-configuration-field]").value === "New name", "Stay changed the page");
await choose("alpha");
button("Discard changes", document.querySelector(".unsaved-guard")).click(); await wait(80);
check(byId("global-profile").value === "alpha" && seen.configurations.at(-1) === "alpha"
  && region().querySelector("[data-configuration-field]").value === "Old name", "Discard did not switch");
// Backups: history of the selection; a profile change closes an open restore review.
commitSection("backups"); await wait(60);
noPageSelector("Backups");
same(seen.backups.at(-1), "alpha", "history profile");
region().querySelector(".backup-restore-action").click(); await wait(40);
check(!byId("restore-panel").hidden && button("Restore this backup") && seen.previews.at(-1) === "alpha",
  "the restore review did not open");
await choose("bravo");
check(seen.backups.at(-1) === "bravo" && byId("restore-panel").hidden && !button("Restore this backup")
  && restoreState.preview === null && byId("global-profile").value === "bravo",
  "a profile change did not close the restore review");
check(region().querySelector(".backup-item strong").textContent === "Bravo backup", "history did not follow");
// The confirmation is modal: the sidebar cannot change the profile behind it, and the request names it.
byId("backup-create").click(); await wait(10);
const confirmation = byId("backup-confirmation");
check(confirmation.textContent.includes("Server: Bravo."), "confirmation names the server");
check(byId("global-profile").closest("[inert]"), "the sidebar is reachable behind the confirmation");
button("Create backup", confirmation).click(); await wait(30);
same(JSON.stringify(seen.created), '["bravo"]', "backup request profile");
await push(record("backup", "CREATE_BACKUP", "SUCCEEDED", {progress_phase: "complete", result: {}})); await wait(30);
// Mods: the count names no server; the heading line does.
commitSection("mods"); await wait(80);
same(region().querySelector(".mods-count").textContent, "1 mod", "Mods count");
check(serverLine().startsWith("Server: Bravo"), "Mods context line");
"""

# Without a profile: restore from a backup archive works and the first profile stays reachable
ZERO = HOST + r"""
profiles.length = 0;
await start(); await wait(40);
noPageSelector("Overview");
const notice = region().querySelector(".notice");
check(notice.querySelector("h2").textContent === "Create a server profile", "no-profile notice");
check(button("Create profile", notice) && button("Restore profile from backup…", notice), "notice actions");
check(button("Start server").disabled, "Start is offered without a profile");
same(byId("page-server").textContent, "No server profile yet", "context line");
// The way to the ZIP restore: notice button, then the catalog action on Backups.
button("Restore profile from backup…", notice).click(); await wait(60);
same(shellState.section, "backups", "notice opened Backups");
noPageSelector("Backups");
check(byId("backup-history").textContent === "Create or restore a profile to make new backups."
  && byId("backup-history").getAttribute("aria-busy") === "false" && byId("backup-create").disabled,
  "Backups without a profile");
same(seen.backups.length, 0, "history reads without a profile");
const browse = button("Restore profile from backup…", byId("backup-catalog"));
check(browse && !browse.disabled && !browse.hasAttribute("aria-disabled"), "ZIP restore is not offered");
browse.click(); await wait(30);
same(seen.archives, 1, "archive selection requests");
// The way to the first profile: notice button and the sidebar button both open the creation form.
commitSection("overview"); await wait(60);
button("Create profile", region().querySelector(".notice")).click(); await wait(60);
check(shellState.section === "profiles" && region().querySelector(".profile-create-panel"),
  "the notice did not open the creation form");
same(byId("page-server").textContent, "New profile", "creation context line");
commitSection("configuration"); await wait(40);
check(region().textContent.includes("No configuration available"), "Configuration empty state");
byId("sidebar-create-profile").click(); await wait(60);
check(shellState.section === "profiles" && region().querySelector(".profile-create-panel"),
  "the sidebar did not open the creation form");
commitSection("mods"); await wait(60);
check(!region().querySelector(".notice-error"), "Mods failed without a profile");
// A restored or created profile ends the empty state everywhere.
profiles.push({profile_id: "alpha", display_name: "Alpha", revision: 1, semantic_digest: "a".repeat(64),
  runtime_profile: "serverman\\alpha\\profile", ...profileFields});
await window.ServerManProfileContext.refreshAndSelect("alpha"); await wait(60);
check(!byId("global-profile").disabled && byId("sidebar-create-profile").hidden
  && byId("page-server").textContent.startsWith("Server: Alpha"), "the first profile did not end the empty state");
"""


# QF-035: a press on the old "Restore this backup" between a sidebar switch and the page rebuild does nothing
STALE = HOST + r"""
const applied = [];
window.pywebview.api.apply_restore = async (profileId) => { applied.push(profileId);
  return ok({operation_id: "restore", state: "QUEUED"}); };
await start("backups"); await wait(100);
region().querySelector(".backup-restore-action").click(); await wait(60);
same(seen.previews.at(-1), "alpha", "review profile");
// A slow catalog widens the time between the selection change and the rebuilt page.
const realList = window.pywebview.api.list_profiles;
window.pywebview.api.list_profiles = async () => { await wait(300); return realList(); };
const oldApply = button("Restore this backup");
const select = byId("global-profile"); select.value = "bravo"; select.dispatchEvent(new Event("change", {bubbles: true}));
await wait(5); oldApply.click(); await wait(10);
check(!byId("restore-confirmation"), "a stale press opened the restore confirmation");
// A confirmation that would still open is pressed, so a stale request would be sent here.
const confirm = byId("restore-confirmation");
if (confirm) { button("Restore now", confirm).click(); await wait(20); }
await wait(400);
same(JSON.stringify(applied), "[]", "restore requests after a stale press");
check(byId("global-profile").value === "bravo" && restoreState.preview === null, "the review stayed for the old profile");
window.pywebview.api.list_profiles = realList;
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class SingleProfileSelectorDynamicTests(unittest.TestCase):
    """Criterion 11: one selector in the sidebar; the pages follow it."""

    def test_pages_follow_the_sidebar_selection_without_their_own_selector(self) -> None:
        """Overview, Profiles, Configuration, Backups and Mods read the shared selection."""
        self.assertEqual(run_shell_harness(PAGES, budget=12000), "PASS")

    def test_stale_restore_press_after_a_sidebar_switch_sends_nothing(self) -> None:
        """QF-035: the review of the old profile cannot be confirmed after the selection changed."""
        self.assertEqual(run_shell_harness(STALE, budget=10000), "PASS")

    def test_zip_restore_and_first_profile_stay_reachable_without_profiles(self) -> None:
        """With no profile the ZIP restore works and the creation form is one press away."""
        self.assertEqual(run_shell_harness(ZERO, budget=10000), "PASS")


if __name__ == "__main__":
    unittest.main()
