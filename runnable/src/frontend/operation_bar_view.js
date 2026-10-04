// Operation bar view: draws the rows of the bar from a plain model and keeps the keyboard focus across a redraw.
"use strict";

// Build one compact bar button; the control key names its action for the click handler and for focus keeping.
function operationBarButton(label, control) {
  const button = window.ServerManUi.element("button", "button operation-button", label);
  button.type = "button";
  button.dataset.barControl = control;
  return button;
}

// Build the target chip, or nothing when the operation belongs to the selected profile or to none.
function operationTargetChip(target) {
  return target ? [window.ServerManUi.element("span", "operation-target", target)] : [];
}

// Build the Cancel button of a row: absent, ready, or locked while the cancellation is in progress.
function operationCancelButton(id, cancel) {
  if (cancel === "none") return [];
  const button = operationBarButton(cancel === "busy" ? "Cancelling…" : "Cancel", `cancel:${id}`);
  if (cancel === "busy") button.setAttribute("aria-disabled", "true");
  return [button];
}

// Build the progress track; without a percent the track is indeterminate and names the phase.
function operationProgressTrack(active) {
  const node = window.ServerManUi.element;
  const track = node("div", "progress-track operation-progress");
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", `${active.name} progress`);
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  const fill = node("div", "progress-bar");
  if (active.determinate) {
    track.setAttribute("aria-valuenow", String(active.percent));
    fill.style.width = `${active.percent}%`;
  } else {
    track.classList.add("is-indeterminate");
    track.setAttribute("aria-valuetext", active.phase);
  }
  track.append(fill);
  return track;
}

// Build the row of the running, cancelling, or first waiting operation.
function operationActiveRow(active, queueOpen) {
  const node = window.ServerManUi.element;
  const row = node("div", `operation-row is-${active.look}`);
  const dot = node("span", "operation-dot"); dot.setAttribute("aria-hidden", "true");
  // Name and target stay together; phase, track, and percent form the detail group.
  const heading = node("div", "operation-heading");
  heading.append(node("strong", "operation-name", active.name), ...operationTargetChip(active.target));
  const detail = node("div", "operation-detail");
  const phase = node("span", "operation-phase", active.phase); phase.title = active.phase;
  detail.append(phase, operationProgressTrack(active));
  if (active.determinate) detail.append(node("span", "operation-percent", `${active.percent}%`));
  const actions = node("div", "operation-actions");
  // Offer the queue list only while more operations wait.
  if (active.waiting > 0) {
    const queue = operationBarButton(`+${active.waiting} waiting`, "queue");
    queue.setAttribute("aria-expanded", String(queueOpen));
    queue.setAttribute("aria-controls", "operation-queue");
    actions.append(queue);
  }
  actions.append(...operationCancelButton(active.id, active.cancel));
  row.append(dot, heading, detail, actions);
  // A failed cancellation request is explained below the phase.
  if (active.note) row.append(node("p", "operation-note", active.note));
  return row;
}

// Build the row of a finished operation: sentence, message, and the two result actions.
function operationResultRow(result) {
  const node = window.ServerManUi.element;
  const row = node("div", `operation-row operation-result is-${result.look}`);
  const dot = node("span", "operation-dot"); dot.setAttribute("aria-hidden", "true");
  const copy = node("div", "operation-copy");
  const heading = node("div", "operation-heading");
  heading.append(node("strong", "operation-name", result.sentence), ...operationTargetChip(result.target));
  copy.append(heading);
  if (result.message) copy.append(node("span", "operation-message", result.message));
  const actions = node("div", "operation-actions");
  actions.append(operationBarButton("View in Logs", `logs:${result.id}`),
    operationBarButton("Dismiss", `dismiss:${result.id}`));
  row.append(dot, copy, actions);
  return row;
}

// Build the list of the other waiting operations, each with its own Cancel.
function operationQueueList(queue) {
  const node = window.ServerManUi.element;
  const list = node("ul", "operation-queue"); list.id = "operation-queue";
  list.setAttribute("aria-label", "Waiting operations");
  queue.forEach((entry) => {
    const item = node("li", "operation-queue-item");
    item.append(node("strong", "operation-name", entry.name), ...operationTargetChip(entry.target),
      node("span", "operation-phase", entry.state), ...operationCancelButton(entry.id, entry.cancel));
    list.append(item);
  });
  return list;
}

// Put the focus back on the control that had it, or on the control that took its place.
function restoreOperationBarFocus(bar, control) {
  if (!control) return;
  const find = (key) => [...bar.querySelectorAll("[data-bar-control]")]
    .find((button) => button.dataset.barControl === key);
  // A Cancel that ended with its operation hands the focus to Dismiss of that result.
  const target = find(control) || (control.startsWith("cancel:")
    ? find(`dismiss:${control.slice("cancel:".length)}`) : null);
  if (target) target.focus();
  else document.getElementById("main-content")?.focus();
}

// Draw the bar from the model; an empty model hides the bar.
function drawOperationBar(model) {
  const bar = document.getElementById("operation-bar");
  if (!bar) return;
  // Remember the focused bar control before its element is replaced.
  const focused = bar.contains(document.activeElement) ? document.activeElement.dataset.barControl : null;
  const earlier = bar.querySelector(".operation-progress:not(.is-indeterminate) .progress-bar")?.style.width;
  const inner = window.ServerManUi.element("div", "operation-bar-inner");
  if (model.problem) inner.append(operationResultRow(model.problem));
  if (model.active) inner.append(operationActiveRow(model.active, model.queueOpen));
  if (model.success) inner.append(operationResultRow(model.success));
  if (model.queueOpen && model.queue.length) inner.append(operationQueueList(model.queue));
  bar.replaceChildren(...(inner.childElementCount ? [inner] : []));
  bar.hidden = inner.childElementCount === 0;
  // Let a determinate fill grow from its earlier width instead of jumping.
  const fill = bar.querySelector(".operation-progress:not(.is-indeterminate) .progress-bar");
  if (earlier && fill && earlier !== fill.style.width) {
    const width = fill.style.width;
    fill.style.width = earlier; void fill.offsetWidth; fill.style.width = width;
  }
  restoreOperationBarFocus(bar, focused);
}
