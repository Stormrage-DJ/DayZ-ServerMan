"""Phase 5 wording (11.2): every copy of a Mods page text in the CLI appears unchanged in its frontend file.

Copies that the window builds with a template keep their fixed parts; each part is checked. The
sentences of `cli/mods_wording.py` that name a command follow the input-name rules of 11.3.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from test_cli_input_names import InputNameChecker, flags  # noqa: E402
from dayz_serverman.application import review_wording  # noqa: E402
from dayz_serverman.cli import mods_wording as texts  # noqa: E402
from dayz_serverman.cli.output import sentence  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"


def source(name: str) -> str:
    """Return one frontend file with each string literal that the file joins with `+` written as one literal."""
    return re.sub(r'"\s*\+\s*"', "", (FRONTEND / name).read_text(encoding="utf-8"))


class CopyTests(unittest.TestCase):
    """Each copy, by the file that the copy names."""

    def assert_in_file(self, name: str, *copies: str) -> None:
        """Every copy appears in the frontend file."""
        text = source(name)
        for copy in copies:
            with self.subTest(file=name, copy=copy):
                self.assertIn(copy, text)

    def test_update_texts(self) -> None:
        """`modsUpdateTexts`, the unconfirmed-update detail and the window's restart hint without its page."""
        self.assert_in_file("mods_update_actions.js", *texts.UPDATE_TEXTS.values(),
                            " SteamCMD exited without a verifiable update result",
                            "All mods are current. The server was not restarted. To restart it anyway, ")

    def test_operation_results(self) -> None:
        """Download results and the start outcomes of an apply (`successPresentation`, `restartApplySuccess`)."""
        self.assert_in_file("operation_messages.js", *texts.DOWNLOAD_RESULTS.values(), *texts.START_TEXTS.values(),
                            texts.START_FAILED, texts.RESTART_START_CANCELLED, texts.RESTART_START_FAILED,
                            texts.RESTART_BACKED_UP)

    def test_outcome_and_verify_labels(self) -> None:
        """Per-mod outcomes of an update and the problems and summaries of a verification."""
        self.assert_in_file("diagnostic_labels.js", *texts.OUTCOME_TEXTS.values(), *texts.OUTCOME_DETAILS.values(),
                            f'"{texts.OUTCOME_FALLBACK}"')
        self.assert_in_file("mods_verify.js", *texts.VERIFY_SOURCE_PROBLEMS.values(),
                            *texts.VERIFY_TARGET_PROBLEMS.values(), texts.VERIFY_NOTHING, texts.VERIFY_ONE,
                            texts.VERIFY_ALL[1], texts.VERIFY_PROBLEM, texts.VERIFY_PROBLEMS)

    def test_apply_review(self) -> None:
        """`modReviewTexts`, the named states and the key lines of the apply review; advice without its buttons."""
        self.assert_in_file("mod-publication.js", *review_wording.MOD_REVIEW_TEXTS.values(),
                            *review_wording.MOD_REVIEW_SERVER_STATES.values(),
                            review_wording.MOD_REVIEW_STATE_UNCONFIRMED, review_wording.MOD_KEYS_ADDED,
                            review_wording.MOD_KEYS_IN_PLACE, " Stop the server first, then update again.",
                            " Wait until the server is stopped, then update again.",
                            " To be safe, apply the mods when the server is stopped.", ", or stop the server first.",
                            " Wait until the server runs, then use ")

    def test_summaries(self) -> None:
        """The verification summary counts the mods with problems, as the Mods page does."""
        clean = {"source_state": "MATCHES_RECORD", "target_state": "MATCHES_SOURCE"}
        self.assertEqual(texts.verify_summary([]), texts.VERIFY_NOTHING)
        self.assertEqual(texts.verify_summary([clean]), texts.VERIFY_ONE)
        self.assertEqual(texts.verify_summary([clean, clean]), "All 2 mods verified; server copies match")
        missing = {"source_state": "MISSING", "target_state": "FAILED"}
        self.assertEqual(texts.verify_problems(missing), ["Download missing"])
        self.assertEqual(texts.verify_summary([missing, {**missing, "target_state": "DIFFERS"}]), "2 problems found")
        self.assertEqual(texts.outcome_text({"outcome": "UNKNOWN_FAILED", "error_code": "CACHE_MANIFEST_ID_MISSING"}),
                         "Could not verify (Download record is incomplete)")


class SentenceTests(unittest.TestCase):
    """Criterion 18: the CLI forms name commands only as text to type."""

    def test_cli_forms(self) -> None:
        """Advice, way outs and sentences of the update and Steam commands."""
        checker = InputNameChecker(self, flags(), [])
        lines = [texts.current_running(), texts.choose_sign_in(), texts.backup_needs_restart(),
                 sentence(*texts.sign_in_again())]
        for state in ("RUNNING_MANAGED", "RUNNING_EXTERNAL", "STARTING", "STOPPING", "UNKNOWN"):
            lines += [sentence(*texts.refused_advice(state, True)), sentence(*texts.refused_advice(state, False))]
        lines += [sentence(*texts.use_restart(True)), sentence(*texts.use_restart(False))]
        for line in lines:
            with self.subTest(line=line):
                checker.blocks([line])


if __name__ == "__main__":
    unittest.main()
