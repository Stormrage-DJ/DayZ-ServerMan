"""A scripted bridge behind the CLI runner for `mods update` (task 5.3): every branch of 10.3 without SteamCMD.

The bridge checks the exact parameter set of each call as the host handlers do, answers from
scripted values, and records every call. "Nothing applied" means that no apply call was made.
The real-composition path of the same command is in `tests/test_cli_mods_update_compositions.py`.
"""

from __future__ import annotations

import io
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.lifecycle_coordinator import OTHER_PROFILE_RUNNING  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.session_observer import BridgeCallFailed  # noqa: E402

PUBLICATION = {"profile_id", "expected_profile_revision", "expected_semantic_profile_digest",
               "expected_settings_revision", "update_operation_id"}
# The exact parameter sets of the host handlers that the command calls
EXACT: dict[str, set[str]] = {
    "list_profiles": set(), "get_ui_preferences": set(), "get_server_status": set(),
    "get_application_snapshot": set(), "read_profile": {"profile_id"},
    "update_workshop_items": {"profile_id", "expected_profile_revision", "expected_semantic_profile_digest",
                              "expected_settings_revision", "authentication_mode", "account_name",
                              "update_all_and_start"},
    "preview_mod_publication": PUBLICATION,
    "publish_mods_and_keys": PUBLICATION | {"publication_fingerprint"},
    "apply_mods_and_restart": PUBLICATION | {"publication_fingerprint", "backup_after_stop"},
    "get_operation": {"operation_id"}, "request_operation_cancellation": {"operation_id"},
}
APPLY_METHODS = {"publish_mods_and_keys": "PUBLISH_MODS_AND_KEYS", "apply_mods_and_restart": "APPLY_MODS_AND_RESTART"}
DIGEST = "a" * 64
FINGERPRINT = "b" * 64


def item(workshop_id: str, outcome: str, proof: str | None = "FULL_CONTENT", error: str | None = None) -> dict:
    """Return one item of an update result with its outcome and proof kind."""
    return {"item": {"workshop_id": workshop_id}, "outcome": outcome, "error_code": error,
            "cache_proof": {"verification_kind": proof} if proof else None}


def update_result(**changes: Any) -> dict:
    """Return the result of an update that downloaded one mod and found one current."""
    value = {"profile_id": "alpha", "profile_revision": 3, "semantic_profile_digest": DIGEST, "settings_revision": 2,
             "download_state": "VERIFIED", "start_requested": False, "process_id": 77, "steamcmd_exit_code": 0,
             "steamcmd_summary": None,
             "items": [item("111", "UPDATED_VERIFIED"), item("222", "VERIFIED_CURRENT", "APPLIED_STATE")]}
    value.update(changes)
    return value


def current_result(**changes: Any) -> dict:
    """Return the result of an update that found everything current: no SteamCMD run, every mod applied."""
    return update_result(process_id=None, steamcmd_exit_code=None, items=[
        item("111", "VERIFIED_CURRENT", "APPLIED_STATE"), item("222", "VERIFIED_CURRENT", "APPLIED_STATE")],
        **changes)


def preview(**changes: Any) -> dict:
    """Return the review of a plan that writes one mod folder; the keys are in place."""
    value = {"profile_id": "alpha", "profile_revision": 3, "semantic_profile_digest": DIGEST, "settings_revision": 2,
             "update_operation_id": "op-1", "publication_fingerprint": FINGERPRINT,
             "targets": [{"workshop_id": "111", "target_relative": "@Alpha", "current": False},
                         {"workshop_id": "222", "target_relative": "@Bravo", "current": True}],
             "key_count": 2, "missing_key_count": 0, "plain_apply_guarded": True}
    value.update(changes)
    return value


class ScriptedBridge:
    """Answers the bridge calls of one test; `statuses` are the successive server status answers."""

    def __init__(self) -> None:
        """Start with a stopped server, one profile with anonymous Steam sign-in, and a writing plan."""
        self.calls: list[tuple[str, dict]] = []
        self.statuses: list[dict] = [{"state": "STOPPED", "profile_id": None}]
        self.profiles = [{"profile_id": "alpha", "display_name": "Alpha"},
                         {"profile_id": "other", "display_name": "Other World"}]
        self.settings = {"revision": 2, "steam_authentication_mode": "ANONYMOUS", "steam_account_name": None}
        self.preferences = {"selected_profile_id": "alpha", "backup_after_stop_profiles": []}
        self.mutation_block: str | None = None
        self.update = update_result()
        self.update_end: tuple[str, dict | None, str | None] = ("SUCCEEDED", None, "complete")
        self.preview = preview()
        self.apply_result: dict = {"profile_id": "alpha", "publication_state": "PUBLISHED",
                                   "start_state": "NOT_REQUESTED"}
        self.apply_end: tuple[str, dict | None, str | None] = ("SUCCEEDED", None, "complete")
        self.apply_percent = 100
        # Errors that dispatch answers per method
        self.refuse: dict[str, dict] = {}
        self.operations: dict[str, dict] = {}
        # Functions that run when a method is called, for example a Ctrl+C during the download
        self.hooks: dict[str, Any] = {}

    def methods(self) -> list[str]:
        """Return the called methods in order."""
        return [method for method, _parameters in self.calls]

    def applied(self) -> list[str]:
        """Return the apply calls that were made."""
        return [method for method in self.methods() if method in APPLY_METHODS]

    def parameters(self, method: str) -> dict:
        """Return the parameters of the last call of a method."""
        return [parameters for name, parameters in self.calls if name == method][-1]

    def dispatch(self, envelope: dict) -> dict:
        """Answer one envelope: a wrong parameter set is refused as the host refuses it."""
        method, parameters = envelope["method"], envelope["parameters"]
        self.calls.append((method, dict(parameters)))
        if method in self.hooks:
            self.hooks[method]()
        if set(parameters) != EXACT[method]:
            return {"success": False, "error": {"code": "INVALID_REQUEST", "message": f"{method} fields"}}
        if method in self.refuse:
            return {"success": False, "error": self.refuse[method]}
        if method == "apply_mods_and_restart" and self._running_other(parameters):
            return {"success": False, "error": {"code": "INVALID_REQUEST", "message": OTHER_PROFILE_RUNNING}}
        return {"success": True, "value": self._answer(method, parameters)}

    def _running_other(self, parameters: dict) -> bool:
        """D11 of the composition's wrapper: the running profile is another one."""
        running = self.statuses[0].get("profile_id")
        return running is not None and running != parameters["profile_id"]

    def _answer(self, method: str, parameters: dict) -> Any:
        """Return the scripted value of a call."""
        if method == "get_server_status":
            return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if method == "list_profiles":
            return self.profiles
        if method == "get_ui_preferences":
            return self.preferences
        if method == "get_application_snapshot":
            return {"settings": self.settings, "mutation_block": self.mutation_block, "mutation_block_owner": None}
        if method == "read_profile":
            return {"profile_id": "alpha", "display_name": "Alpha", "revision": 3, "semantic_digest": DIGEST}
        if method == "update_workshop_items":
            result = {**self.update, "start_requested": parameters["update_all_and_start"]}
            return self._submit("op-1", "UPDATE_WORKSHOP_ITEMS", result, self.update_end, 100)
        if method == "preview_mod_publication":
            return self.preview
        if method in APPLY_METHODS:
            return self._submit("op-2", APPLY_METHODS[method], self.apply_result, self.apply_end, self.apply_percent)
        if method == "get_operation":
            return self.operations[parameters["operation_id"]]
        return {}

    def _submit(self, operation_id: str, kind: str, result: dict, end: tuple, percent: int) -> dict:
        """Store a finished record for the waiter and return the submit answer."""
        state, error, phase = end
        self.operations[operation_id] = {
            "operation_id": operation_id, "kind": kind, "state": state, "progress_percent": percent,
            "progress_phase": phase, "last_working_phase": phase, "terminal_error": error,
            "result": result if state == "SUCCEEDED" else None}
        return {"operation_id": operation_id, "state": "QUEUED"}


class _Observer:
    """An observer session over the scripted bridge."""

    def __init__(self, bridge: ScriptedBridge) -> None:
        """Bind the bridge; the data folder exists."""
        self.bridge = bridge
        self.paths = SimpleNamespace(data=Path(__file__).resolve().parent)

    def call(self, method: str, parameters: dict) -> Any:
        """Answer one read as an observer does."""
        answer = self.bridge.dispatch({"method": method, "parameters": parameters})
        if not answer["success"]:
            raise BridgeCallFailed(answer["error"])
        return answer["value"]

    def close(self) -> None:
        """Nothing to close."""


def patched_sessions(bridge: ScriptedBridge) -> ExitStack:
    """Return the patches that give the runner sessions over the scripted bridge."""
    owner = SimpleNamespace(composition=SimpleNamespace(bridge=SimpleNamespace(dispatch=bridge.dispatch)),
                            close=lambda drain_seconds=None: None)
    stack = ExitStack()
    stack.enter_context(patch.object(runner.observer_sessions, "open_observer_session",
                                     lambda _root: _Observer(bridge)))
    stack.enter_context(patch.object(runner.owner_sessions, "open_owner_session", lambda *_a, **_k: owner))
    return stack


def run_update(bridge: ScriptedBridge, *arguments: str, stdin=None,
               interrupts: Interrupts | None = None) -> tuple[int, str, str]:
    """Run `mods update --profile alpha` with the arguments over the scripted bridge."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with patched_sessions(bridge):
        code = runner.run(["mods", "update", "--profile", "alpha", *arguments], None, stdout=stdout, stderr=stderr,
                          interrupts=interrupts or Interrupts(), stdin=stdin)
    return code, stdout.getvalue(), stderr.getvalue()
