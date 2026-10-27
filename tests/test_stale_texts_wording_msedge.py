"""The stale texts of leaf 8.14 in the window catalogue, the tweaks page and the editor document.

A composed-shell harness in Microsoft Edge words the cases of `stale_wording_cases` as the Python
catalogue does (`test_stale_texts_wording`). The tweaks page shows a failed zone toggle with the 8.13
stale-review text. The independent editor document words a failed restore with the 8.8 stale-restore
text, which needs `operation_results.js` and `operation_failures.js` in that document.
"""

from __future__ import annotations

import json
import unittest

try:
    from tests.ui_harness_support import EDGE, run_editor_harness, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_editor_harness, run_shell_harness

import tests.test_tweaks_medical_editor_msedge as tweaks_page
from dayz_serverman.application.activity_wording import KIND_TEXTS
from dayz_serverman.domain.medical_editor_texts import FINGERPRINT_STALE_TEXT
from dayz_serverman.domain.mission_map_restore_texts import LANE_KINDS, RESTORE_STALE_TEXT
from tests.stale_wording_cases import ALL_CASES, CHANGED_TEXT, GENERAL_TEXT, LATE_CHANGE_TEXT, MEDICAL_KIND

# The constants of the harness bodies
CONSTANTS = r"""
const CASES = %s;
const STALE = %s;
const GENERAL = %s;
const CHANGED = %s;
const FAILURES = %s;
""" % (json.dumps(ALL_CASES), json.dumps(FINGERPRINT_STALE_TEXT), json.dumps(GENERAL_TEXT),
       json.dumps(CHANGED_TEXT), json.dumps({kind: texts[2] for kind, texts in KIND_TEXTS.items()}))


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class WindowCatalogueEdgeTests(unittest.TestCase):
    """The composed shell's ServerManOperationMessages gives the Python text for each case."""

    def test_each_case_in_the_window_catalogue(self) -> None:
        """A lane failure of a kind is worded by error() and result(); a refusal without a kind by bridgeError()."""
        self.assertEqual(run_shell_harness(CONSTANTS + r"""
const messages = window.ServerManOperationMessages;
for (const [label, kind, code, message, shown] of CASES) {
  const error = {code, message, retryable: true};
  if (kind === null) {
    const text = messages.bridgeError({success: false, error}, "");
    check(text === shown, label + ": " + text);
    continue;
  }
  check(messages.error(error, "", kind) === shown, label + ": " + messages.error(error, "", kind));
  const result = messages.result(record("op", kind, "FAILED", {terminal_error: error}));
  check(result.text === FAILURES[kind] + " " + shown, label + " result: " + result.text);
  check(shown === GENERAL || !result.text.includes(GENERAL), label + " general text");
}
"""), "PASS")

    def test_tweaks_page_shows_the_stale_review_text(self) -> None:
        """A zone toggle that fails as a stale review shows the 8.13 text; an ordinary failure the general text."""
        self.assertEqual(run_shell_harness(tweaks_page.SETUP + CONSTANTS + r"""
await start("tweaks");
setTweakValue("medical_loot_zones", "enabled", true);
await reviewOrApplyTweakTarget("medical_loot_zones");
await reviewOrApplyTweakTarget("medical_loot_zones");
check(sent.at(-1)[0] === "apply", "applied");
const stale = window.ServerManOperationBar.pageResult(failed("REVISION_CONFLICT", STALE));
check(stale.text.includes(STALE) && !stale.text.includes(GENERAL), "stale review: " + stale.text);
const ordinary = window.ServerManOperationBar.pageResult(failed("REVISION_CONFLICT", CHANGED));
check(ordinary.text.includes(GENERAL) && !ordinary.text.includes(STALE), "ordinary: " + ordinary.text);
// A review that refuses with the stale text shows it in the page feedback
window.pywebview.api.preview_medical_feature = async () => fail("REVISION_CONFLICT", STALE);
tweaksState.reviewed.clear();
await reviewOrApplyTweakTarget("medical_loot_zones");
check(feedback() === STALE, "feedback: " + feedback());
"""), "PASS")


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class EditorDocumentEdgeTests(unittest.TestCase):
    """The editor document composes the result and failure wording with the messages (8.14)."""

    def test_editor_words_a_failed_restore_and_a_stale_review(self) -> None:
        """result() works in the editor document and shows the 8.8 and 8.13 texts."""
        body = r"""
const KINDS = %s;
const RESTORE_STALE = %s;
const LATE = %s;
const STALE = %s;
const FAILURES = %s;
const messages = window.ServerManOperationMessages;
const failed = (kind, message) => messages.result({operation_id: "op", kind, state: "FAILED",
  terminal_error: {code: "REVISION_CONFLICT", message, retryable: true}, last_working_phase: null});
for (const kind of KINDS) {
  for (const message of [RESTORE_STALE, LATE]) {
    const result = failed(kind, message);
    check(result.look === "failed" && result.text === FAILURES[kind] + " " + RESTORE_STALE, kind + ": " + result.text);
  }
}
const medical = failed("APPLY_MEDICAL_FEATURE", STALE);
check(medical.text === FAILURES.APPLY_MEDICAL_FEATURE + " " + STALE, "medical: " + medical.text);
check(messages.result({operation_id: "op", kind: KINDS[0], state: "SUCCEEDED", result: {}}).look === "success",
  "success");
""" % (json.dumps(list(LANE_KINDS.values())), json.dumps(RESTORE_STALE_TEXT), json.dumps(LATE_CHANGE_TEXT),
       json.dumps(FINGERPRINT_STALE_TEXT),
       json.dumps({kind: KIND_TEXTS[kind][2] for kind in (*LANE_KINDS.values(), MEDICAL_KIND)}))
        self.assertEqual(run_editor_harness(body), "PASS")


if __name__ == "__main__":
    unittest.main()
