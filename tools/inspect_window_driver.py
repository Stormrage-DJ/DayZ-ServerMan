"""The page driver of tools/check_inspect_window.py: the 4.6 steps, run inside the real editor window.

The script wraps the three Inspect calls of the host API and records each call and its answer. Then it
runs the steps one after another with the page's own controls and events. window.__check holds the record; the
Python side polls it. Each step records what the page showed and whether it matches the backend answer.
"""

from __future__ import annotations

# Runs once in the composed editor document after the saved plan with zones A and B has loaded
DRIVER_SCRIPT = r'''(() => {
  const record = {steps: [], calls: [], done: false, error: null}, api = window.pywebview.api;
  window.__check = record;
  // Record each Inspect call with a short summary of its answer; capacity lists stay counts. A continue or
  // cancel names only its job, so the target of each job comes from the start that created it.
  const jobs = new Map();
  for (const name of ["inspect", "continue_inspection", "cancel_inspection"]) {
    const original = api[name];
    api[name] = async (...args) => {
      const begun = performance.now(), entry = {method: name, at: begun / 1000,
        target: name === "inspect" ? args[1] : jobs.get(args[0]) ?? null, draft: name === "inspect" ? args[0] !== null : null,
        job: name === "inspect" ? null : args[0]};
      record.calls.push(entry);
      const answer = await original(...args), value = answer?.value ?? {}, result = value.result ?? null;
      Object.assign(entry, {seconds: (performance.now() - begun) / 1000, success: answer?.success ?? null,
        status: value.status ?? null, phase: value.job?.phase ?? null, id: value.job?.id ?? null,
        rows: value.job ? [value.job.rows_done, value.job.rows_total] : null, reason: answer?.error?.details?.reason ?? null,
        result: result && {effective: result.effective_tiers ?? result.effective_tier ?? null,
          original: result.original_tiers ?? result.original_tier ?? null, capacity: Array.isArray(result.capacity) ? result.capacity.length : null,
          winner: result.winner?.name ?? null, capacity_reason: result.capacity_reason ?? null}});
      if (value.job?.id && entry.target) jobs.set(value.job.id, entry.target);
      return answer;
    };
  }
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), element = (id) => document.getElementById(id);
  const text = (id) => element(id).textContent, line = () => text("map-tier-effective"), words = window.ServerManMapInspectWording;
  const reading = () => line().includes("Reading tiers");
  const pick = (id) => document.querySelector(`[data-map-control="${id}:select"]`).click();
  const lastDone = (key, value) => [...record.calls].reverse().find((call) => call.status === "done" && call.target?.[key] === value);
  // Wait for a condition; keep each distinct tier line text with the seconds after the wait began when it first showed
  const until = async (test, seen = new Map(), ms = 60000) => {
    const start = performance.now();
    while (performance.now() - start < ms) {
      if (!seen.has(line())) seen.set(line(), (performance.now() - start) / 1000);
      if (test()) return true;
      await sleep(20);
    }
    return false;
  };
  const step = async (name, run) => {
    const start = performance.now(), entry = {name, passed: false, detail: {}};
    record.steps.push(entry);
    try { entry.passed = Boolean(await run(entry.detail)); } catch (error) { entry.detail.error = String(error?.stack ?? error); }
    entry.seconds = (performance.now() - start) / 1000;
  };
  // The tier line matches the last done answer of the selected object
  const matches = (id) => line() === words.effective(lastDone("object", id)?.result?.effective ?? ["missing"]);
  const clickAt = (x, z) => {
    const region = element("map-viewport"), point = window.ServerManMapViewportUi.view().toScreen({x, z});
    const box = region.getBoundingClientRect(), place = {clientX: box.left + region.clientLeft + point.x,
      clientY: box.top + region.clientTop + point.y, button: 0, bubbles: true, cancelable: true};
    region.dispatchEvent(new PointerEvent("pointerdown", {...place, pointerId: 1, buttons: 1}));
    region.dispatchEvent(new PointerEvent("pointerup", {...place, pointerId: 1, buttons: 0}));
    region.dispatchEvent(new MouseEvent("click", place));
  };
  const rows = () => Object.fromEntries([...document.querySelectorAll("#map-inspect-result > div")].map((row) =>
    [row.children[0].textContent, row.children[1].textContent]));
  (async () => {
    const plan = mapEditorState.model.snapshot(), centre = plan.objects[0].centre;
    await step("tier_read", async (detail) => {
      const seen = new Map();
      pick("A");
      const ended = await until(() => !reading(), seen);
      // T3: a percent shows only after about 1 s of the read; 0.9 s allows for the 20 ms polling
      const early = [...seen].filter(([shown, seconds]) => shown.includes("%") && seconds < 0.9);
      Object.assign(detail, {lines: [...seen], early_percent: early, busy: element("map-tier-read").getAttribute("aria-busy"), line: line(),
        original: element("map-tier-original").hidden ? null : text("map-tier-original"),
        capacity: [...document.querySelectorAll("#map-tier-capacity-list li")].slice(0, 3).map((item) => item.textContent),
        capacity_count: document.querySelectorAll("#map-tier-capacity-list li").length});
      return ended && seen.has("Effective tier: Reading tiers") && !early.length && matches("A") && detail.busy === "false" &&
        detail.capacity_count > 0;
    });
    await step("tier_choice", async (detail) => {
      const select = element("map-tier-select"), before = mapEditorState.model.status().history.length;
      const other = [...select.options].find((option) => option.value && !option.disabled && option.value !== select.value);
      const calls = record.calls.length;
      select.value = other.value;
      select.dispatchEvent(new Event("change"));
      const ended = await until(() => !reading());
      const started = record.calls.slice(calls).find((call) => call.method === "inspect");
      Object.assign(detail, {tier: other.value, entries: mapEditorState.model.status().history.length - before,
        draft_sent: started?.draft ?? null, line: line(), select_enabled: !select.disabled});
      return ended && detail.entries === 1 && started?.target?.object === "A" && matches("A");
    });
    await step("selection_change_mid_read", async (detail) => {
      const calls = record.calls.length;
      pick("B");
      await sleep(30);
      pick("A");
      const ended = await until(() => !reading());
      await sleep(1500);
      const later = record.calls.slice(calls);
      Object.assign(detail, {selected: window.ServerManMapLayers.selected(), line: line(),
        calls: later.map((call) => [call.method, call.target?.object ?? call.job, call.status ?? call.reason])});
      return ended && detail.selected === "A" && matches("A") && later.some((call) => call.target?.object === "B");
    });
    await step("point_inspect", async (detail) => {
      window.ServerManMapTools.choose("inspect");
      clickAt(centre.x, centre.z);
      const ended = await until(() => !element("map-inspect-result").hidden && text("map-inspect-status") === "");
      Object.assign(detail, {rows: rows(), sentence: text("map-inspect-capacity"), announced: text("map-announcement"),
        marker: Boolean(document.querySelector("#map-inspect-marker"))});
      return ended && detail.rows["Winning zone"]?.startsWith("Zone B") && detail.sentence === words.pointCapacity && detail.marker;
    });
    await step("winner_delete", async (detail) => {
      pick("B");
      element("map-properties-delete").click();
      const ended = await until(() => rows()["Winning zone"]?.startsWith("Zone A") && text("map-inspect-status") === "");
      Object.assign(detail, {rows: rows(), objects: mapEditorState.model.snapshot().objects.map((item) => item.name)});
      return ended && !detail.objects.includes("Zone B");
    });
    await step("escape", async (detail) => {
      const region = element("map-viewport");
      region.focus();
      region.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
      await sleep(100);
      Object.assign(detail, {marker: Boolean(document.querySelector("#map-inspect-marker")), selected: window.ServerManMapLayers.selected(),
        status: text("map-inspect-status")});
      return !detail.marker && detail.selected === null && detail.status === words.inspectIdle;
    });
    await step("close_during_read", async (detail) => {
      // The autosave of the delete ends first, so the close needs no dialog
      await until(() => !mapEditorState.model.status().dirty && !window.ServerManMapPlanSave.busy(), new Map(), 20000);
      const calls = record.calls.length;
      pick("A");
      await until(() => record.calls.slice(calls).some((call) => call.status === "running"), new Map(), 10000);
      const settledAt = performance.now() / 1000, answer = await window.ServerManMapEditor.settleForClose("editor");
      await sleep(3000);
      const later = record.calls.slice(calls);
      Object.assign(detail, {answer, calls: later.map((call) => [call.method, call.job ?? call.target?.object, call.status ?? call.reason]),
        line_hidden: element("map-tier-read").hidden,
        started_after_close: later.filter((call) => call.at > settledAt + 0.05 && call.method !== "cancel_inspection").length});
      return answer === "close" && detail.started_after_close === 0 && later.some((call) => call.method === "cancel_inspection");
    });
    record.done = true;
  })().catch((error) => { record.error = String(error?.stack ?? error); });
  return true;
})()'''
