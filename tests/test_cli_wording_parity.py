"""Task 2.5, design 11.2 and 11.3: the Python phase wording equals the frontend; the CLI way out covers every page."""

from __future__ import annotations

import inspect
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application import activity_wording, phase_wording  # noqa: E402
from dayz_serverman.cli import read_wording, wording  # noqa: E402
from dayz_serverman.cli.output import Label, Line, line_text  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
# Names of the GUI pages that a CLI text must not send the operator to (11.3)
PAGES = re.compile(r"\b(Overview|Mods|Backups|Profiles|Configuration|Tweaks|Logs|Settings)\b")


def table(source: str, name: str) -> str:
    """Return the body of one `const name = Object.freeze({...});` table."""
    return source.split(f"const {name} = Object.freeze({{", 1)[1].split("});", 1)[0]


def catalogue_texts() -> set[str]:
    """Return every operator text of the Python catalogue that the CLI can print."""
    texts = {activity_wording.INTERNAL_TEXT, activity_wording.REQUEST_TEXT, activity_wording.RECOVERY_TEXT,
             activity_wording.QUEUE_FULL_TEXT, activity_wording.CLOSING_TEXT, activity_wording.BLOCKED_TEXT,
             activity_wording.BLOCK_FALLBACK, activity_wording.BLOCK_RESTART_ACTION,
             activity_wording.BLOCK_RESTORE_ACTION}
    texts.update(activity_wording.ERROR_TEXTS.values())
    texts.update(text for own in activity_wording.KIND_ERROR_TEXTS.values() for text in own.values())
    texts.update(sentence for _fragment, sentence in activity_wording.BLOCK_REASONS)
    texts.update(sentence for _fragment, sentence in activity_wording.REQUEST_TEXTS)
    texts.update(activity_wording.RESTART_APPLY_TEXTS.values())
    texts.update(activity_wording.NEUTRAL_SUCCESS.values())
    texts.update(text for texts_of_kind in activity_wording.KIND_TEXTS.values() for text in texts_of_kind)
    # The copied texts of the read commands (2.6)
    texts.update(read_wording.BUILD_FAILURE_REASONS.values())
    texts.update(read_wording.BUILD_UNKNOWN_REASONS.values())
    texts.update(read_wording.BUILD_PENDING_TEXTS.values())
    texts.update(text.replace("{l}", "location") for pair in read_wording.PATH_STATUS_TEXTS.values() for text in pair)
    texts.add(read_wording._build_check_reason({"reason": "STEAMCMD_NOT_CONFIGURED"}, True))
    return texts


def names_a_page(text: str) -> bool:
    """Report whether a text names a GUI page; a page word that starts a sentence is ordinary prose."""
    for match in PAGES.finditer(text):
        before = text[:match.start()].rstrip()
        if before and before[-1] not in ".!?":
            return True
    return False


class PhaseWordingParityTests(unittest.TestCase):
    """`application/phase_wording.py` equals `operation_labels.js` and `overview_readiness.js`."""

    @classmethod
    def setUpClass(cls) -> None:
        """Read both frontend catalogues."""
        cls.labels = (FRONTEND / "operation_labels.js").read_text(encoding="utf-8")
        cls.readiness = (FRONTEND / "overview_readiness.js").read_text(encoding="utf-8")

    def test_phase_rows_are_equal(self) -> None:
        """Every "kind/phase" row has the same text and the same determinate flag."""
        rows = re.findall(r'^  "([^"]+)": \["([^"]+)", (true|false)\],$', table(self.labels, "operationPhaseLabels"),
                          re.MULTILINE)
        self.assertEqual({key: (text, flag == "true") for key, text, flag in rows}, phase_wording.PHASE_TEXTS)

    def test_lookup_tables_are_equal(self) -> None:
        """Fallback, terminal phases, lifecycle kinds and borrowed phases are the same."""
        self.assertIn('const operationPhaseFallback = Object.freeze(["Working", false]);', self.labels)
        self.assertEqual(phase_wording.PHASE_FALLBACK, ("Working", False))
        self.assertIn(f"const operationTerminalPhases = Object.freeze({json.dumps(list(phase_wording.TERMINAL_PHASES))});",
                      self.labels)
        self.assertIn(f"const operationLifecycleKinds = Object.freeze({json.dumps(list(phase_wording.LIFECYCLE_KINDS))});",
                      self.labels)
        borrowed = re.findall(r'^  ([A-Z_]+): \[(.*)\],$', table(self.labels, "operationBorrowedPhases"), re.MULTILINE)
        self.assertEqual({kind: tuple(re.findall(r'"([A-Z_]+)"', kinds)) for kind, kinds in borrowed},
                         phase_wording.BORROWED_PHASES)

    def test_state_and_cancelling_texts_are_equal(self) -> None:
        """Operation state labels, their fallback, and the cancelling lines are the same."""
        states = table(self.labels, "operationStateLabels")
        self.assertEqual(dict(re.findall(r'([A-Z_]+): "([^"]+)"', states)), phase_wording.STATE_TEXTS)
        self.assertIn(f'"{phase_wording.STATE_FALLBACK}"', self.labels)
        cancelling = table(self.labels, "operationCancellingTexts")
        self.assertEqual(dict(re.findall(r'^  ([A-Z_]+): "([^"]+)",$', cancelling, re.MULTILINE)),
                         phase_wording.CANCELLING_TEXTS)
        self.assertIn(f'"{phase_wording.CANCELLING_FALLBACK}"', self.labels)

    def test_server_status_texts_are_equal(self) -> None:
        """The Overview's readiness and state rows have the same label and explanation."""
        found = {state: (label, text) for state, label, text in
                 re.findall(r'([A-Z_]+): \[\s*"([^"]+)", "[a-z-]+",\s*"([^"]+)",?\s*\]', self.readiness)}
        expected = {**phase_wording.READINESS_TEXTS, **phase_wording.SERVER_STATE_TEXTS}
        self.assertEqual(found, expected)
        for label, text in (phase_wording.READINESS_FALLBACK, phase_wording.SERVER_STATE_FALLBACK):
            self.assertRegex(self.readiness, rf'\["{re.escape(label)}", "[a-z-]+", "{re.escape(text)}"\]')

    def test_status_text_follows_the_overview_rules(self) -> None:
        """Readiness refines only a managed running server; unknown values get the fallbacks."""
        self.assertEqual(phase_wording.server_status_text({"state": "RUNNING_MANAGED", "readiness": "READY"})[0],
                         "Ready")
        self.assertEqual(phase_wording.server_status_text({"state": "RUNNING_MANAGED"}),
                         phase_wording.READINESS_FALLBACK)
        self.assertEqual(phase_wording.server_status_text({"state": "STOPPED", "readiness": "READY"})[0], "Stopped")
        self.assertEqual(phase_wording.server_status_text(None), phase_wording.SERVER_STATE_FALLBACK)

    def test_python_texts_hold_no_identifier(self) -> None:
        """No phase, state, cancelling or status text leaks an identifier."""
        texts = [text for text, _flag in phase_wording.PHASE_TEXTS.values()]
        texts += list(phase_wording.STATE_TEXTS.values()) + list(phase_wording.CANCELLING_TEXTS.values())
        texts += [text for pair in (*phase_wording.READINESS_TEXTS.values(),
                                    *phase_wording.SERVER_STATE_TEXTS.values()) for text in pair]
        for text in texts:
            self.assertFalse(activity_wording.leaks_identifier(text), text)


class WayOutTests(unittest.TestCase):
    """11.3: every catalogue text that names a GUI page reads with a CLI command instead."""

    def test_every_page_sentence_is_covered(self) -> None:
        """After the way-out table, no catalogue text names a GUI page."""
        covered = 0
        for text in sorted(catalogue_texts()):
            if names_a_page(text):
                covered += 1
                with self.subTest(text=text):
                    self.assertFalse(names_a_page(line_text(wording.way_out(text))), line_text(wording.way_out(text)))
        self.assertGreaterEqual(covered, 15)

    def test_every_row_is_used(self) -> None:
        """Each row of the table matches at least one catalogue text, so no row is stale."""
        texts = catalogue_texts()
        for needle, _replacement in wording.WAY_OUT:
            with self.subTest(needle=needle):
                self.assertTrue(any(needle in text for text in texts))

    def test_way_out_keeps_the_rest_and_types_the_command(self) -> None:
        """The replacement is exact: the rest of the text stays, the command is text to type."""
        line = wording.way_out(activity_wording.RECOVERY_TEXT)
        self.assertEqual(line_text(line), "An earlier operation did not finish cleanly. Changes are blocked until it "
                                          "is resolved. Details: run logs --source diagnostics.")
        self.assertEqual([part.text for part in line.parts if not isinstance(part, Label)],
                         ["logs --source diagnostics"])

    def test_cli_sentences_hold_no_identifier_in_prose(self) -> None:
        """Every prose part of the CLI-only sentences passes the raw-identifier check."""
        lines: list[Line] = [wording.way_out(text) for text in catalogue_texts()]
        lines += [wording.instance_active(None), wording.no_profile_named("x"), wording.no_profile_exists(),
                  wording.name_the_profile("Chernarus PvE"), wording.unknown_backup("b"), wording.unknown_key("k", "c"),
                  wording.duplicate_key("k"), wording.invalid_set("v"), wording.invalid_file("f"),
                  wording.overwrite_needs_replace(), wording.profile_ambiguous("x", ["a", "b"]),
                  wording.usage("bad", "server start")]
        # Templates are checked through their builders above (instance_active)
        constants = [value for name, value in inspect.getmembers(wording)
                     if name.isupper() and isinstance(value, str) and "{" not in value]
        for text in [part.text for line in lines for part in line.parts if isinstance(part, Label)] + constants:
            with self.subTest(text=text):
                self.assertFalse(activity_wording.leaks_identifier(text))


if __name__ == "__main__":
    unittest.main()
