"""Composed-shell checks of the medical loot-zone toggle: no changing group (6.3), unmappable baseline (6.5)."""
from __future__ import annotations

import json
import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

from dayz_serverman.application.activity_wording import ZONE_ORIGINAL_WAY_OUT
from dayz_serverman.application.medical_features import EDITOR_MANAGED, NO_ZONE_CHANGE


# A fake host whose zone toggle changes no group: the review and the result carry the no-change message
SETUP = r"""
const UNCHANGED = %s;
const mission = (id, target) => ok({profile_id: id, profile_revision: 3,
  settings_revision: 2, digest: "c".repeat(64), values: target === "events"
    ? {events: {}} : target === "starter_loadout" ? {items: []} : {}});
window.pywebview.api.load_configuration = async (id) => ok({profile_id: id, fields: []});
window.pywebview.api.load_mission_configuration = async (id, target) => mission(id, target);
window.pywebview.api.load_medical_features = async (id) => ok({profile_id: id,
  profile_revision: 3, settings_revision: 2, features: {
    medical_loot_zones: {enabled: true, digest: "d".repeat(64)}}});
const sent = [];
window.pywebview.api.preview_medical_feature = async (...args) => { sent.push(["preview", args]);
  return ok({profile_id: "alpha", feature: args[1], enabled: args[2], expected_digest: "d".repeat(64),
    proposed_digest: "d".repeat(64), changed_fields: [], message: UNCHANGED}); };
window.pywebview.api.apply_medical_feature = async (...args) => { sent.push(["apply", args]);
  return ok({operation_id: "zones", state: "QUEUED"}); };
const feedback = () => document.getElementById("tweaks-feedback").textContent;
const done = record("zones", "APPLY_MEDICAL_FEATURE", "SUCCEEDED", {result: {profile_id: "alpha",
  feature: "medical_loot_zones", enabled: false, digest: "d".repeat(64), changed_fields: [], message: UNCHANGED}});
host.operations.set("zones", done);
""" % json.dumps(NO_ZONE_CHANGE)


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class TweaksMedicalNoChangeEdgeTests(unittest.TestCase):
    """Use actual composed assets with a fake host and disposable browser profiles."""

    def run_case(self, body: str) -> None:
        """Run one bounded composed-shell scenario."""
        self.assertEqual(run_shell_harness(SETUP + body), "PASS")

    def test_review_and_success_show_the_no_change_sentence(self) -> None:
        """The review and the finished toggle show the no-change sentence in place of the normal texts."""
        self.run_case(r"""
await start("tweaks");
setTweakValue("medical_loot_zones", "enabled", false);
await reviewOrApplyTweakTarget("medical_loot_zones");
check(feedback() === UNCHANGED, "review: " + feedback());
check(sent.at(-1)[0] === "preview" && sent.at(-1)[1].length === 6, "six review arguments");
await reviewOrApplyTweakTarget("medical_loot_zones");
check(sent.at(-1)[0] === "apply" && sent.at(-1)[1].length === 6, "six apply arguments");
const result = window.ServerManOperationMessages.result(done);
check(result.sentence === UNCHANGED && result.look === "success", "bar sentence: " + result.sentence);
check(window.ServerManTweaks.operationFinished(done), "tracked operation");
await wait(20);
check(feedback() === UNCHANGED, "page after reload: " + feedback());
check(!feedback().includes("Medical loot setting applied."), "normal success sentence replaced");
""")

    def test_a_changing_toggle_keeps_the_normal_sentences(self) -> None:
        """A toggle that changes a group keeps the change count and the normal success sentence."""
        self.run_case(r"""
window.pywebview.api.preview_medical_feature = async (...args) => ok({profile_id: "alpha", feature: args[1],
  enabled: args[2], expected_digest: "d".repeat(64), proposed_digest: "f".repeat(64), changed_fields: [args[1]]});
await start("tweaks");
setTweakValue("medical_loot_zones", "enabled", false);
await reviewOrApplyTweakTarget("medical_loot_zones");
check(feedback() === "1 medical loot zones change(s) validated. Select Apply to publish one file.",
  "review: " + feedback());
const changed = record("zones", "APPLY_MEDICAL_FEATURE", "SUCCEEDED", {result: {changed_fields: ["medical_loot_zones"]}});
check(window.ServerManOperationMessages.result(changed).sentence === "Medical loot setting applied.", "normal sentence");
""")


# The zone answer with its classification, the medical group's controls and notices, and the texts
BASELINE = r"""
const WAY_OUT = %s;
const EDITOR = %s;
const REASON = "mapgroupproto.xml differs from its stored original: group PlannerRich_00534 is not in the stored "
  + "original. No file was changed.";
const STORED = "mpmissions\\dayzOffline.Pripyat\\.dayz_manager_backups\\mapgroupproto.xml";
const answer = (baseline) => async (id) => ok({profile_id: id, profile_revision: 3, settings_revision: 2,
  features: {medical_loot_zones: {enabled: true, digest: "d".repeat(64), zone_baseline: baseline}}});
const medical = async () => { await start("tweaks"); chooseGameplayTab("map-loot");
  const group = [...document.querySelectorAll("fieldset.tweak-group")]
    .find((node) => node.querySelector("legend").textContent === "Medical loot");
  return {selects: [...group.querySelectorAll("select")],
    notices: [...group.querySelectorAll(".notice")].map((node) => node.textContent)}; };
""" % (json.dumps(ZONE_ORIGINAL_WAY_OUT), json.dumps(EDITOR_MANAGED))


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class TweaksMedicalBaselineEdgeTests(unittest.TestCase):
    """6.5: the unmappable loot-zone baseline on the tweaks page; the read state is never guessed."""

    def run_case(self, body: str) -> None:
        """Run one bounded composed-shell scenario with the baseline helpers."""
        self.assertEqual(run_shell_harness(SETUP + BASELINE + body), "PASS")

    def test_a_refusal_that_the_load_reads_past_locks_the_toggle(self) -> None:
        """The read state stays, the zone toggle is locked, and one notice names the refusal."""
        self.run_case(r"""
window.pywebview.api.load_medical_features = answer({mappable: false, reason: EDITOR, location: "legacy",
  stored_original: STORED});
const page = await medical();
check(page.selects.length === 1, "one toggle: " + page.selects.length);
check(page.selects[0].value === "true" && page.selects[0].disabled, "zones: read state, locked");
check(page.notices.length === 1, "one notice: " + page.notices.join(" | "));
check(page.notices[0] === "Medical loot zones cannot change because the map editor manages this mission.",
  "notice: " + page.notices[0]);
""")

    def test_a_mappable_baseline_shows_no_notice(self) -> None:
        """A mappable baseline, also with other line endings, adds nothing to the page."""
        self.run_case(r"""
window.pywebview.api.load_medical_features = answer({mappable: true, reason: null, location: "legacy",
  stored_original: STORED});
const page = await medical();
check(page.selects.length === 1 && !page.selects[0].disabled, "zone toggle open");
check(page.notices.length === 0, "no notice: " + page.notices.join(" | "));
""")

    def test_a_zone_refusal_that_stops_the_load_names_the_way_out(self) -> None:
        """The load error keeps its code; the group notice gives the reason, the way out and the file."""
        self.run_case(r"""
window.pywebview.api.load_medical_features = async () => ({success: false, error: {code: "REVISION_CONFLICT",
  message: REASON, retryable: true, details: {zone_baseline: {mappable: false, reason: REASON,
    location: "legacy", stored_original: STORED}}}});
const page = await medical();
check(page.selects.length === 0, "no toggle: " + page.selects.length);
check(page.notices.length === 1, "one notice: " + page.notices.join(" | "));
check(page.notices[0] === `${REASON} ${WAY_OUT} Stored original: ${STORED}`, "notice: " + page.notices[0]);
const plain = window.ServerManOperationMessages.error({code: "REVISION_CONFLICT", message: REASON});
check(plain === REASON, "without details the host sentence stays: " + plain);
// QA-6.5-6: an unknown encoding names the file and the cause, then the layout way out
const encoding = "mapgroupproto.xml has a layout that this setting cannot change safely: the file declares an "
  + "unknown encoding. No file was changed.";
const worded = window.ServerManOperationMessages.error({code: "RECOVERY_REQUIRED", message: encoding,
  details: {zone_baseline: {mappable: false, reason: encoding, location: "legacy", stored_original: STORED}}});
check(worded === `${encoding} Correct this in mapgroupproto.xml, then try again.`, "encoding: " + worded);
""")


if __name__ == "__main__":
    unittest.main()
