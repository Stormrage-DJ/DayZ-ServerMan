// Formats backup dates, sizes, and summaries for the desktop interface.
"use strict";

// Format one backup timestamp, falling back to a neutral label for invalid values.
function formatBackupDate(value) {
  const date = new Date(value);
  // Fall back to a neutral label when the timestamp cannot be parsed.
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

// Render a byte count as a readable size with binary units.
function formatBackupSize(bytes) {
  let value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return "Unknown size";
  const units = ["bytes", "KB", "MB", "GB", "TB"];
  let unit = 0;
  // Scale the value down into the largest sensible binary unit.
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // Drop decimal places for whole units and large values.
  const digits = unit === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

// Summarize one backup as date, file count, and total size.
function backupSummary(backup) {
  const files = Number(backup.entry_count) === 1 ? "1 file" : `${backup.entry_count} files`;
  return `${formatBackupDate(backup.created_at)} · ${files} · ${formatBackupSize(backup.total_size)}`;
}

// Normalize legacy wording in backup messages to the current product terms.
function userBackupText(value) {
  return String(value).replaceAll("Snapshots", "Backups").replaceAll("Snapshot", "Backup")
    .replaceAll("snapshots", "backups").replaceAll("snapshot", "backup");
}

// Publish the backup display helpers used by the interface.
window.ServerManBackupDisplay = Object.freeze({
  date: formatBackupDate,
  size: formatBackupSize,
  summary: backupSummary,
  text: userBackupText,
});
