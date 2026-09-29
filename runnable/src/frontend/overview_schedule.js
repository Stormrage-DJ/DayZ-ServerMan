// Daily lifecycle schedule controls for the overview workspace.
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

// Describe the next run and the result of the last run for the status line.
function scheduleStatusText(schedule) {
  // Summarize the next scheduled run in local time.
  const next = schedule.next_run_local
    ? `Next: ${schedule.next_run_local.replace("T", " ")} local time.`
    : "No timed action is enabled.";
  // Map the last host status to a short explanation.
  const last = {
    QUEUED: " Last run was queued.",
    SKIPPED_NOT_RUNNING: " Last run was skipped because the server was not managed and running.",
    QUEUE_FAILED: " Last run could not be queued.",
    CLAIMED: " Last run is being prepared.",
  }[schedule.last_status] || "";
  return `${next}${last}`;
}

// Build the schedule row and wire its load, validation, and save behavior.
function createScheduleControl(profile) {
  const row = scheduleNode("section", "server-schedule-row");
  row.dataset.profileId = profile?.profile_id || "";
  const copy = scheduleNode("div", "schedule-copy");
  copy.append(
    scheduleNode("strong", "", "Daily schedule"),
    scheduleNode("small", "", "Runs at local time while DayZ-ServerMan is open."),
  );
  // Offer hour and minute inputs for the daily run time.
  const fields = scheduleNode("div", "schedule-time");
  const hour = scheduleTimeField("Hour", 0, 23);
  const minute = scheduleTimeField("Minute", 0, 59);
  fields.append(hour.label, minute.label);
  // Offer the save-and-stop and save-and-restart actions.
  const choices = scheduleNode("fieldset", "schedule-choices");
  const legend = scheduleNode("legend", "sr-only", "Scheduled action");
  const stop = scheduleChoice("Save & Stop");
  const restart = scheduleChoice("Save & Restart");
  choices.append(legend, stop.label, restart.label);
  // Provide the save control and an announced status line.
  const save = scheduleNode("button", "button", "Save schedule");
  save.type = "button";
  const status = scheduleNode("small", "schedule-status", "Loading schedule…");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  // Enable or disable every interactive control at once.
  const setDisabled = (disabled) => {
    [hour.input, minute.input, stop.input, restart.input, save]
      .forEach((control) => { control.disabled = disabled; });
  };
  // Keep only one scheduled action selected.
  const selectOnly = (selected, other) => {
    if (selected.checked) other.checked = false;
  };
  stop.input.addEventListener("change", () => selectOnly(stop.input, restart.input));
  restart.input.addEventListener("change", () => selectOnly(restart.input, stop.input));
  setDisabled(true);
  row.append(copy, fields, choices, save, status);

  // Show a stored schedule in the row while it is still current.
  const render = (schedule) => {
    if (!row.isConnected || row.dataset.profileId !== schedule.profile_id) return;
    // Mirror the stored schedule into the controls.
    hour.input.value = String(schedule.hour);
    minute.input.value = String(schedule.minute).padStart(2, "0");
    stop.input.checked = schedule.action === "stop";
    restart.input.checked = schedule.action === "restart";
    status.textContent = scheduleStatusText(schedule);
    setDisabled(false);
  };
  // Load the stored schedule from the host service.
  const load = async () => {
    if (!profile) {
      status.textContent = "Create a server profile before setting a schedule.";
      return;
    }
    const result = await window.pywebview.api.get_lifecycle_schedule(profile.profile_id);
    if (!result.success) {
      status.textContent = "The schedule could not be loaded.";
      window.ServerManUi.renderHostError(result);
      return;
    }
    render(result.value);
  };
  // Validate the entered time and save the chosen schedule action.
  save.addEventListener("click", async () => {
    // Refuse invalid time values before saving.
    if (!hour.input.checkValidity() || !minute.input.checkValidity()) {
      status.textContent = "Enter an hour from 0–23 and a minute from 0–59.";
      hour.input.reportValidity();
      return;
    }
    setDisabled(true);
    status.textContent = "Saving schedule…";
    const action = restart.input.checked ? "restart" : stop.input.checked ? "stop" : null;
    const result = await window.pywebview.api.save_lifecycle_schedule(
      profile.profile_id, Number(hour.input.value), Number(minute.input.value), action,
    );
    if (!result.success) {
      status.textContent = "The schedule could not be saved.";
      setDisabled(false);
      window.ServerManUi.renderHostError(result);
      return;
    }
    render(result.value);
  });
  // Start loading the stored schedule as the row is created.
  load();
  return row;
}

// Publish the schedule control factory for the overview workspace.
window.ServerManOverviewSchedule = Object.freeze({ create: createScheduleControl });
