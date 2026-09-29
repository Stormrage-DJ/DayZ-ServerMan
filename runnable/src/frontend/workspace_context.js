// Tracks the active shell section and guards async UI updates against stale work.
"use strict";

// Shared generation counters used to detect stale workspace captures.
const workspaceContext = {
  generation: 0,
  // Per-owner generation counters keyed by owner name.
  ownerGenerations: new Map(),
  workspace: "overview",
};

// Activate a workspace section and return a fresh capture token.
function activateWorkspace(workspace) {
  workspaceContext.workspace = workspace;
  workspaceContext.generation += 1;
  return captureWorkspace(workspace);
}

// Invalidate the current generation, globally or for a single owner.
function invalidateWorkspace(owner = null) {
  if (owner === null) {
    workspaceContext.generation += 1;
    return;
  }
  workspaceContext.ownerGenerations.set(
    owner,
    (workspaceContext.ownerGenerations.get(owner) || 0) + 1,
  );
}

// Capture a frozen token that describes the current workspace state.
function captureWorkspace(workspace = workspaceContext.workspace, owner = null) {
  return Object.freeze({
    generation: workspaceContext.generation,
    owner,
    ownerGeneration: owner === null ? null : workspaceContext.ownerGenerations.get(owner) || 0,
    workspace,
  });
}

// Report whether a captured token still matches the live workspace state.
function isWorkspaceActive(token) {
  return Boolean(token
    && token.workspace === workspaceContext.workspace
    && token.generation === workspaceContext.generation
    && (token.owner === null
      || token.ownerGeneration === (workspaceContext.ownerGenerations.get(token.owner) || 0)));
}

// Publish the workspace helpers used by the shell UI.
window.ServerManWorkspace = Object.freeze({
  activate: activateWorkspace,
  capture: captureWorkspace,
  invalidate: invalidateWorkspace,
  isActive: isWorkspaceActive,
});
