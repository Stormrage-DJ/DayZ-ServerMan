"""The window catalogue (`operation_messages.js`) shows the 8.9 texts as the Python catalogue does (leaf 8.9).

A composed-shell harness in Microsoft Edge words each Apply failure under kind
APPLY_MISSION_MAP and each bridge refusal without a kind. The Python side runs
the same cases through `activity_wording.error_text`.
"""

from __future__ import annotations

import json
import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

from dayz_serverman.application.activity_wording import ERROR_TEXTS, REQUEST_TEXT, error_text
from dayz_serverman.application.mission_map_apply_choices import CHOICE_ENCOUNTERS_TEXT, CHOICE_FILES_TEXT
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND
from dayz_serverman.domain.mission_map_preview_texts import NO_SAVED_PLAN_TEXT, PREVIEW_STALE_TEXT
from dayz_serverman.domain.mission_map_restore_texts import INVALID_MODE_TEXT
from dayz_serverman.repositories.mission_map_incorporate import OWNED_VALUE_ABSENT_TEXT, OWNED_VALUE_TEXT

PATH = "mapgroupdirt.xml"
# Apply failures under the editor kind: (code, host message, shown text)
LANE_CASES = (
    ("REVISION_CONFLICT", PREVIEW_STALE_TEXT, PREVIEW_STALE_TEXT),
    # A late file change carries the commit text; the kind shows the stale text for the code
    ("REVISION_CONFLICT", "Apply stopped because mapgroupproto.xml changed.", PREVIEW_STALE_TEXT),
    ("INVALID_REQUEST", CHOICE_FILES_TEXT, CHOICE_FILES_TEXT),
    ("INVALID_REQUEST", CHOICE_ENCOUNTERS_TEXT, CHOICE_ENCOUNTERS_TEXT),
    ("INVALID_REQUEST", OWNED_VALUE_TEXT.format(path=PATH), OWNED_VALUE_TEXT.format(path=PATH)),
    ("INVALID_REQUEST", OWNED_VALUE_ABSENT_TEXT.format(path=PATH), OWNED_VALUE_ABSENT_TEXT.format(path=PATH)),
    ("INVALID_REQUEST", NO_SAVED_PLAN_TEXT, NO_SAVED_PLAN_TEXT),
)
# Bridge refusals of the previews, worded without a kind as the window's bridge wording does
BRIDGE_CASES = (("INVALID_REQUEST", NO_SAVED_PLAN_TEXT), ("INVALID_REQUEST", INVALID_MODE_TEXT))


class PythonCatalogueTests(unittest.TestCase):
    """`error_text` shows each text unchanged, never the general REVISION_CONFLICT or request text."""

    def test_lane_and_bridge_cases(self) -> None:
        """The same cases as the Edge harness, in the Python catalogue."""
        for code, message, shown in LANE_CASES:
            with self.subTest(message=message[:40]):
                self.assertEqual(error_text(code, message, kind=EDITOR_KIND), shown)
                self.assertNotIn(error_text(code, message, kind=EDITOR_KIND), (ERROR_TEXTS["REVISION_CONFLICT"],
                                                                               REQUEST_TEXT))
        for code, message in BRIDGE_CASES:
            with self.subTest(bridge=message[:40]):
                self.assertEqual(error_text(code, message), message)


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class WindowCatalogueEdgeTests(unittest.TestCase):
    """The composed shell's ServerManOperationMessages gives the same text for each case."""

    def test_lane_and_bridge_cases(self) -> None:
        """Each Apply failure and each preview refusal is shown unchanged in the window catalogue."""
        body = r"""
const LANE = %s;
const BRIDGE = %s;
const KIND = %s;
const messages = window.ServerManOperationMessages;
for (const [code, message, shown] of LANE) {
  const text = messages.error({code, message, retryable: false}, "", KIND);
  check(text === shown, "lane " + code + ": " + text);
}
for (const [code, message] of BRIDGE) {
  const text = messages.bridgeError({success: false, error: {code, message, retryable: false}}, "");
  check(text === message, "bridge " + code + ": " + text);
}
""" % (json.dumps(LANE_CASES), json.dumps(BRIDGE_CASES), json.dumps(EDITOR_KIND))
        self.assertEqual(run_shell_harness(body), "PASS")


if __name__ == "__main__":
    unittest.main()
