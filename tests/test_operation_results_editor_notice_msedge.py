"""The window result of a backup that left out its editor records shows the 10.9 notice (10.37, Edge)."""

from __future__ import annotations

import json
import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

from dayz_serverman.application.backups import EDITOR_RECORDS_OMITTED


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class EditorNoticeEdgeTests(unittest.TestCase):
    """ServerManOperationMessages.result gives the notice as the message of the success, and only then."""

    def test_the_notice_follows_the_success_sentence(self) -> None:
        self.assertEqual(run_shell_harness("const NOTICE = %s;\n" % json.dumps(EDITOR_RECORDS_OMITTED) + r"""
const messages = window.ServerManOperationMessages;
const cases = [["CREATE_BACKUP", {editor_records_omitted: true}, true],
  ["STOP_SERVER", {backup: {editor_records_omitted: true}}, true],
  ["RESTART_SERVER", {backup: {editor_records_omitted: true}}, true],
  ["CREATE_BACKUP", {editor_records_omitted: false}, false],
  ["STOP_SERVER", {backup: {}}, false]];
for (const [kind, result, shown] of cases) {
  const worded = messages.result(record("op", kind, "SUCCEEDED", {result}));
  check(worded.look === "success", kind + " look: " + worded.look);
  check((worded.message === NOTICE) === shown, kind + " message: " + worded.message);
  check(!shown || worded.text === worded.sentence + " " + NOTICE, kind + " text: " + worded.text);
}
"""), "PASS")


if __name__ == "__main__":
    unittest.main()
