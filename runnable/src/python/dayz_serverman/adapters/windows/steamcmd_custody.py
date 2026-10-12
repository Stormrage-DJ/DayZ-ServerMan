"""Keep bounded SteamCMD escalation and surviving native failure custody."""
from __future__ import annotations

import subprocess
from .process_custody import Custody, CustodyError


def owner(tree=None, error=None) -> Custody:
    """Reuse the shared owner or retain a constructor's partial custodian."""
    candidate = error.custody if isinstance(error, CustodyError) else getattr(tree, "custody", None)
    if isinstance(candidate, Custody):
        return candidate
    candidate = getattr(tree, "_supervision_custody", None)
    if isinstance(candidate, Custody):
        return candidate
    custody = Custody()
    if tree is not None:
        tree._supervision_custody = custody
    return custody


def escalate(tree) -> bool:
    """Attempt independent bounded steps while keeping native errors explicit."""
    custody = owner(tree)
    # Existing step durations stay fixed; a native refusal cannot imply termination.
    for action, seconds in ((tree.request_close, 15.0), (tree.terminate_tree, 5.0), (tree.kill_tree, 5.0)):
        try:
            action()
        except OSError as error:
            custody.fault(error)
            custody.secondary.append(("escalation", error))
        try:
            if tree.wait_absent(seconds):
                return custody.primary is None and not custody.secondary
        except OSError as error:
            custody.fault(error)
            custody.secondary.append(("escalation-absence", error))
    return False


def fallback(process, error) -> tuple[bool, Custody]:
    """Contain only the exact held child and any proved partial private job."""
    custody = owner(error=error)
    custody.anchors["legacy-process"] = process
    custody.fault(error)
    tree = custody.anchors.get("tree")
    job = getattr(tree, "job", None)
    if job is not None and job.new_identity and job.reference.state == "owned":
        custody.attempt("partial-job-containment", job.terminate)
    # The constructor failure grants authority only over already-held exact resources.
    custody.attempt("held-terminate", process.terminate)
    absent = custody.attempt("held-wait", lambda: process.wait(timeout=5.0)) is not None
    if not absent:
        custody.attempt("held-kill", process.kill)
        absent = custody.attempt("held-wait-after-kill", lambda: process.wait(timeout=5.0)) is not None
    job_absent = True
    if job is not None:
        job_absent = custody.attempt("partial-job-absence", lambda: job.wait_absent(5.0)) is True if job.new_identity else True
        if absent and job_absent:
            custody.attempt("partial-job-close", job.close)
    hard_faults = [error for _, error in custody.secondary if not isinstance(error, subprocess.TimeoutExpired)]
    return absent and job_absent and not hard_faults, custody


def retain(adapter, result):
    """Keep nonpersisted fault ownership for the adapter's service lifetime."""
    if result.failure_custody is not None:
        adapter.custody_holds.append(result.failure_custody)
    return result


def retaining_call(adapter, function, *arguments):
    """Retain ownership before preserving a supervisor's original thrown fault."""
    try:
        return retain(adapter, function(*arguments))
    except BaseException as error:
        custody = getattr(error, "custody", None)
        if isinstance(custody, Custody):
            adapter.custody_holds.append(custody)
        raise


def with_custody(result, custody):
    """Attach failure diagnostics without changing the existing result schema."""
    if custody is not None:
        object.__setattr__(result, "_failure_custody", custody)
    return result
