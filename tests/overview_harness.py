"""Shared fake host of the Overview dynamic tests: snapshot parts, update state, backup history, and the schedule."""
from __future__ import annotations

# Fake answers of the Overview page: snapshot parts, update state, backup history, and the schedule
OVERVIEW_HEAD = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const byId = (id) => document.getElementById(id);
const region = () => byId("content-region");
const button = (label, root = region()) => [...root.querySelectorAll("button")]
  .find((item) => item.textContent === label);
const tick = async () => { await pollEvents(); window.clearTimeout(shellState.pollTimer); await wait(10); };
const page = {block: null, root: "D:\\Server", backups: [], runtime: "serverman\\alpha\\profile", backupReads: 0,
  backupAnswer: null, schedule: {action: "restart", hour: 4, minute: 0, last_status: "QUEUED"}, saved: [],
  automatic: true, requests: [], scheduleFault: null, saveFault: null};
const updates = (extra = {}, state = "OK", checking = false) => ({mods: {check_state: state, checked_at: null,
  last_success_at: state === "NEVER" ? null : "2026-10-04T08:41:07.120Z", error_code: null, update_count: 0,
  pending_apply_count: 0, ...extra}, server_build: null, checking, revision: 1});
const scheduleView = (profileId) => ({profile_id: profileId, enabled: Boolean(page.schedule.action), ...page.schedule,
  next_run_local: page.schedule.action ? "2026-10-05T04:00" : null});
Object.assign(window.pywebview.api, {
  get_application_snapshot: async () => { const value = snapshot(host.snapshotOperations);
    value.mutation_block = page.block; value.settings.dayz_root = page.root; return ok(value); },
  get_ui_preferences: async () => ok({selected_profile_id: "alpha", backup_after_stop_profiles: [],
    automatic_update_checks: page.automatic}),
  request_update_check: async (scope, force) => { page.requests.push(force);
    return ok({accepted: force, checking: false}); },
  list_backups: async (profileId) => { page.backupReads += 1;
    if (page.backupAnswer) return page.backupAnswer;
    return ok({profile_id: profileId, profile_revision: 3, settings_revision: 2, backups: page.backups,
      diagnostics: [], legacy_backups: [], destination_kind: "portable", runtime_profile: page.runtime}); },
  // A failed or thrown schedule call must keep the page (QF-056).
  get_lifecycle_schedule: async (profileId) => { if (page.scheduleFault === "throw") throw new Error("bridge");
    return page.scheduleFault ? fail("STORAGE_FAILURE", "x") : ok(scheduleView(profileId)); },
  save_lifecycle_schedule: async (profileId, hour, minute, action) => {
    if (page.saveFault === "throw") throw new Error("bridge");
    if (page.saveFault) return fail("STORAGE_FAILURE", "x");
    page.saved.push([profileId, hour, minute, action]);
    page.schedule = {...page.schedule, hour, minute, action}; return ok(scheduleView(profileId)); },
});
const running = (extra = {}) => ({state: "RUNNING_MANAGED", readiness: "READY", process_id: 18244,
  diagnostic_code: null, query_port: 27016, profile_id: "alpha",
  started_at: new Date(Date.now() - (3 * 60 + 12) * 60000).toISOString(), ...extra});
const control = (label) => button(label, region().querySelector(".overview-controls"));
const toggle = () => byId("overview-process-toggle");
const reasons = () => ["Start server", "Save & Stop", "Save & Restart"].map((label) =>
  control(label).disabled ? control(label).title : "on").join("|");
const reopen = async () => { commitSection("logs"); await wait(10); commitSection("overview"); await wait(80); };
"""
