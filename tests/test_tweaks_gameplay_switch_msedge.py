"""Composed-shell checks of the gameplay file switch on the Tweaks page."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness


# A fake host whose gameplay target is refused until the server configuration turns the gameplay file on
SETUP = r"""
let gameplayOn = false;
const notEnabled = {success: false, error: {code: "GAMEPLAY_NOT_ENABLED", retryable: false,
  message: "Enable the gameplay configuration file in Server settings before editing Gameplay settings."}};
window.pywebview.api.load_configuration = async (id, target) => {
  if (target === "server") return ok({profile_id: id, target, profile_revision: 3, settings_revision: 2,
    digest: "s".repeat(64), server_config_digest: null, fields: []});
  if (!gameplayOn) return notEnabled;
  return ok({profile_id: id, target, profile_revision: 3, settings_revision: 2, digest: "g".repeat(64),
    server_config_digest: "s".repeat(64), fields: []});
};
window.pywebview.api.load_mission_configuration = async (id, target) => ok({profile_id: id, target,
  profile_revision: 3, settings_revision: 2, digest: "c".repeat(64), values: {}});
window.pywebview.api.load_medical_features = async (id) => ok({profile_id: id, profile_revision: 3,
  settings_revision: 2, features: {medical_loot_zones: {enabled: false, digest: "d".repeat(64)}}});
const previews = [];
const applies = [];
window.pywebview.api.preview_configuration = async (...args) => { previews.push(args);
  return ok({changed_fields: Object.keys(args[6])}); };
window.pywebview.api.apply_configuration = async (...args) => { applies.push(args);
  return ok({operation_id: "op-server", state: "QUEUED"}); };
const switchButton = () => document.getElementById("tweaks-enable-gameplay");
const text = () => document.getElementById("tweaks-workspace").textContent;
"""


@unittest.skipUnless(EDGE.exists(), "Microsoft Edge is required")
class TweaksGameplaySwitchEdgeTests(unittest.TestCase):
    """Use actual composed assets with a fake host and disposable browser profiles."""

    def run_case(self, body: str) -> None:
        """Run one bounded composed-shell scenario."""
        self.assertEqual(run_shell_harness(SETUP + body), "PASS")

    def test_the_switch_turns_the_gameplay_file_on_from_the_tweaks_page(self) -> None:
        """No pointer to Configuration; one button sends the server change, and the page reloads with the settings."""
        self.run_case(r"""
await start("tweaks");
check(switchButton(), "the switch is on the Tweaks page");
check(!text().includes("in Configuration first"), "no pointer to another page");
check(text().includes("Turn on the gameplay file above to change these settings."), "groups point to the switch");
switchButton().click(); await wait(30);
check(previews.length === 1 && applies.length === 1, "one preview and one apply");
check(previews[0][1] === "server" && JSON.stringify(previews[0][6]) === '{"enableCfgGameplayFile":true}',
  "server change: " + JSON.stringify(previews[0]));
check(switchButton()?.disabled === true, "the button waits for the apply");
gameplayOn = true;
check(window.ServerManTweaks.operationFinished(record("op-server", "APPLY_CONFIGURATION", "SUCCEEDED", {result: {}})),
  "the apply is tracked");
await wait(60);
check(!switchButton(), "the switch is gone after the reload");
check(!text().includes("Turn on the gameplay file above"), "the groups show their settings");
""")

    def test_a_failed_apply_offers_the_button_again(self) -> None:
        """A failed switch reports the failure and enables the button again."""
        self.run_case(r"""
await start("tweaks");
switchButton().click(); await wait(30);
check(window.ServerManTweaks.operationFinished(record("op-server", "APPLY_CONFIGURATION", "FAILED",
  {terminal_error: {code: "SERVER_NOT_STOPPED", message: "Stop the server first."}})), "the failure is tracked");
await wait(30);
check(switchButton() && !switchButton().disabled, "the button is offered again");
""")


if __name__ == "__main__":
    unittest.main()
