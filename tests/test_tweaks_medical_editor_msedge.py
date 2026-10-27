"""Composed-shell checks of the zone toggle on an editor-managed mission (8.13): fingerprint and refusal texts."""
from __future__ import annotations

import json
import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

from dayz_serverman.domain.medical_editor_texts import FINGERPRINT_MISSING_TEXT, MISMATCH_TEXT, NO_APPLIED_PLAN_TEXT


# A fake host whose zone review returns a fingerprint, as on an editor-managed mission
SETUP = r"""
const MISMATCH = %s;
const MISSING = %s;
const NO_PLAN = %s;
const FINGERPRINT = "f".repeat(64);
const mission = (id, target) => ok({profile_id: id, profile_revision: 3,
  settings_revision: 2, digest: "c".repeat(64), values: target === "events"
    ? {events: {}} : target === "starter_loadout" ? {items: []} : {}});
window.pywebview.api.load_configuration = async (id) => ok({profile_id: id, fields: []});
window.pywebview.api.load_mission_configuration = async (id, target) => mission(id, target);
window.pywebview.api.load_medical_features = async (id) => ok({profile_id: id,
  profile_revision: 3, settings_revision: 2, features: {
    medical_loot_zones: {enabled: false, digest: "d".repeat(64), zone_baseline: {mappable: true, reason: null,
      location: "manager", stored_original: "x.xml"}}}});
const sent = [];
window.pywebview.api.preview_medical_feature = async (...args) => { sent.push(["preview", args]);
  return ok({profile_id: "alpha", feature: args[1], enabled: args[2], expected_digest: "d".repeat(64),
    proposed_digest: "a".repeat(64), changed_fields: [args[1]],
    ...(args[1] === "medical_loot_zones" ? {preview_fingerprint: FINGERPRINT} : {})}); };
window.pywebview.api.apply_medical_feature = async (...args) => { sent.push(["apply", args]);
  return ok({operation_id: "zones", state: "QUEUED"}); };
const feedback = () => document.getElementById("tweaks-feedback").textContent;
const failed = (code, message) => record("zones", "APPLY_MEDICAL_FEATURE", "FAILED",
  {terminal_error: {code, message, retryable: false}});
""" % (json.dumps(MISMATCH_TEXT), json.dumps(FINGERPRINT_MISSING_TEXT), json.dumps(NO_APPLIED_PLAN_TEXT))


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class TweaksMedicalEditorEdgeTests(unittest.TestCase):
    """Actual composed assets with a fake host and disposable browser profiles."""

    def run_case(self, body: str) -> None:
        """Run one bounded composed-shell scenario."""
        self.assertEqual(run_shell_harness(SETUP + body), "PASS")

    def test_the_zone_review_fingerprint_goes_to_apply(self) -> None:
        """The zone Apply sends the six fields and the reviewed fingerprint."""
        self.run_case(r"""
await start("tweaks");
setTweakValue("medical_loot_zones", "enabled", true);
await reviewOrApplyTweakTarget("medical_loot_zones");
check(sent.at(-1)[0] === "preview" && sent.at(-1)[1].length === 6, "six review arguments");
check(feedback() === "1 medical loot zones change(s) validated. Select Apply to publish one file.",
  "review: " + feedback());
await reviewOrApplyTweakTarget("medical_loot_zones");
check(sent.at(-1)[0] === "apply" && sent.at(-1)[1].length === 7, "seven apply arguments");
check(sent.at(-1)[1][6] === FINGERPRINT, "fingerprint sent: " + sent.at(-1)[1][6]);
check(JSON.stringify(sent.at(-1)[1].slice(0, 6)) === JSON.stringify(sent.at(-2)[1]), "reviewed fields");
""")

    def test_refusals_show_their_text(self) -> None:
        """A refused review and a failed toggle show the host sentence; a running server reads as for Apply."""
        self.run_case(r"""
window.pywebview.api.preview_medical_feature = async () => fail("INVALID_REQUEST", MISMATCH);
await start("tweaks");
setTweakValue("medical_loot_zones", "enabled", true);
await reviewOrApplyTweakTarget("medical_loot_zones");
check(feedback() === MISMATCH, "review refusal: " + feedback());
const messages = window.ServerManOperationMessages;
check(messages.error({code: "INVALID_REQUEST", message: MISSING}, undefined, "APPLY_MEDICAL_FEATURE") === MISSING,
  "missing fingerprint text");
check(messages.error({code: "INVALID_REQUEST", message: NO_PLAN}, undefined, "APPLY_MEDICAL_FEATURE") === NO_PLAN,
  "no applied plan text");
window.pywebview.api.preview_medical_feature = async () => fail("INVALID_REQUEST", NO_PLAN);
tweaksState.reviewed.clear();
await reviewOrApplyTweakTarget("medical_loot_zones");
check(feedback() === NO_PLAN, "no applied plan review refusal: " + feedback());
const running = {code: "CONTROL_CONFLICT", message: "Applying mods requires STOPPED; current state is RUNNING_MANAGED."};
check(messages.error(running, undefined, "APPLY_MEDICAL_FEATURE") === messages.error(running, undefined,
  "APPLY_MISSION_MAP"), "running server as for Apply");
const result = window.ServerManOperationBar.pageResult(failed("INVALID_REQUEST", MISMATCH));
check(result.text.includes(MISMATCH), "failed toggle: " + result.text);
""")


if __name__ == "__main__":
    unittest.main()
