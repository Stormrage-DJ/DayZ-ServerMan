// Profile-scoped backup history rendering, separate from creation controls.
"use strict";
// Render one profile's backup history, diagnostics, and legacy references.
function renderBackupHistory(history) {
  const recent = [...history.backups].sort((left, right) =>
    String(right.created_at).localeCompare(String(left.created_at))
      || String(right.backup_id).localeCompare(String(left.backup_id))).slice(0, 3);
  history = { ...history, backups: recent };
  backupState.history = history;
  const profile = backupState.profiles.find((item) => item.profile_id === history.profile_id);
  const profileName = profile ? profile.display_name : history.profile_id;
  const list = document.getElementById("backup-history");
  list.replaceChildren();
  // Show an explicit empty state when no backups exist for this profile.
  if (!history.backups.length) {
    const empty = backupNode("div", "notice", "No backups exist for this server profile.");
    empty.dataset.state = "empty";
    list.append(empty);
  } else {
    // Describe each backup with its verification state and restore readiness.
    history.backups.forEach((backup) => {
      const item = backupNode("article", "backup-item");
      const compatible = backup.restore_compatibility === "COMPATIBLE";
      item.append(
        backupNode("strong", "", `${profileName} backup`),
        backupRestoreAction(backup),
        backupNode("span", "status-label status-normal", "Verified"),
        backupNode("span", compatible ? "status-label status-normal" : "status-label status-warning",
          compatible ? "Restore ready" : "Restore support pending"),
        backupNode("p", "", window.ServerManBackupDisplay.summary(backup)),
        backupNode("p", "", "Includes configuration, runtime, mission and world persistence."),
      );
      if (!compatible) item.append(backupNode("p", "",
        window.ServerManBackupDisplay.text(backup.restore_compatibility_reason)));
      list.append(item);
    });
  }
  // Surface profile and destination diagnostics from the last verification.
  history.diagnostics.forEach((diagnostic) => {
    const scope = diagnostic.scope === "PROFILE" ? "This profile" : "Backup destination";
    const warning = backupNode("div", "notice notice-warning",
      `${scope}: ${window.ServerManBackupDisplay.text(diagnostic.message)}`);
    warning.dataset.state = diagnostic.code === "CORRUPT" ? "recovery" : "warning";
    warning.setAttribute("role", "status");
    list.append(warning);
  });
  // Keep older external references summarized rather than adding another archive list.
  if (history.legacy_backups?.length) list.append(backupNode("p", "notice notice-warning",
    `${history.legacy_backups.length} legacy backup reference(s) are unavailable for restore.`));
  document.getElementById("backup-destination").textContent =
    history.destination_kind === "custom" ? "Custom local destination" : "Portable default destination";
  const create = document.getElementById("backup-create");
  create.disabled = !history.runtime_profile;
  // Explain how to fix the missing runtime profile before creating backups.
  if (!history.runtime_profile) {
    const setup = backupNode("div", "notice notice-warning",
      "Set a runtime profile directory in Profiles before creating a backup.");
    setup.setAttribute("role", "status");
    document.getElementById("backup-feedback").replaceChildren(setup);
  }
  if (window.ServerManRestore) void window.ServerManRestore.render(history);
}

// Open this card's regular restore review with a labelled, keyboard-accessible icon.
function backupRestoreAction(backup) {
  const button = backupNode("button", "button backup-restore-action");
  button.type = "button";
  button.dataset.backupId = backup.backup_id;
  const label = `Restore backup from ${window.ServerManBackupDisplay.date(backup.created_at)}`;
  button.setAttribute("aria-label", label); button.title = label;
  button.disabled = backup.restore_compatibility !== "COMPATIBLE";
  if (button.disabled) button.title = window.ServerManBackupDisplay.text(backup.restore_compatibility_reason);
  const icon = backupNode("span", "", "↶");
  icon.setAttribute("aria-hidden", "true"); button.append(icon);
  button.addEventListener("click", () => { void window.ServerManRestore.preview(backup.backup_id); });
  return button;
}
