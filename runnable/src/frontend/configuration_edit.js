// Turns edited configuration inputs into reviewed, fingerprint-bound apply arguments.
"use strict";

// Convert one input control into the typed value for its field.
function fieldValue(field, input) {
  if (field.kind === "boolean") return input.value === "true";
  if (field.kind === "string") return input.value;
  // Reject empty numeric inputs before any coercion.
  if (input.value === "") throw new Error(`${field.key} requires a numeric value.`);
  // Reject values the browser already flags as invalid.
  if (!input.checkValidity()) throw new Error(`${field.key} is not a valid ${field.kind}.`);
  // Enforce whole safe integer values for integer fields.
  if (field.kind === "integer") {
    if (!/^[+-]?\d+$/.test(input.value)) throw new Error(`${field.key} must be a whole integer.`);
    const value = Number(input.value);
    if (!Number.isFinite(value) || !Number.isInteger(value) || !Number.isSafeInteger(value)) {
      throw new Error(`${field.key} must be a safe whole integer.`);
    }
    return value;
  }
  // Require a finite number for number fields.
  if (field.kind === "number") {
    const value = Number(input.value);
    if (!Number.isFinite(value)) throw new Error(`${field.key} must be a finite number.`);
    return value;
  }
  throw new Error(`${field.key} has an unsupported field type.`);
}

// Collect values that differ from the loaded configuration, in field order.
function collectChangedValues(values) {
  const changes = {};
  document.querySelectorAll("[data-configuration-field]").forEach((input) => {
    const field = values.get(input.dataset.configurationField);
    // Skip untouched optional fields that remain empty.
    if (!field.present && input.value === "") return;
    const value = fieldValue(field, input);
    // Record the new value when the field is newly set or changed.
    if (!field.present || JSON.stringify(value) !== JSON.stringify(field.value)) {
      changes[field.key] = value;
    }
  });
  return changes;
}

// Build the frozen apply arguments and fingerprint for the current edits.
function captureEdit(loaded, values) {
  // Refuse to capture before a target has been loaded.
  if (!loaded) throw new Error("Load a configuration target before editing it.");
  const updates = collectChangedValues(values);
  // Sort the changed keys so equivalent edits produce the same fingerprint.
  const orderedUpdates = Object.fromEntries(
    Object.keys(updates).sort().map((key) => [key, updates[key]]),
  );
  const context = {
    profile_id: loaded.profile_id,
    target: loaded.target,
    profile_revision: loaded.profile_revision,
    settings_revision: loaded.settings_revision,
    digest: loaded.digest,
    server_config_digest: loaded.server_config_digest,
  };
  const fingerprint = window.ServerManTransitions.canonicalFingerprint({ context, updates: orderedUpdates });
  const immutableUpdates = window.ServerManTransitions.immutableCopy(orderedUpdates);
  const args = Object.freeze([
    context.profile_id,
    context.target,
    context.profile_revision,
    context.settings_revision,
    context.digest,
    context.server_config_digest,
    immutableUpdates,
  ]);
  return Object.freeze({ fingerprint, args });
}

// Publish the configuration edit helpers used by the workspace.
window.ServerManConfigurationEdit = Object.freeze({
  captureEdit,
  changedValues: collectChangedValues,
});
