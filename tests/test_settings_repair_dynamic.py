"""QF-069 (D19), test (u): the Settings page asks for the restart after a repair save and shows its refusals."""
from __future__ import annotations

import unittest

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness

from dayz_serverman.application import activity_wording as wording
from dayz_serverman.application.settings_repair import NOT_A_SERVER_FOLDER, NOT_THE_USED_FOLDER

# The restart sentence of the design, and the start of a failed save
RESTART = "Restart DayZ-ServerMan. It then checks the interrupted work in this DayZ server folder."
FAILED = "The application locations could not be saved."

SETTINGS_REPAIR = r"""
const same = (actual, expected, name) => check(actual === expected, `${name}: ${actual}`);
const feedback = () => document.getElementById("settings-feedback")?.textContent || "";
let count = 0;
// Finish one pending save on the open Settings page with the given terminal fields.
const finish = async (state, extra) => {
  const id = `save-${count += 1}`;
  settingsState.pending = Object.freeze({operationId: id, context: settingsContext()});
  window.ServerManSettings.operationFinished(record(id, "SAVE_SETTINGS", state, extra));
  await wait(60);
};
await start("settings");
await finish("SUCCEEDED", {result: {revision: 3, restart_required: true}});
same(feedback(), RESTART, "restart sentence after a repair save");
await finish("SUCCEEDED", {result: {revision: 4}});
same(feedback(), "", "no restart sentence after a normal save");
for (const message of [NOT_A_SERVER_FOLDER, NOT_THE_USED_FOLDER]) {
  await finish("FAILED", {progress_phase: "failed", terminal_error: {code: "PATH_INVALID", message}});
  same(feedback(), `${FAILED} ${message}`, `refusal ${message}`);
}
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class SettingsRepairDynamicTests(unittest.TestCase):
    """The page words the repair save in operator language."""

    def test_u_restart_sentence_and_refusals(self) -> None:
        """(u) The restart sentence on restart_required, and both PATH_INVALID sentences of the repair."""
        for message in (NOT_A_SERVER_FOLDER, NOT_THE_USED_FOLDER, RESTART):
            self.assertFalse(wording.leaks_identifier(message), message)
        constants = (f"const RESTART = {RESTART!r};\nconst FAILED = {FAILED!r};\n"
                     f"const NOT_A_SERVER_FOLDER = {NOT_A_SERVER_FOLDER!r};\n"
                     f"const NOT_THE_USED_FOLDER = {NOT_THE_USED_FOLDER!r};\n")
        self.assertEqual(run_shell_harness(constants + SETTINGS_REPAIR), "PASS")


if __name__ == "__main__":
    unittest.main()
