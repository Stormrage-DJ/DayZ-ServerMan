"""Startup classification for interrupted SteamCMD update operations."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..adapters.windows.process_tree import ChildEvidence, ProcessIdentity
from ..adapters.windows.shared_files import read_text_shared, replace_file


# Update states that may still have a live SteamCMD child process
NONTERMINAL = frozenset(("ACCEPTED", "QUEUED", "RUNNING", "CANCELLING"))


class ChildProbe(Protocol):
    """Platform hook that decides whether a recorded child no longer runs."""
    # Return True when the recorded child process is proven absent
    def is_absent(self, evidence: ChildEvidence) -> bool: ...


def inspect_workshop_recovery(root: Path, probe: ChildProbe) -> dict[str, object]:
    """Mark interrupted workshop updates for recovery and report blocking ids."""
    blocked: list[str] = []
    absence: dict[str, bool] = {}
    for path in sorted(root.glob("*.json"), key=lambda value: value.name.casefold()):
        try:
            document = json.loads(read_text_shared(path, encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        # Ignore records that are not workshop update journals
        if not isinstance(document, dict) or document.get("kind") != "UPDATE_WORKSHOP_ITEMS":
            continue
        # Only non-terminal updates can still be running
        if document.get("state") not in NONTERMINAL:
            continue
        operation_id = document.get("operation_id")
        if not isinstance(operation_id, str) or path.name != f"{operation_id}.json":
            blocked.append(path.name)
            continue
        evidence = _child_evidence(document.get("result"))
        # Record whether the recorded child process is proven gone
        absence[operation_id] = bool(evidence is not None and probe.is_absent(evidence))
        # Persist the recovery-required terminal state on disk
        _mark_recovery_required(path, document)
        blocked.append(operation_id)
    return {"blocked": bool(blocked), "operation_ids": blocked, "absence_proven": absence}


def _child_evidence(value: object) -> ChildEvidence | None:
    """Rebuild child process evidence from a stored update result, or None."""
    # Only results claiming a launched child carry evidence
    if not isinstance(value, dict) or value.get("child_state") != "CHILD_LAUNCHED":
        return None
    child = value.get("child")
    if not isinstance(child, dict):
        return None
    # Validate the persisted child shape before trusting any field
    if set(child) != {
        "schema_version", "process_id", "creation_identity", "job_name", "members",
    } or child.get("schema_version") != 1:
        return None
    pid, identity = child.get("process_id"), child.get("creation_identity")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not isinstance(identity, str):
        return None
    job_name, raw_members = child.get("job_name"), child.get("members")
    # A named job must belong to this application's SteamCMD launches
    if job_name is not None and (not isinstance(job_name, str) or not job_name.startswith(
            "Local\\DayZServerMan-SteamCMD-")):
        return None
    if not isinstance(raw_members, list) or not raw_members:
        return None
    members: list[ProcessIdentity] = []
    seen: set[int] = set()
    for raw in raw_members:
        if not isinstance(raw, dict) or set(raw) != {"process_id", "creation_identity"}:
            return None
        member_pid, member_identity = raw.get("process_id"), raw.get("creation_identity")
        if (not isinstance(member_pid, int) or isinstance(member_pid, bool) or member_pid <= 0
                or member_pid in seen or not isinstance(member_identity, str)):
            return None
        seen.add(member_pid)
        members.append(ProcessIdentity(member_pid, member_identity))
    # The main process must appear among its own member set
    if pid not in seen:
        return None
    if not any(value.process_id == pid and value.creation_identity == identity for value in members):
        return None
    return ChildEvidence(pid, identity, tuple(members), job_name)


def _mark_recovery_required(path: Path, document: dict[str, object]) -> None:
    """Rewrite an update record into its recovery-required terminal state."""
    # Bump the revision and stamp the recovery terminal state
    revision = document.get("revision")
    document["revision"] = revision + 1 if isinstance(revision, int) and not isinstance(revision, bool) else 1
    document["state"] = "RECOVERY_REQUIRED"
    document["finished_at"] = datetime.now(UTC).isoformat(timespec="milliseconds")
    document["progress_phase"] = "failed"
    document["terminal_error"] = {
        "code": "UPDATE_RESULT_UNKNOWN",
        "message": "A prior SteamCMD update ended without a proven result.",
        "retryable": False,
    }
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    # Flush the staged copy before the atomic replace
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    # Replace atomically so a crash cannot leave a torn record
    replace_file(temporary, path)
