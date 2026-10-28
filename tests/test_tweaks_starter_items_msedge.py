"""Composed-shell checks of Starter Items: the mission's own items, their switch, and the own items."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# A fake host whose mission gives its own items, as DeerIsle does
SETUP = r"""
const mission = {items: [{name: "Rag", count: 2}, {name: "Apple", count: 1}],
  choices: [["Chemlight_White", "Chemlight_Red"]], random: true, effects: ["sets item quantities"]};
window.pywebview.api.load_configuration = async (id) => ok({profile_id: id, fields: []});
window.pywebview.api.load_mission_configuration = async (id, target) => ok({profile_id: id, target,
  profile_revision: 3, settings_revision: 2, digest: "c".repeat(64),
  values: target === "starter_loadout" ? {items: [], mission_items: true} : {},
  ...(target === "starter_loadout" ? {mission_starter: mission} : {})});
window.pywebview.api.load_medical_features = async (id) => ok({profile_id: id, profile_revision: 3,
  settings_revision: 2, features: {medical_loot_zones: {enabled: false, digest: "d".repeat(64)}}});
const sent = [];
window.pywebview.api.preview_mission_configuration = async (...args) => { sent.push(args);
  return ok({changed_fields: Object.keys(args[5])}); };
const section = () => document.querySelector(".starter-mission");
"""


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class TweaksStarterItemsEdgeTests(unittest.TestCase):
    """Use actual composed assets with a fake host and disposable browser profiles."""

    def run_case(self, body: str) -> None:
        """Run one bounded composed-shell scenario."""
        self.assertEqual(run_shell_harness(SETUP + body), "PASS")

    def test_the_mission_items_are_named_and_can_be_turned_off(self) -> None:
        """The section names the mission's items; turning them off with an own item reviews both values."""
        self.run_case(r"""
await start("tweaks");
chooseGameplayTab("starter");
const text = section()?.textContent ?? "";
check(text.includes("Gives: Rag ×2, Apple."), "items: " + text);
check(text.includes("One of: Chemlight_White, Chemlight_Red."), "choice");
check(text.includes("Some items are chosen at random.") && text.includes("It also sets item quantities."), "notes");
check(!document.getElementById("tweaks-workspace").textContent.includes("conversion"), "no conversion");
const keep = document.getElementById("starter-mission-items");
check(keep.checked, "the mission's items start on");
keep.click(); await wait(10);
check(section().textContent.includes("a new character gets only the items chosen below"), "off explains itself");
const apple = [...document.querySelectorAll(".starter-grid label")].find((label) => label.textContent === "Apple");
apple.querySelector("input").click(); await wait(10);
await reviewOrApplyTweakPage();
check(sent.length === 1 && sent[0][1] === "starter_loadout", "one starter review");
check(JSON.stringify(sent[0][5]) === JSON.stringify({items: ["Apple"], mission_items: false}),
  "updates: " + JSON.stringify(sent[0][5]));
""")


if __name__ == "__main__":
    unittest.main()
