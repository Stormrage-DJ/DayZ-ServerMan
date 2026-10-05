"""Manager activity wording, the causes of a refused submission, and the logged recovery block reasons."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from tests.ui_harness_support import FRONTEND, ROOT
except ModuleNotFoundError:
    from ui_harness_support import FRONTEND, ROOT

from dayz_serverman.application import activity_wording as wording
from dayz_serverman.application.log_activity import (
    EVENT_TEXTS, HIDDEN_EVENTS, METHOD_TEXTS, format_manager_record,
)
from dayz_serverman.application.logs import LogQueryService
from dayz_serverman.application.migration_preview import settings_proposal
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import QueueUnavailable
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.bridge.contracts import CONTRACT_VERSION, ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError, BridgeFacade
from dayz_serverman.observability.structured_log import StructuredLogger


PACKAGE = ROOT / "runnable" / "src" / "python" / "dayz_serverman"
# Events with their own sentence builder in the activity formatter
BUILT_EVENTS = {"operation.state", "bridge.failure", "operation_lane.recovery_block",
                "schedule.saved", "schedule.queued", "update_check.completed", "server_build.check_completed"}


def activity(event: str, fields: dict, level: str = "INFO") -> str:
    """Return the activity sentence of one record without its time and level columns."""
    line = format_manager_record({"event": event, "level": level, "fields": fields,
                                  "occurred_at": "2026-10-03T10:00:00Z"})
    return line.split("  ", 2)[2].strip() if line else ""


class MutationConflictCauseTests(unittest.TestCase):
    """QF-007: the error envelope names why a submission was refused."""

    def setUp(self) -> None:
        """Create a lane with a one-entry queue, a log file, and a bridge method that submits."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_conflict_")
        self.gate = threading.Event()
        self.started = threading.Event()
        root = Path(self.temporary.name)
        self.log = root / "manager.jsonl"
        self.manager = OperationManager(
            OperationStore(root / "operations"), queue_limit=1, logger=StructuredLogger(self.log))

        def submit(_parameters):
            """Submit like every coordinator: a refusal becomes a mutation conflict with its cause."""
            try:
                return {"operation_id": self.manager.submit("SAVE_PROFILE", self.work).operation_id}
            except QueueUnavailable as error:
                raise ApplicationCallError(
                    ErrorCode.MUTATION_CONFLICT, str(error), retryable=True, details=error.details,
                ) from error
        self.facade = BridgeFacade({"submit": submit})

    def tearDown(self) -> None:
        """Let the blocked work end, stop the lane, and remove the temporary root."""
        self.gate.set()
        self.manager.shutdown(2)
        self.temporary.cleanup()

    def work(self, _context) -> None:
        """Report that the lane took this work, then hold the lane until the test ends."""
        self.started.set()
        self.gate.wait(5)

    def call(self) -> dict:
        """Dispatch one submission through the bridge and return the envelope."""
        return self.facade.dispatch({"contract_version": CONTRACT_VERSION, "request_id": "r1",
                                     "method": "submit", "parameters": {}})

    def assert_conflict(self, reason: str, message: str) -> None:
        """Check code, additive detail, and message of a refused submission."""
        error = self.call()["error"]
        self.assertEqual(error["code"], "MUTATION_CONFLICT")
        self.assertEqual(error["details"], {"reason": reason})
        self.assertEqual(error["message"], message)
        self.assertTrue(error["retryable"])
        self.assertEqual(CONTRACT_VERSION, 1)

    def test_recovery_block_is_named_with_its_reason(self) -> None:
        """A blocked lane reports the cause and keeps the block reason as message."""
        self.manager.block_for_recovery("Mutations are blocked by unresolved restore recovery.")
        self.assert_conflict("RECOVERY_BLOCK", "Mutations are blocked by unresolved restore recovery.")

    def test_draining_lane_is_named(self) -> None:
        """A lane that shuts down reports the closing cause."""
        self.manager.begin_shutdown()
        self.assert_conflict("SHUTTING_DOWN", "operation lane is draining")

    def test_full_queue_is_named(self) -> None:
        """A full queue reports the queue cause."""
        # The lane must hold the first operation before the one queue slot is filled
        self.assertTrue(self.call()["success"])
        self.assertTrue(self.started.wait(5), "the lane did not take the first operation")
        self.assertTrue(self.call()["success"])
        self.assert_conflict("QUEUE_FULL", "operation queue is full")

    def test_every_coordinator_passes_the_cause(self) -> None:
        """Each place that turns a refusal into a mutation conflict hands over the detail."""
        sites = 0
        for path in sorted((PACKAGE / "application").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for block in re.findall(r"except QueueUnavailable as error:\n(.*?)from error", source, re.DOTALL):
                sites += 1
                self.assertIn("ErrorCode.MUTATION_CONFLICT", block, path.name)
                self.assertIn("details=error.details", block, path.name)
        self.assertEqual(sites, 17)

    def test_block_reason_is_logged_when_it_is_set(self) -> None:
        """QF-008: Manager diagnostics holds every block reason; Manager activity words it."""
        reason = "Mutations are blocked by an interrupted SteamCMD update with an unknown result."
        self.manager.block_for_recovery(reason)
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        blocks = [record for record in records if record["event"] == "operation_lane.recovery_block"]
        self.assertEqual([(record["level"], record["fields"]) for record in blocks], [("ERROR", {"reason": reason})])
        service = LogQueryService(self.log, self.log.with_name("server.log"))
        raw = service.read_log({"source": "manager_diagnostics", "maximum_lines": 50})["lines"]
        self.assertTrue(any(reason in line for line in raw))
        shown = service.read_log({"source": "manager", "maximum_lines": 50})["lines"]
        self.assertTrue(shown[-1].endswith(
            "Error    Changes are now blocked. A mod update was interrupted, so its result is not known. "
            "Restart DayZ-ServerMan, then update the mods again."), shown)


class ActivityFormatterTests(unittest.TestCase):
    """QF-010: Manager activity is operator wording for kinds, states, codes, methods, and actions."""

    def test_scenarios_of_the_review(self) -> None:
        """The five records of the independent review give plain sentences."""
        self.assertEqual(activity("operation.state", {"kind": "START_SERVER", "state": "FAILED",
            "error_code": "EXTERNAL_PROCESS", "error_message": "Cannot start while server state is RUNNING_EXTERNAL."}),
            "The server could not be started. This cannot be done while the server is running outside DayZ-ServerMan.")
        self.assertEqual(activity("operation.state", {"kind": "PUBLISH_MODS_AND_KEYS",
            "state": "RECOVERY_REQUIRED", "error_code": "PUBLICATION_FAILED"}),
            "The mods could not be applied. Changes are blocked. The mods could not be copied to the server folder.")
        self.assertEqual(activity("operation.state", {"kind": "UPDATE_WORKSHOP_ITEMS", "state": "SUCCEEDED"}),
                         "Mod update finished. The result is on the Mods page.")
        self.assertEqual(activity("bridge.failure", {"method": "save_steam_settings", "error_code": "INVALID_REQUEST",
            "message": "steam_account_name must contain only letters, digits, or underscore"}, "WARNING"),
            "Saving Steam sign-in settings failed: The Steam account name may contain only letters, digits and underscores.")
        self.assertEqual(activity("schedule.queued", {"action": "RESTART", "operation_id": "abc",
            "profile_id": "alpha"}), "The scheduled save and restart started.")

    def test_results_errors_conflicts_and_schedule(self) -> None:
        """Result sentences, the error rules, the three conflict causes, and the schedule lines."""
        self.assertEqual(activity("operation.state", {"kind": "CREATE_BACKUP", "state": "CANCELLED"}), "Backup cancelled.")
        self.assertEqual(activity("operation.state", {"kind": "START_SERVER", "state": "FAILED",
            "error_code": "LAUNCH_FAILED", "error_message": "The DayZ server could not be started."}),
            "The server could not be started.")
        self.assertEqual(activity("operation.state", {"kind": "LATER_KIND", "state": "SUCCEEDED"}), "Operation finished.")
        self.assertEqual(activity("operation.state", {"kind": "CREATE_BACKUP", "state": "RUNNING"}), "")
        self.assertEqual(wording.error_text("INVALID_REQUEST", "maxPlayers is above its supported maximum"),
                         wording.REQUEST_TEXT)
        self.assertEqual(wording.error_text("INVALID_REQUEST", "dayz_executable must be inside dayz_root"),
                         "The DayZ server program must be inside the DayZ server folder.")
        self.assertEqual(wording.error_text("STORAGE_FAILURE", "write failed for applied_mod_state"), wording.INTERNAL_TEXT)
        self.assertEqual(wording.error_text("MUTATION_CONFLICT", "operation queue is full"), wording.QUEUE_FULL_TEXT)
        self.assertEqual(wording.error_text("MUTATION_CONFLICT", "operation lane is draining"), wording.CLOSING_TEXT)
        self.assertEqual(wording.error_text("MUTATION_CONFLICT", "Mutations are blocked by unresolved restore recovery."),
                         "Changes are blocked until recovery is resolved. A backup restore did not finish cleanly. "
                         "Open Backups; DayZ-ServerMan checks the unfinished restore again there.")
        self.assertEqual(activity("schedule.saved", {"profile_id": "alpha", "action": "stop", "hour": 4, "minute": 5}),
                         "Daily schedule saved: save and stop at 04:05.")
        self.assertEqual(activity("schedule.saved", {"profile_id": "alpha", "action": None}), "Daily schedule turned off.")
        self.assertEqual(activity("later.event", {"some_field": "RAW_VALUE"}), "")
        self.assertEqual(activity("later.event", {"some_field": "RAW_VALUE"}, "ERROR"),
                         "A problem was recorded. Details are in Manager diagnostics.")
        self.assertEqual(activity("bridge.failure", {"method": "later_method", "error_code": "NOT_FOUND",
            "message": "Profile was not found."}), "A request failed: Profile was not found.")

    def test_a_code_text_follows_the_operation_kind(self) -> None:
        """D19: an unproven SteamCMD exit of a sign-in speaks of the sign-in, of a mod update of the update."""
        sign_in = activity("operation.state", {"kind": "AUTHENTICATE_STEAMCMD", "state": "RECOVERY_REQUIRED",
            "error_code": "UPDATE_RESULT_UNKNOWN",
            "error_message": "SteamCMD process-tree exit after the sign-in could not be proven."})
        self.assertEqual(sign_in, "Steam sign-in did not complete. Changes are blocked. SteamCMD did not close cleanly "
                         "after the Steam sign-in, so the sign-in cannot be confirmed.")
        self.assertFalse(wording.leaks_identifier(sign_in))
        update = activity("operation.state", {"kind": "UPDATE_WORKSHOP_ITEMS", "state": "RECOVERY_REQUIRED",
            "error_code": "UPDATE_RESULT_UNKNOWN", "error_message": "A prior SteamCMD update ended without a proven result."})
        self.assertEqual(update, "The mods could not be updated. Changes are blocked. SteamCMD did not close cleanly, "
                         "so the update cannot be confirmed.")
        # D19: every recovery line states the block once, also for the general recovery code
        recovery = activity("operation.state", {"kind": "RESTORE_BACKUP", "state": "RECOVERY_REQUIRED",
            "error_code": "RECOVERY_REQUIRED", "error_message": "x"})
        self.assertEqual(recovery, "The backup could not be restored. Changes are blocked. An earlier operation did not "
                         "finish cleanly. Details are in Logs, Manager diagnostics.")
        for line in (sign_in, update, recovery):
            self.assertEqual(line.count("Changes are blocked"), 1, line)
        # Without a kind, and for another code of the same kind, the general text stays
        self.assertEqual(wording.error_text("UPDATE_RESULT_UNKNOWN", ""), wording.ERROR_TEXTS["UPDATE_RESULT_UNKNOWN"])
        self.assertEqual(wording.error_text("AUTHENTICATION_FAILED", "", kind="AUTHENTICATE_STEAMCMD"),
                         wording.ERROR_TEXTS["AUTHENTICATION_FAILED"])

    def test_every_backend_event_and_frontend_method_is_known(self) -> None:
        """Each event that the backend logs is hidden or worded; each bridge method of the pages has a subject."""
        events: set[str] = set()
        for path in PACKAGE.rglob("*.py"):
            events.update(re.findall(r'(?:_log|\.emit)\(\s*"([a-z_]+\.[a-z_.]+)"', path.read_text(encoding="utf-8")))
        self.assertGreaterEqual(len(events), 20, sorted(events))
        self.assertEqual(events - HIDDEN_EVENTS - BUILT_EVENTS - set(EVENT_TEXTS), set())
        methods: set[str] = set()
        for path in FRONTEND.glob("*.js"):
            methods.update(re.findall(r"pywebview\.api\.([a-z_]+)", path.read_text(encoding="utf-8")))
        self.assertGreaterEqual(len(methods), 44)
        self.assertEqual(methods - set(METHOD_TEXTS), set())

    def test_python_and_frontend_catalogues_agree(self) -> None:
        """The kinds with their four texts and the error codes are the same in both catalogues."""
        labels = (FRONTEND / "operation_labels.js").read_text(encoding="utf-8")
        table = labels.split("const operationKindLabels = Object.freeze({", 1)[1].split("});", 1)[0]
        kinds = {kind: tuple(re.findall(r'"([^"]+)"', texts))
                 for kind, texts in re.findall(r'^  ([A-Z_]+): (\[.*\]),$', table, re.MULTILINE)}
        self.assertEqual(kinds, wording.KIND_TEXTS)
        self.assertIn(json.dumps(list(wording.KIND_FALLBACK)), labels)
        messages = (FRONTEND / "operation_messages.js").read_text(encoding="utf-8")
        codes = messages.split("const operationErrorTexts = Object.freeze({", 1)[1].split("});", 1)[0]
        self.assertEqual(set(re.findall(r"^  ([A-Z_]+): ", codes, re.MULTILINE)), set(wording.ERROR_TEXTS))
        self.assertEqual(set(wording.NEUTRAL_SUCCESS) - set(wording.KIND_TEXTS), set())
        # The code texts of one operation kind are the same table in both catalogues
        own = messages.split("const operationKindErrorTexts = Object.freeze({", 1)[1].split("\n});", 1)[0]
        self.assertEqual({kind: dict(re.findall(r'^    ([A-Z_]+): "([^"]+)",$', texts, re.MULTILINE))
                          for kind, texts in re.findall(r"^  ([A-Z_]+): Object\.freeze\(\{\n(.*?)\n  \}\),$",
                                                        own, re.MULTILINE | re.DOTALL)}, wording.KIND_ERROR_TEXTS)
        self.assertEqual(set(wording.KIND_ERROR_TEXTS) - set(wording.KIND_TEXTS), set())
        # Both catalogues drop the same block sentences from a recovery result
        self.assertIn(f"const OPERATION_BLOCK_SENTENCE = /{wording.BLOCK_SENTENCE.pattern}/g;", messages)
        # The sentences of "apply mods and restart" are the same table in both catalogues
        restart = messages.split("const operationRestartApplyTexts = Object.freeze({", 1)[1].split("});", 1)[0]
        self.assertEqual(dict(re.findall(r'^  (\w+): "([^"]+)",$', restart, re.MULTILINE)),
                         wording.RESTART_APPLY_TEXTS)

    def test_legacy_preview_names_settings_by_their_labels(self) -> None:
        """The import preview says "DayZ server folder", never the settings field name."""
        with tempfile.TemporaryDirectory(prefix="serverman_preview_") as temporary:
            root = Path(temporary)

            class Inventory:
                """Legacy inventory with only the installation root."""
            inventory = Inventory()
            inventory.root = root

            class Settings:
                """Current settings: the same root in one case, another root in the other."""
                dayz_executable = None
            same, other = Settings(), Settings()
            same.dayz_root, other.dayz_root = str(root), str(root / "elsewhere")
            _updates, _conflicts, warnings = settings_proposal(inventory, same)
            self.assertEqual(warnings, ("The current DayZ server folder already matches the legacy installation.",))
            _updates, conflicts, _warnings = settings_proposal(inventory, other)
            self.assertIn("The current DayZ server folder is already configured differently.",
                          [conflict.message for conflict in conflicts])


if __name__ == "__main__":
    unittest.main()
