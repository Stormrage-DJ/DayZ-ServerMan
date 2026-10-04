"""Shared harness parts for the dynamic tests of the Mods update actions and row marks."""
from __future__ import annotations

# Fake host of the Mods page: two Workshop rows and one local row, recorded submits, a settable preview
MODS_HOST = r"""
const calls = {updates: [], published: [], restarted: [], previews: 0};
let previewAnswer = () => ok({profile_id: "alpha", publication_fingerprint: "b".repeat(64), key_count: 2,
  missing_key_count: 0, plain_apply_guarded: true,
  targets: [{workshop_id: "111", target_relative: "mods\\alpha", current: false}]});
const modRow = (order, id, state, kind = "workshop") => ({order, name: `Mod ${order}`, directory: `mods\\m${order}`,
  launch_scope: "client", source_kind: kind, workshop_id: id, version: "1.0", state, time_updated: 1,
  remote_time_updated: 1, remote_check: "OK", pending_reason: null});
const queued = (id, kind) => { host.operations.set(id, record(id, kind, "QUEUED",
  {target_profile_id: "alpha", cancellable: true})); return ok({operation_id: id, state: "QUEUED"}); };
Object.assign(window.pywebview.api, {
  get_update_status: async () => ok({mods: {check_state: "OK", checked_at: null,
    last_success_at: "2026-10-03T13:41:07.120Z", error_code: null, update_count: 1, pending_apply_count: 0},
    server_build: null, checking: false, revision: 1}),
  request_update_check: async () => ok({accepted: false, checking: false}),
  list_mod_inventory: async () => ok([modRow(1, "111", "UPDATE_AVAILABLE"), modRow(2, "222", "CURRENT"),
    modRow(3, null, "LOCAL", "external")]),
  save_backup_after_stop: async (id, enabled) => ok({profile_id: id, backup_after_stop: enabled}),
  update_workshop_items: async (...args) => { calls.updates.push(args.at(-1));
    return queued(`update-${calls.updates.length}`, "UPDATE_WORKSHOP_ITEMS"); },
  preview_mod_publication: async () => { calls.previews += 1; return previewAnswer(); },
  publish_mods_and_keys: async (...args) => { calls.published.push(args);
    return queued(`publish-${calls.published.length}`, "PUBLISH_MODS_AND_KEYS"); },
  apply_mods_and_restart: async (...args) => { calls.restarted.push(args);
    return queued(`restart-${calls.restarted.length}`, "APPLY_MODS_AND_RESTART"); },
});
const byId = (id) => document.getElementById(id);
const feedback = () => byId("mods-feedback").textContent;
const dialog = () => byId("mod-publication-confirmation");
const dialogButton = (label) => [...dialog().querySelectorAll("button")].find((button) => button.textContent === label);
const statusCells = () => [...document.querySelectorAll(".mods-table tbody .mods-status")];
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
// Set the server state and let the shell read it, as a poll tick does in every section.
const serverState = async (state) => { host.status = {...host.status, state};
  await window.ServerManServerState.refresh(); await wait(10); };
// One item of an update result with its outcome and proof kind.
const item = (id, outcome, proof, error = null) => ({item: {workshop_id: id}, outcome, error_code: error,
  cache_proof: proof ? {verification_kind: proof} : null});
// Result of an update: by default one updated and one current mod after a SteamCMD run.
const updateResult = (extra = {}) => ({profile_id: "alpha", profile_revision: 3,
  semantic_profile_digest: "a".repeat(64), settings_revision: 2, download_state: "VERIFIED",
  start_requested: calls.updates.at(-1) === true, process_id: 77, steamcmd_exit_code: 0, steamcmd_summary: null,
  items: [item("111", "UPDATED_VERIFIED", "FULL_CONTENT"), item("222", "VERIFIED_CURRENT", "APPLIED_STATE")],
  ...extra});
// Result of an update that found everything current: no process, every mod already applied.
const currentResult = (extra = {}) => updateResult({process_id: null, steamcmd_exit_code: null,
  items: [item("111", "VERIFIED_CURRENT", "APPLIED_STATE"), item("222", "VERIFIED_CURRENT", "APPLIED_STATE")],
  ...extra});
// Press one of the two update buttons and return the identifier of the queued operation.
const pressUpdate = async (start) => { byId(start ? "update-start" : "update-workshop").click(); await wait(10);
  return `update-${calls.updates.length}`; };
// End an operation of the page with a state, a result, and further record fields.
const finish = async (id, kind, result, state = "SUCCEEDED", extra = {}) => { await push(record(id, kind, state,
  {target_profile_id: "alpha", progress_phase: "complete", result, ...extra})); await wait(30); };
// Run one update from the button to its result.
const runUpdate = async (start, result) => { const id = await pressUpdate(start);
  await finish(id, "UPDATE_WORKSHOP_ITEMS", typeof result === "function" ? result() : result); };
"""
