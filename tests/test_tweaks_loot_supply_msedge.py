"""Composed-shell checks of the Loot Supply tab: percentage sliders, their effect text and the review."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# A fake host with supply percentages, group facts, the magazine percentage and the throughput globals
SETUP = r"""
const supply = {supply_weapons: 100, supply_attachments: 100, supply_magazines: 100, supply_ammo: 150,
  supply_ammo_boxes: 100, supply_explosives: 100, supply_food: 100, supply_medical: 100, supply_tools: 100,
  supply_clothing: 100, supply_containers: 100, ammo_fill: 100};
const facts = {groups: {supply_ammo: {types: 27, nominal: 1265}, supply_clothing: {types: 0, nominal: 0}},
  fill_types: 73, edited: 0, duplicates: 0};
const values = {loot_supply: supply, weapon_magazines: {magazine_chance: 100},
  economy: {InitialSpawn: 100, RespawnTypes: 12, RespawnLimit: 20, RespawnAttempt: 2, SpawnInitial: 1200,
    RestartSpawn: 0}, events: {events: {}}, starter_loadout: {items: []}};
window.pywebview.api.load_configuration = async (id) => ok({profile_id: id, fields: []});
window.pywebview.api.load_mission_configuration = async (id, target) => ok({profile_id: id, target,
  profile_revision: 3, settings_revision: 2, digest: "c".repeat(64), values: values[target] ?? {},
  ...(target === "loot_supply" ? {facts} : {})});
window.pywebview.api.load_medical_features = async (id) => ok({profile_id: id, profile_revision: 3,
  settings_revision: 2, features: {medical_loot_zones: {enabled: false, digest: "d".repeat(64)}}});
const sent = [];
const applied = [];
window.pywebview.api.preview_mission_configuration = async (...args) => { sent.push(args);
  return ok({changed_fields: Object.keys(args[5])}); };
window.pywebview.api.apply_mission_configuration = async (...args) => { applied.push(args);
  return ok({operation_id: `op-${args[1]}`, state: "QUEUED"}); };
let loads = 0;
const loadMission = window.pywebview.api.load_mission_configuration;
window.pywebview.api.load_mission_configuration = async (...args) => { loads += 1; return loadMission(...args); };
const actions = () => [...document.querySelectorAll("#tweaks-actions button")].map((node) => node.textContent);
const feedback = () => document.getElementById("tweaks-feedback").textContent;
const slider = (label) => document.querySelector(`input[type="range"][aria-label="${label}"]`);
const effect = (label) => slider(label).closest("label").querySelector(".tweak-percent-effect")?.textContent ?? "";
"""


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class TweaksLootSupplyEdgeTests(unittest.TestCase):
    """Use actual composed assets with a fake host and disposable browser profiles."""

    def run_case(self, body: str) -> None:
        """Run one bounded composed-shell scenario."""
        self.assertEqual(run_shell_harness(SETUP + body), "PASS")

    def test_sliders_show_percent_and_effect_and_review_the_change(self) -> None:
        """Sliders read the percentages, show their effect, and a moved slider reviews only its own value."""
        self.run_case(r"""
await start("tweaks");
chooseGameplayTab("supply");
const ammo = slider("Loose ammunition");
check(ammo && ammo.min === "0" && ammo.max === "300" && ammo.step === "5", "ammo slider range");
check(ammo.value === "150", "ammo value: " + ammo.value);
check(effect("Loose ammunition") === "27 item types · 1265 on the map originally · about 1898 at 150%",
  "ammo effect: " + effect("Loose ammunition"));
check(effect("Clothing") === "No item type of this group spawns in this mission.", "empty group");
check(effect("Ammunition & magazine fill") === "73 ammunition and magazine types in this mission.", "fill");
const initial = slider("Initial loot");
check(initial && initial.max === "100" && initial.value === "100", "initial loot slider");
check(slider("Weapons with a magazine")?.value === "100", "magazine slider");
ammo.value = "200"; ammo.dispatchEvent(new Event("input"));
check(ammo.closest("label").querySelector("output").textContent === "200%", "shown value follows");
ammo.dispatchEvent(new Event("change"));
check(JSON.stringify(actions()) === JSON.stringify(["Review changes", "Discard all changes"]), "one review");
await reviewOrApplyTweakPage();
check(sent.length === 1 && sent[0][1] === "loot_supply", "target: " + sent[0]?.[1]);
check(JSON.stringify(sent[0][5]) === JSON.stringify({supply_ammo: 200}), "updates: " + JSON.stringify(sent[0][5]));
""")


    def test_one_review_and_one_apply_cover_every_changed_part_of_the_page(self) -> None:
        """Changes in three files give one Review changes, one Apply changes, and one reload after the last apply."""
        self.run_case(r"""
await start("tweaks");
chooseGameplayTab("supply");
setTweakValue("loot_supply", "supply_ammo", 200);
setTweakValue("weapon_magazines", "magazine_chance", 150);
setTweakValue("economy", "RespawnTypes", 24);
check(JSON.stringify(actions()) === JSON.stringify(["Review changes", "Discard all changes"]), "one review: " + actions());
await reviewOrApplyTweakPage();
check(sent.map((args) => args[1]).join() === "loot_supply,weapon_magazines,economy", "three previews");
check(feedback() === "3 change(s) validated in 3 file(s). Select Apply changes to publish them.", "review: " + feedback());
check(JSON.stringify(actions()) === JSON.stringify(["Apply changes", "Discard all changes"]), "one apply: " + actions());
setTweakValue("economy", "RespawnTypes", 30);
check(actions()[0] === "Review changes", "a later change needs a new review");
await reviewOrApplyTweakPage();
check(sent.length === 4 && sent[3][1] === "economy", "only the changed part is reviewed again");
await reviewOrApplyTweakPage();
check(applied.map((args) => args[1]).join() === "loot_supply,weapon_magazines,economy", "three applies");
const before = loads;
const done = (target) => record(`op-${target}`, "APPLY_MISSION_CONFIGURATION", "SUCCEEDED", {result: {}});
check(window.ServerManTweaks.operationFinished(done("loot_supply")), "first tracked");
check(window.ServerManTweaks.operationFinished(done("weapon_magazines")), "second tracked");
await wait(20);
check(loads === before, "no reload before the last apply");
check(window.ServerManTweaks.operationFinished(done("economy")), "last tracked");
await wait(20);
check(loads > before, "one reload after the last apply");
""")


if __name__ == "__main__":
    unittest.main()
