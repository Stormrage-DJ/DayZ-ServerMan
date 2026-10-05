// "Next scheduled action" row of the Overview status board: the daily schedule at a glance, and its editor as a
// row under it that "Change schedule" opens.
"use strict";

// Create a UI element through the shared interface helper.
function scheduleNode(tag, className = "", text = "") {
  return window.ServerManUi.element(tag, className, text);
}

// Build a labeled numeric time field with the given bounds.
function scheduleTimeField(labelText, minimum, maximum) {
  const label = scheduleNode("label", "schedule-time-field");
  label.append(scheduleNode("span", "", labelText));
  // Constrain the input to whole values inside the allowed range.
  const input = document.createElement("input");
  input.type = "number";
  input.min = String(minimum);
  input.max = String(maximum);
  input.step = "1";
  input.required = true;
  label.append(input);
  return { label, input };
}

// Build one labeled checkbox for a scheduled action.
function scheduleChoice(text) {
  const label = scheduleNode("label", "check-row schedule-choice");
  const input = document.createElement("input");
  input.type = "checkbox";
  label.append(input, scheduleNode("span", "", text));
  return { label, input };
}

// Name the scheduled action and its time in one line, or say that none is set.
function scheduleSummaryText(schedule) {
  const action = { stop: "Save & Stop", restart: "Save & Restart" }[schedule.action];
  if (!action) return "No scheduled action";
  const time = `${String(schedule.hour).padStart(2, "0")}:${String(schedule.minute).padStart(2, "0")}`;
  return `${action} daily at ${time}`;
}

// Describe the next run and the result of the last run for the status line.
function scheduleStatusText(schedule) {
  // Summarize the next scheduled run in local time.
  const next = schedule.next_run_local
    ? `Next: ${schedule.next_run_local.replace("T", " ")} local time.`
    : "No timed action is enabled.";
  // Map the last host status to a short explanation.
  const last = {
    QUEUED: " Last run was queued.",
    SKIPPED_NOT_RUNNING: " Last run was skipped because this server was not running under the manager.",
    QUEUE_FAILED: " Last run could not be queued.",
    CLAIMED: " Last run is being prepared.",
  }[schedule.last_status] || "";
  return `${next}${last}`;
}

// Tone of the summary dot: none set is neutral, a set schedule is green, a run that could not be queued is amber.
function scheduleTone(schedule) {
  if (!schedule.action) return "neutral";
  return schedule.last_status === "QUEUE_FAILED" ? "warning" : "normal";
}

// Call the host; a thrown bridge call counts as a failed answer, so the page stays (QF-056).
async function scheduleCall(call) {
  try { return await call(); } catch (_error) { return null; }
}

// Build the schedule editor row: hour, minute, the two actions, "Cancel" and "Save schedule".
function scheduleEditor() {
  const row = scheduleNode("li", "overview-editor"); row.id = "overview-schedule-editor"; row.hidden = true;
  // Offer hour and minute inputs for the daily run time.
  const fields = scheduleNode("div", "schedule-time");
  const hour = scheduleTimeField("Hour", 0, 23);
  const minute = scheduleTimeField("Minute", 0, 59);
  fields.append(hour.label, minute.label);
  // Offer the save-and-stop and save-and-restart actions.
  const choices = scheduleNode("fieldset", "schedule-choices");
  const stop = scheduleChoice("Save & Stop");
  const restart = scheduleChoice("Save & Restart");
  choices.append(scheduleNode("legend", "sr-only", "Scheduled action"), stop.label, restart.label);
  const cancel = scheduleNode("button", "button button-compact", "Cancel"); cancel.type = "button";
  const save = scheduleNode("button", "button button-compact button-primary", "Save schedule"); save.type = "button";
  const actions = scheduleNode("div", "overview-editor-actions");
  actions.append(cancel, save);
  row.append(fields, choices, actions);
  // Keep only one scheduled action selected.
  const selectOnly = (selected, other) => { if (selected.checked) other.checked = false; };
  stop.input.addEventListener("change", () => selectOnly(stop.input, restart.input));
  restart.input.addEventListener("change", () => selectOnly(restart.input, stop.input));
  return { row, hour: hour.input, minute: minute.input, stop: stop.input, restart: restart.input, cancel, save };
}

// Build the schedule row and its editor row, and wire their load, validation, and save behavior.
function createScheduleControl(profile) {
  const { row: card, body, action } = window.ServerManOverviewCards.row("overview-schedule", "schedule",
    "Next scheduled action");
  card.dataset.profileId = profile?.profile_id || "";
  const summary = scheduleNode("p", "status-label overview-status schedule-summary status-neutral", "Checking…");
  // Announce the next run, the last run, and every problem politely.
  const status = scheduleNode("p", "overview-meta schedule-status", "Loading schedule…");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  // Hour and minute are local time; the note says so also before a schedule exists (QF-031).
  const note = scheduleNode("p", "overview-meta overview-meta-subtle", "Runs at local time, only while DayZ-ServerMan is open.");
  const metas = scheduleNode("div", "overview-metas");
  metas.append(status, note);
  body.append(summary, metas);
  // The editor stays closed until the operator asks for it; a problem opens it.
  const editor = scheduleEditor();
  const toggle = scheduleNode("button", "button button-compact schedule-toggle", "Set schedule");
  toggle.type = "button"; toggle.id = "overview-schedule-toggle";
  toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-controls", editor.row.id);
  action.append(toggle);
  let stored = null;
  // Enable or disable every interactive control at once.
  const setDisabled = (disabled) => {
    [editor.hour, editor.minute, editor.stop, editor.restart, editor.save, toggle]
      .forEach((control) => { control.disabled = disabled; });
  };
  // Open or close the editor row; closing puts the stored values back.
  const setOpen = (open) => {
    editor.row.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (!open && stored) mirror(stored);
  };
  // Mirror a stored schedule into the editor controls.
  const mirror = (schedule) => {
    editor.hour.value = String(schedule.hour);
    editor.minute.value = String(schedule.minute).padStart(2, "0");
    editor.stop.checked = schedule.action === "stop";
    editor.restart.checked = schedule.action === "restart";
  };
  setDisabled(true);
  toggle.addEventListener("click", () => {
    setOpen(editor.row.hidden);
    if (!editor.row.hidden) editor.hour.focus();
  });
  // Cancel drops the edits and any save or validation problem: the row shows the stored schedule again and the
  // application status no longer warns, because nothing is pending (QF-060).
  editor.cancel.addEventListener("click", () => {
    setOpen(false);
    if (stored) status.textContent = scheduleStatusText(stored);
    window.ServerManUi.clearHostStatus("schedule");
    toggle.focus();
  });

  // Show a stored schedule in the row while it is still current.
  const render = (schedule) => {
    if (!card.isConnected || card.dataset.profileId !== schedule.profile_id) return;
    stored = schedule;
    setOverviewCardText(summary, scheduleSummaryText(schedule),
      `status-label overview-status schedule-summary status-${scheduleTone(schedule)}`);
    toggle.textContent = schedule.action ? "Change schedule" : "Set schedule";
    mirror(schedule);
    status.textContent = scheduleStatusText(schedule);
    setDisabled(false);
  };
  // A failed host call keeps the page: the row says what failed, the application status names it once.
  const reportFailure = (text, hostText) => {
    status.textContent = text;
    window.ServerManUi.setHostStatus(hostText, "is-warning", "schedule");
  };
  // Load the stored schedule from the host service.
  const load = async () => {
    if (!profile) {
      summary.textContent = "No scheduled action";
      status.textContent = "Create a server profile before setting a schedule.";
      return;
    }
    const result = await scheduleCall(() => window.pywebview.api.get_lifecycle_schedule(profile.profile_id));
    if (!card.isConnected) return;
    if (!result?.success) {
      // The stored schedule is not known, so the head does not claim that none is set.
      setOverviewCardText(summary, "Schedule not known", "status-label overview-status schedule-summary status-error");
      return reportFailure("The schedule could not be loaded.", "Schedule could not be loaded");
    }
    window.ServerManUi.clearHostStatus("schedule");
    render(result.value);
  };
  // Validate the entered time and save the chosen schedule action.
  editor.save.addEventListener("click", async () => {
    // Refuse invalid time values before saving; the editor stays open with the problem.
    if (!editor.hour.checkValidity() || !editor.minute.checkValidity()) {
      status.textContent = "Enter an hour from 0–23 and a minute from 0–59.";
      setOpen(true);
      editor.hour.reportValidity();
      return;
    }
    setDisabled(true);
    status.textContent = "Saving schedule…";
    const chosen = editor.restart.checked ? "restart" : editor.stop.checked ? "stop" : null;
    const result = await scheduleCall(() => window.pywebview.api.save_lifecycle_schedule(
      profile.profile_id, Number(editor.hour.value), Number(editor.minute.value), chosen));
    if (!result?.success) {
      setDisabled(false);
      setOpen(true);
      return reportFailure("The schedule could not be saved.", "Schedule could not be saved");
    }
    window.ServerManUi.clearHostStatus("schedule");
    render(result.value);
    setOpen(false);
    toggle.focus();
  });
  // Start loading the stored schedule as the row is created.
  load();
  return [card, editor.row];
}

// Publish the schedule row factory for the overview workspace.
window.ServerManOverviewSchedule = Object.freeze({ create: createScheduleControl });
