"""Criterion 31 (QF-40): a profile restore while a server runs gives the state sentence of backup restore.

The refusal keeps the code CONTROL_CONFLICT; its message names the server state, so the catalogue
(`activity_wording.error_text`, sibling of the window's `operationErrorText`) gives the sentence
"This cannot be done while the server is <state word>." and not the text of a second manager.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.activity_wording import ERROR_TEXTS, error_text  # noqa: E402
from dayz_serverman.application.profile_restores import ProfileRestoreService  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerState  # noqa: E402

# Server states other than STOPPED and the state words of the catalogue
STATES = {ServerState.RUNNING_MANAGED: "running", ServerState.RUNNING_EXTERNAL: "running outside DayZ-ServerMan",
          ServerState.STARTING: "starting", ServerState.UNKNOWN: "in an unknown state"}


def refusal(state: ServerState) -> LifecycleFailure:
    """Return the refusal of the profile restore's stopped check in one server state."""
    lifecycle = SimpleNamespace(status=lambda: SimpleNamespace(state=state))
    service = ProfileRestoreService(None, None, None, None, lifecycle, None, frozenset)  # type: ignore[arg-type]
    try:
        service.require_stopped()
    except LifecycleFailure as failure:
        return failure
    raise AssertionError(f"no refusal in {state}")


class RunningServerRefusalTests(unittest.TestCase):
    """The profile restore's refusal reads like backup restore's, in every state but STOPPED."""

    def test_state_sentence_and_code(self) -> None:
        """CONTROL_CONFLICT with the backup restore's state sentence, never the second-manager text."""
        for state, words in STATES.items():
            with self.subTest(state=state):
                failure = refusal(state)
                self.assertEqual(failure.code, "CONTROL_CONFLICT")
                text = error_text(failure.code, failure.safe_message)
                self.assertEqual(text, f"This cannot be done while the server is {words}.")
                backup_restore = error_text("CONTROL_CONFLICT",
                                            f"Restore requires STOPPED; current state is {state.value}.")
                self.assertEqual(text, backup_restore)
                self.assertNotEqual(text, ERROR_TEXTS["CONTROL_CONFLICT"])

    def test_stopped_passes(self) -> None:
        """A proven stopped server is no refusal."""
        lifecycle = SimpleNamespace(status=lambda: SimpleNamespace(state=ServerState.STOPPED))
        ProfileRestoreService(None, None, None, None, lifecycle, None, frozenset).require_stopped()  # type: ignore


if __name__ == "__main__":
    unittest.main()
