"""The window result of a backup names the older backups that retention deleted, as the CLI does (Edge)."""

from __future__ import annotations

import json
import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

from dayz_serverman.application.backups import EDITOR_RECORDS_OMITTED, retention_notice


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class RetentionNoticeEdgeTests(unittest.TestCase):
    """ServerManOperationMessages.result words retention like application/backups.py retention_notice."""

    def test_the_window_and_the_cli_give_the_same_text(self) -> None:
        pruned = {"backups_to_keep": 3, "pruned_backups": ["main_a"], "prune_failures": ["main_b"]}
        both = {**pruned, "editor_records_omitted": True}
        cases = [["CREATE_BACKUP", pruned, retention_notice(pruned)],
                 ["STOP_SERVER", {"backup": pruned}, retention_notice(pruned)],
                 ["CREATE_BACKUP", both, EDITOR_RECORDS_OMITTED + " " + retention_notice(both)],
                 ["CREATE_BACKUP", {"backups_to_keep": 3, "pruned_backups": [], "prune_failures": []}, ""]]
        self.assertEqual(run_shell_harness("const CASES = %s;\n" % json.dumps(cases) + r"""
const messages = window.ServerManOperationMessages;
for (const [kind, result, expected] of CASES) {
  const worded = messages.result(record("op", kind, "SUCCEEDED", {result}));
  check(worded.look === "success", kind + " look: " + worded.look);
  check(worded.message === expected, kind + " message: " + worded.message);
}
"""), "PASS")


if __name__ == "__main__":
    unittest.main()
