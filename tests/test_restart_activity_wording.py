"""Manager activity wording of "apply mods and restart" and of an apply with a start, per end point."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application import activity_wording as wording  # noqa: E402
from dayz_serverman.application.log_activity import format_manager_record  # noqa: E402

# Text of an internal fault, as both catalogues word it
INTERNAL = "Something went wrong inside DayZ-ServerMan. Details are in Logs, Manager diagnostics."


def activity(kind: str, state: str, phase: str | None, **fields: object) -> str:
    """Return the activity sentence of the end of one operation."""
    line = format_manager_record({
        "event": "operation.state", "level": "ERROR", "occurred_at": "2026-10-03T10:00:00Z",
        "fields": {"kind": kind, "state": state, "last_working_phase": phase, **fields}})
    return line.split("  ", 2)[2].strip() if line else ""


def restart(state: str, phase: str | None, **fields: object) -> str:
    """Return the activity sentence of the end of one restart operation."""
    return activity("APPLY_MODS_AND_RESTART", state, phase, **fields)


class RestartActivityWordingTests(unittest.TestCase):
    """Each end of the restart names only what is proven at that point."""

    def test_apply_and_restart_names_what_each_point_leaves_behind(self) -> None:
        """A failed or cancelled restart is worded by the phase that worked last; success is neutral."""
        texts = wording.RESTART_APPLY_TEXTS
        self.assertEqual(restart("CANCELLED", None), texts["cancelledRunning"])
        self.assertEqual(restart("CANCELLED", "preflight"), texts["cancelledRunning"])
        self.assertEqual(restart("CANCELLED", "BACKUP_HASH"), texts["cancelledStopped"])
        self.assertEqual(restart("CANCELLED", "STAGE_TARGET"), texts["cancelledStopped"])
        for phase, key in (("preflight", "preflight"), ("STOP_SERVER", "stop"), ("BACKUP_STAGE", "backup"),
                           ("COPY_FILE", "apply"), ("VERIFY_BEFORE_START", "check"), ("START_SERVER", "start")):
            self.assertEqual(restart("FAILED", phase), texts[key])
        self.assertEqual(restart("RECOVERY_REQUIRED", "AFTER_LIVE_TARGET"),
                         f"{texts['applyBlocked']} Changes are blocked.")
        self.assertEqual(restart("FAILED", None), "Applying mods and restarting the server did not finish.")
        self.assertEqual(restart("SUCCEEDED", "START_SERVER"),
                         "Applying mods and restarting finished. The result is on the Mods page.")

    def test_failed_check_before_the_start_is_not_told_as_a_copy_failure(self) -> None:
        """The check sentence stands alone; the same code in a copy phase keeps its text."""
        texts = wording.RESTART_APPLY_TEXTS
        failed = {"error_code": "PUBLICATION_FAILED", "error_message": "Published mods or keys changed before start."}
        self.assertEqual(restart("FAILED", "VERIFY_BEFORE_START", **failed), texts["check"])
        self.assertEqual(activity("PUBLISH_MODS_AND_KEYS", "FAILED", "VERIFY_BEFORE_START", **failed),
                         texts["startCheck"])
        self.assertEqual(activity("PUBLISH_MODS_AND_KEYS", "FAILED", "COPY_FILE", **failed),
                         "The mods could not be applied. The mods could not be copied to the server folder.")

    def test_failed_start_handoff_claims_neither_a_changed_folder_nor_a_stopped_server(self) -> None:
        """QF-021: last phase START_SERVER has its own sentence in both kinds, with the host message."""
        text = wording.RESTART_APPLY_TEXTS["start"]
        self.assertEqual(text, "Mods applied, but the server start did not succeed or could not be confirmed. "
                               "Check the server state on Overview.")
        internal = {"error_code": "INTERNAL_FAILURE", "error_message": "Publication processing failed."}
        for kind in ("APPLY_MODS_AND_RESTART", "PUBLISH_MODS_AND_KEYS"):
            line = activity(kind, "FAILED", "START_SERVER", **internal)
            self.assertEqual(line, f"{text} {INTERNAL}", kind)
            self.assertNotIn("folder changed", line)
            self.assertNotIn("stays stopped", line)
            # A host message that is a sentence is shown after it
            self.assertEqual(
                activity(kind, "FAILED", "START_SERVER", error_code="STORAGE_FAILURE",
                         error_message="Launch evidence could not be recorded safely."),
                f"{text} Launch evidence could not be recorded safely.", kind)
        # The sentence of the changed folder stays with the check before the start only
        self.assertNotEqual(wording.restart_apply_text("FAILED", "START_SERVER"),
                            wording.restart_apply_text("FAILED", "VERIFY_BEFORE_START"))

    def test_guard_refusal_after_the_stop_does_not_contradict_itself(self) -> None:
        """A refused apply names the changed state once; "Server stopped, but" is not said with it."""
        texts = wording.RESTART_APPLY_TEXTS
        for code, state, words in (("CONTROL_CONFLICT", "STARTING", "starting"),
                                   ("CONTROL_CONFLICT", "RUNNING_MANAGED", "running"),
                                   ("EXTERNAL_PROCESS", "RUNNING_EXTERNAL", "running outside DayZ-ServerMan"),
                                   ("PROCESS_STATE_UNKNOWN", "UNKNOWN", "in an unknown state")):
            line = restart("FAILED", "CACHE_PROOF_RECHECK", error_code=code,
                           error_message=f"Applying mods requires STOPPED; current state is {state}.")
            self.assertEqual(line, f"{texts['applyRefused']} This cannot be done while the server is {words}.")
            self.assertNotIn("Server stopped, but", line)
        # A busy installation names no state: the server is still stopped, and the apply sentence is true
        self.assertEqual(
            restart("FAILED", "CACHE_PROOF_RECHECK", error_code="CONTROL_CONFLICT",
                    error_message="Another manager controls this DayZ installation."),
            f"{texts['apply']} Another DayZ-ServerMan is using this DayZ installation. Close it and try again.")
        # Another failure whose message names a state is not a guard refusal
        self.assertEqual(wording.restart_apply_text("FAILED", "COPY_FILE", "PUBLICATION_FAILED", "state STARTING"),
                         texts["apply"])

    def test_restart_of_a_stopped_server_is_not_told_as_a_failed_stop(self) -> None:
        """A stop refused on a stopped server: one true sentence, without the state sentence."""
        texts = wording.RESTART_APPLY_TEXTS
        self.assertEqual(
            restart("FAILED", "STOP_SERVER", error_code="CONTROL_CONFLICT",
                    error_message="Cannot stop while server state is STOPPED."), texts["stopNotRunning"])
        self.assertNotIn("could not be stopped", texts["stopNotRunning"])
        # Every other refused or failed stop keeps the stop sentence
        self.assertEqual(
            restart("FAILED", "STOP_SERVER", error_code="EXTERNAL_PROCESS",
                    error_message="Cannot stop while server state is RUNNING_EXTERNAL."),
            f"{texts['stop']} This cannot be done while the server is running outside DayZ-ServerMan.")
        self.assertEqual(
            restart("FAILED", "STOP_SERVER", error_code="STOP_TIMEOUT", error_message="x"),
            f"{texts['stop']} DayZ did not close in time. Check the server window, then try again.")


if __name__ == "__main__":
    unittest.main()
