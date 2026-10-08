"""Task 2.5, criteria 4, 8, 25, 26: exit code per error code (6.4) and the pre-change evidence (6.5)."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.lifecycle_coordinator import OTHER_PROFILE_RUNNING  # noqa: E402
from dayz_serverman.bridge.contracts import ErrorCode  # noqa: E402
from dayz_serverman.cli import exit_codes  # noqa: E402
from dayz_serverman.cli.exit_codes import (  # noqa: E402
    CLI_CODES, FIXED_CODES, REFUSAL_CODES, dispatch_exit, pre_change, record_exit,
)
from dayz_serverman.cli.flow import STATE_CODES  # noqa: E402

# The CLI package, scanned for the codes it emits
CLI_ROOT = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python" / "dayz_serverman" / "cli"

# The 6.4 table: code -> (exit at dispatch, exit in a FAILED record before / after the first change)
TABLE: dict[str, tuple[int | None, int, int]] = {
    "INVALID_REQUEST": (2, 2, 1), "CONTRACT_VERSION_UNSUPPORTED": (1, 1, 1), "NOT_FOUND": (2, 2, 1),
    "REVISION_CONFLICT": (3, 3, 1), "CONTROL_CONFLICT": (3, 3, 1), "EXTERNAL_PROCESS": (3, 3, 1),
    "PROCESS_STATE_UNKNOWN": (3, 3, 1), "LAUNCH_FAILED": (1, 1, 1), "STOP_METHOD_UNPROVEN": (1, 1, 1),
    "GAMEPLAY_NOT_ENABLED": (3, 3, 1), "RUNTIME_PROFILE_UNRESOLVED": (3, 3, 1),
    "PENDING_RUNTIME_PROFILE_SUPPORT": (3, 3, 1), "PROFILE_CONTEXT_MISMATCH": (3, 3, 1),
    "UNSUPPORTED_SNAPSHOT_CONTENT": (3, 3, 1), "MIGRATION_CONFLICT": (3, 3, 1), "SOURCE_CHANGED": (3, 3, 1),
    "RECOVERY_REQUIRED": (6, 6, 6), "PATH_INVALID": (2, 2, 1), "PATH_OUTSIDE_ALLOWED_ROOT": (2, 2, 1),
    "STORAGE_FAILURE": (1, 1, 1), "STEAMCMD_UNAVAILABLE": (3, 3, 1), "AUTHENTICATION_REQUIRED": (3, 3, 1),
    "AUTHENTICATION_FAILED": (1, 1, 1), "ENTITLEMENT_DENIED": (1, 1, 1), "CONNECTION_FAILED": (1, 1, 1),
    "WORKSHOP_CONTENT_FAILED": (1, 1, 1), "CACHE_VERIFICATION_FAILED": (1, 1, 1), "UPDATE_RESULT_UNKNOWN": (1, 1, 1),
    "UPDATE_CANCELLED": (5, 5, 5), "PUBLICATION_REQUIRED": (3, 3, 1), "PUBLICATION_PREVIEW_STALE": (3, 3, 1),
    "PUBLICATION_FAILED": (1, 1, 1), "OPERATION_NOT_CANCELLABLE": (None, 1, 1), "EVENT_CURSOR_EXPIRED": (1, 1, 1),
    "INTERNAL_FAILURE": (1, 1, 1),
    # Operation codes that are not ErrorCode members
    "DELETION_BLOCKED": (3, 3, 3), "STEAMCMD_BUSY": (3, 3, 1), "STEAMCMD_PATH_CHANGED": (3, 3, 1),
    "STOP_REQUEST_FAILED": (1, 1, 1), "STOP_TIMEOUT": (1, 1, 1), "PROVISION_FAILED": (1, 1, 1),
    "BACKUP_UNAVAILABLE": (1, 1, 1), "PUBLICATION_STAGE_FAILED": (1, 1, 1),
    "PUBLICATION_VERIFICATION_FAILED": (1, 1, 1), "WORKSHOP_MANIFEST_INVALID": (1, 1, 1),
    "STEAMCMD_EXIT_UNPROVEN": (1, 1, 1), "PROCESS_OWNERSHIP_UNPROVEN": (1, 1, 1), "SOMETHING_NEW": (1, 1, 1),
}


def failed(kind: str, code: str, *, percent: int = 0, phase: str | None = None) -> dict:
    """Return a FAILED operation record with the evidence fields of 6.5."""
    return {"kind": kind, "state": "FAILED", "progress_percent": percent, "last_working_phase": phase,
            "terminal_error": {"code": code, "message": "refused", "retryable": False}}


class DispatchExitTests(unittest.TestCase):
    """Left column of 6.4: a code that dispatch returned, so nothing was submitted."""

    def test_every_error_code_has_a_row(self) -> None:
        """Every ErrorCode member is in the table, so a new code fails here until it gets its class."""
        # MUTATION_CONFLICT has its own rows by cause (test_mutation_conflict_by_its_cause)
        self.assertEqual({code.value for code in ErrorCode} - set(TABLE), {"MUTATION_CONFLICT"})

    def test_one_case_per_row(self) -> None:
        """Each code maps to its exit code at dispatch."""
        for code, (expected, _before, _after) in TABLE.items():
            with self.subTest(code=code):
                self.assertEqual(dispatch_exit({"code": code, "message": "host text"}), expected)

    def test_d11_refusal_is_3_and_a_plain_invalid_request_is_2(self) -> None:
        """Criterion 26: the running-profile refusal exits 3, by the exact message of the constant (QF-9)."""
        self.assertEqual(dispatch_exit({"code": "INVALID_REQUEST", "message": OTHER_PROFILE_RUNNING}), 3)
        self.assertEqual(dispatch_exit({"code": "INVALID_REQUEST", "message": "profile_id is invalid"}), 2)

    def test_mutation_conflict_by_its_cause(self) -> None:
        """A full queue or no cause is 3, a recovery block 6, a closing lane 1."""
        cases = {"QUEUE_FULL": 3, None: 3, "RECOVERY_BLOCK": 6, "SHUTTING_DOWN": 1}
        for reason, expected in cases.items():
            details = {} if reason is None else {"reason": reason}
            with self.subTest(reason=reason):
                self.assertEqual(dispatch_exit({"code": "MUTATION_CONFLICT", "details": details}), expected)
        self.assertEqual(dispatch_exit({"code": "MUTATION_CONFLICT"}), 3)

    def test_cli_codes(self) -> None:
        """The closed list of A11 (QF-62): USAGE 2; INSTANCE_ACTIVE, SETUP_REQUIRED, NOTHING_TO_CONVERT 3;
        CONFIRMATION_REQUIRED, NOT_INTERACTIVE 4; CANCELLED 5; INSTANCE_LOCK_UNSUPPORTED, CHECK_NOT_FINISHED,
        NOT_READY 1."""
        self.assertEqual(CLI_CODES, {"USAGE": 2, "INSTANCE_ACTIVE": 3, "SETUP_REQUIRED": 3, "NOTHING_TO_CONVERT": 3,
                                     "CONFIRMATION_REQUIRED": 4, "NOT_INTERACTIVE": 4, "CANCELLED": 5,
                                     "INSTANCE_LOCK_UNSUPPORTED": 1, "CHECK_NOT_FINISHED": 1, "NOT_READY": 1})
        for code, expected in CLI_CODES.items():
            self.assertEqual(dispatch_exit({"code": code}), expected)

    def test_the_cli_emits_exactly_the_a11_codes(self) -> None:
        """Every `CliFailure` with a literal code outside the bridge and operation codes uses an A11 code with
        its A11 exit, and every A11 code is emitted somewhere."""
        known = {member.value for member in ErrorCode} | set(REFUSAL_CODES) | set(FIXED_CODES)
        emitted: dict[str, set[int]] = {}
        for path in CLI_ROOT.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CliFailure"
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    continue
                code = str(node.args[0].value)
                if code in known:
                    continue
                exit_node = node.args[2] if len(node.args) > 2 else None
                self.assertIsInstance(exit_node, ast.Name, (path.name, code))
                emitted.setdefault(code, set()).add(getattr(exit_codes, exit_node.id))
        self.assertEqual({code: {number} for code, number in CLI_CODES.items()}, emitted)
        # Codes passed on by name: the end states of a record are A11 or bridge codes
        self.assertLessEqual(set(STATE_CODES.values()), set(CLI_CODES) | known)


class RecordExitTests(unittest.TestCase):
    """Right column of 6.4 and the end states: the record decides, with the pre-change evidence of 6.5."""

    def test_end_states(self) -> None:
        """SUCCEEDED 0, CANCELLED 5, RECOVERY_REQUIRED 6 whatever the code."""
        self.assertEqual(record_exit({"kind": "CREATE_BACKUP", "state": "SUCCEEDED"}), 0)
        self.assertEqual(record_exit({"kind": "CREATE_BACKUP", "state": "CANCELLED"}), 5)
        recovery = failed("CREATE_BACKUP", "CONTROL_CONFLICT") | {"state": "RECOVERY_REQUIRED"}
        self.assertEqual(record_exit(recovery), 6)

    def test_one_case_per_row_before_and_after_the_first_change(self) -> None:
        """A refusal keeps its class only before the first change; a fixed code keeps it always."""
        for code, (_dispatch, before, after) in TABLE.items():
            with self.subTest(code=code):
                self.assertEqual(record_exit(failed("CREATE_BACKUP", code, phase=None)), before)
                self.assertEqual(record_exit(failed("CREATE_BACKUP", code, phase="DISCOVER", percent=5)), after)


class PreChangeEvidenceTests(unittest.TestCase):
    """6.5: one case per kind, including each first-change marker; the code alone never decides."""

    def test_marker_kinds_by_percent(self) -> None:
        """Below the marker percent nothing changed; at the marker the first change starts."""
        markers = {("START_SERVER", "preflight"): 11, ("STOP_SERVER", "STOP_SERVER"): 21,
                   ("APPLY_MODS_AND_RESTART", "STOP_SERVER"): 9}
        for (kind, phase), marker in markers.items():
            with self.subTest(kind=kind):
                self.assertTrue(pre_change(failed(kind, "EXTERNAL_PROCESS", percent=marker - 1, phase=phase)))
                self.assertFalse(pre_change(failed(kind, "EXTERNAL_PROCESS", percent=marker, phase=phase)))
                self.assertEqual(record_exit(failed(kind, "EXTERNAL_PROCESS", percent=marker - 1, phase=phase)), 3)
                self.assertEqual(record_exit(failed(kind, "EXTERNAL_PROCESS", percent=marker, phase=phase)), 1)
        # Apply and restart: the A13 refusal at preflight (3 percent) exits 3
        self.assertEqual(record_exit(failed("APPLY_MODS_AND_RESTART", "CONTROL_CONFLICT", percent=3,
                                            phase="preflight")), 3)

    def test_restart_with_and_without_backup(self) -> None:
        """Criterion 25: without a backup the marker is 12; with a backup it is 16 (STOP_SERVER at 15 is before)."""
        stop_check = failed("RESTART_SERVER", "EXTERNAL_PROCESS", percent=10, phase="preflight")
        self.assertEqual(record_exit(stop_check), 3)
        start_refused = failed("RESTART_SERVER", "EXTERNAL_PROCESS", percent=12, phase="preflight")
        self.assertEqual(record_exit(start_refused), 1)
        self.assertEqual(record_exit(failed("RESTART_SERVER", "CONTROL_CONFLICT", percent=12, phase="preflight")), 1)
        before_stop = failed("RESTART_SERVER", "EXTERNAL_PROCESS", percent=15, phase="STOP_SERVER")
        self.assertEqual(record_exit(before_stop, backup_after_stop=True), 3)
        self.assertEqual(record_exit(before_stop), 1)
        after_backup = failed("RESTART_SERVER", "EXTERNAL_PROCESS", percent=98, phase="START_SERVER")
        self.assertEqual(record_exit(after_backup, backup_after_stop=True), 1)

    def test_delete_profile_marker(self) -> None:
        """DELETE_PROFILE: percent 0 is before the first change; the marker sets 1."""
        self.assertEqual(record_exit(failed("DELETE_PROFILE", "CONTROL_CONFLICT", percent=0)), 3)
        self.assertEqual(record_exit(failed("DELETE_PROFILE", "CONTROL_CONFLICT", percent=1, phase="running")), 1)
        self.assertEqual(record_exit(failed("DELETE_PROFILE", "DELETION_BLOCKED", percent=0)), 3)

    def test_phase_kinds(self) -> None:
        """Kinds judged by the last working phase: each listed phase is before, the next phase is after."""
        cases = {
            "PUBLISH_MODS_AND_KEYS": ((None, "PUBLICATION_PREFLIGHT", "DISCOVER_ITEM", "CACHE_PROOF_RECHECK",
                                      "STAGE_TARGET", "CHECK_TARGET", "COPY_FILE", "COPY_KEY"),
                                     ("BEFORE_PUBLICATION", "AFTER_LIVE_TARGET", "START_SERVER")),
            "RESTORE_BACKUP": ((None, "VERIFY_SOURCE", "PREPARE_RECOVERY", "STAGE_TARGETS"), ("WRITE_JOURNAL",)),
            "RESTORE_PROFILE_FROM_BACKUP": ((None,), ("VERIFYING_BACKUP", "PREPARING", "PREPARED", "PUBLISHING")),
            "APPLY_CONFIGURATION": ((None, "loaded"), ("validated", "verified")),
            "APPLY_MISSION_CONFIGURATION": ((None, "loaded"), ("validated", "published")),
            "CONVERT_STARTER_LOADOUT": ((None, "loaded"), ("validated",)),
            "APPLY_MEDICAL_FEATURE": ((None,), ("loaded", "validated")),
            "CREATE_BACKUP": ((None,), ("DISCOVER", "PUBLISH")),
            "VERIFY_WORKSHOP_FILES": ((None,), ("verify_source",)),
            "AUTHENTICATE_STEAMCMD": ((None, "preflight", "wait_steamcmd"), ("interactive_authentication",)),
            "UPDATE_WORKSHOP_ITEMS": ((None, "preflight", "wait_steamcmd", "resolve_items"),
                                      ("check_remote", "download")),
        }
        for kind, (before, after) in cases.items():
            for phase in before:
                with self.subTest(kind=kind, phase=phase):
                    self.assertTrue(pre_change(failed(kind, "CONTROL_CONFLICT", phase=phase)))
            for phase in after:
                with self.subTest(kind=kind, phase=phase):
                    self.assertFalse(pre_change(failed(kind, "CONTROL_CONFLICT", phase=phase)))

    def test_kinds_without_evidence_always_exit_1(self) -> None:
        """Saves, provisioning and legacy import cannot show the side of the first change: every refusal is 1."""
        for kind in ("PROVISION_PROFILE", "SAVE_PROFILE", "SAVE_SETTINGS", "SAVE_STEAM_SETTINGS", "IMPORT_LEGACY",
                     "UNKNOWN_KIND"):
            for code in ("REVISION_CONFLICT", "NOT_FOUND", "CONTROL_CONFLICT", "INVALID_REQUEST"):
                with self.subTest(kind=kind, code=code):
                    self.assertEqual(record_exit(failed(kind, code, phase=None)), 1)
            self.assertEqual(record_exit(failed(kind, "RECOVERY_REQUIRED")), 6)


if __name__ == "__main__":
    unittest.main()
