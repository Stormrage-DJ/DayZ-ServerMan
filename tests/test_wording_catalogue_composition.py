"""The split of the wording catalogues (leaf 8.14): which document composes which wording script.

`operation_messages.js` publishes `result: operationResult`, and `operationResult` is declared in
`operation_results.js`. The export resolves only when both scripts are in the same script element. So
each document that loads the messages also loads the results. The editor document also loads the
failures (Architect 2026-10-15 15:46:42 in evidence 8.10). The Python catalogue re-exports its moved
result texts.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest

try:
    from tests.ui_harness_support import FRONTEND, ROOT
except ModuleNotFoundError:
    from ui_harness_support import FRONTEND, ROOT

from dayz_serverman.application import activity_result_wording as results
from dayz_serverman.application import activity_wording as wording
from dayz_serverman.host.assets import compose_shell_html
from dayz_serverman.host.editor_assets import EDITOR_SCRIPTS, compose_editor_html

# Each frontend document and the function that composes it
COMPOSERS = {"index.html": compose_shell_html, "editor.html": compose_editor_html}
# The names that moved to the Python result module
MOVED = ("GUARD_CODES", "NEUTRAL_SUCCESS", "RESTART_APPLY_TEXTS", "restart_apply_text")


def script_elements(document: str) -> list[str]:
    """Return the text of each inline script element of a composed document."""
    return re.findall(r"<script>\n(.*?)\n</script>", document, re.DOTALL)


def source(name: str) -> str:
    """Return one frontend script."""
    return (FRONTEND / name).read_text(encoding="utf-8")


class DocumentCompositionTests(unittest.TestCase):
    """Each composed document that holds the messages also holds the results in the same script element."""

    def test_every_frontend_document_has_a_composer(self) -> None:
        """A new document must be added here, so the rule below covers it."""
        self.assertEqual({path.name for path in FRONTEND.glob("*.html")}, set(COMPOSERS))

    def test_messages_come_with_results_in_one_script_element(self) -> None:
        """Both documents load the messages, and each element with the messages also has the results."""
        messages, outcomes = source("operation_messages.js"), source("operation_results.js")
        for name, compose in COMPOSERS.items():
            with self.subTest(document=name):
                elements = [text for text in script_elements(compose(FRONTEND)) if messages in text]
                self.assertEqual(len(elements), 1)
                self.assertIn(outcomes, elements[0])
                # The results follow the messages, as the documents list them
                self.assertLess(elements[0].index(messages), elements[0].index(outcomes))

    def test_editor_document_also_loads_the_failures(self) -> None:
        """The window words a lane failure with result(), so the editor composes the failure wording too."""
        element = script_elements(compose_editor_html(FRONTEND))[0]
        self.assertIn(source("operation_failures.js"), element)
        order = [EDITOR_SCRIPTS.index(name) for name in
                 ("operation_messages.js", "operation_results.js", "operation_failures.js")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(order[2] - order[0], 2)

    def test_the_split_boundary(self) -> None:
        """The result wording is declared once, in the results file; the export keeps its seven keys."""
        messages, outcomes = source("operation_messages.js"), source("operation_results.js")
        for name in ("operationDownloadResults", "operationVerifyGood", "verifyProblemCount",
                     "restartApplySuccess", "successPresentation", "operationResult"):
            declared = re.compile(rf"^(?:function|const) {name}\b", re.MULTILINE)
            self.assertRegex(outcomes, declared)
            self.assertNotRegex(messages, declared)
        exported = messages.split("window.ServerManOperationMessages = Object.freeze({", 1)[1].split("});", 1)[0]
        self.assertEqual(re.findall(r"^  (\w+):", exported, re.MULTILINE),
                         ["error", "bridgeError", "result", "recoveryText", "recoveryNotice", "medicalUnchanged",
                          "zoneBaseline"])
        self.assertIn("  result: operationResult,", exported)


class PythonSplitTests(unittest.TestCase):
    """`activity_wording` re-exports the result texts of `activity_result_wording` unchanged."""

    def test_moved_names_are_the_same_objects(self) -> None:
        """Existing importers get the objects of the new module."""
        for name in MOVED:
            self.assertIs(getattr(wording, name), getattr(results, name), name)

    def test_the_result_module_imports_first(self) -> None:
        """Importing the result module first, in a new interpreter, works and words a refused restart apply."""
        code = ("from dayz_serverman.application.activity_result_wording import restart_apply_text\n"
                "print(restart_apply_text('FAILED', 'STOP_SERVER', 'CONTROL_CONFLICT', 'state is STOPPED'))")
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False,
                                   cwd=ROOT, env={**os.environ,
                                                  "PYTHONPATH": str(ROOT / "runnable" / "src" / "python")})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), results.RESTART_APPLY_TEXTS["stopNotRunning"])


if __name__ == "__main__":
    unittest.main()
