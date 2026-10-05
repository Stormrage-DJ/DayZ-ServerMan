"""QF-069 (D19): the real Settings page in the blocked state sends a request that the repair save accepts.

The operator only chooses the DayZ server folder and presses Save locations. The
page runs in headless Edge on the real snapshot and the real folder selection of
the host; the request that it sends goes through the real host method, the lane
and the repair save. Every combination of the three "no DayZ server folder"
blocks, a stored backup folder (none, the portable folder named explicitly, a
custom folder) and a stored SteamCMD folder (set or not) is driven.
"""
from __future__ import annotations

import html
import json
import tempfile
import time
import unittest
from pathlib import Path

try:
    from tests.ui_harness_support import EDGE, run_shell_harness
    from tests.test_settings_missing_root_repair import build_repair, server_folder
except ModuleNotFoundError:
    from ui_harness_support import EDGE, run_shell_harness
    from test_settings_missing_root_repair import build_repair, server_folder

from dayz_serverman.application.coordinator import ApplicationCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import TERMINAL_STATES
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.application.settings import SETTINGS_FIELDS, expand_location_roots
from dayz_serverman.application.shutdown import ShutdownCoordinator
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.domain.models import SettingsInput
from dayz_serverman.host.api import HostApi
from dayz_serverman.observability.structured_log import StructuredLogger

# The three blocks that a repair save may pass, with their owners
BLOCKS = (("Mutations are blocked by unresolved mod publication.", None),
          ("Direct profile restore recovery requires a configured DayZ root.", None),
          ("Backup restore recovery requires a configured DayZ root.", "RESTORE_BACKUP"))
RESTART = "Restart DayZ-ServerMan. It then checks the interrupted work in this DayZ server folder."

# Run 1: open each case, choose the DayZ folder, save; return every request the page sent
CHOOSE_AND_SAVE = r"""
const api = window.pywebview.api;
const sent = [];
await start("settings");
for (const item of CASES) {
  api.get_application_snapshot = async () => ok(item.snapshot);
  api.select_settings_path = async () => item.selection;
  api.save_settings = async (payload, revision) => { sent.push({payload, revision});
    return ok({operation_id: `page-${sent.length}`, state: "QUEUED"}); };
  window.ServerManTransitions.setDirty("settings-paths", false);
  await window.ServerManSettings.open(item.snapshot); await wait(20);
  document.querySelector("[data-settings-browse='dayz_root']").click(); await wait(20);
  document.getElementById("save-path-settings").click(); await wait(20);
}
harnessResult.textContent = JSON.stringify(sent);
return;
"""

# Run 2: finish each save with its real terminal record; the restart sentence must show
FINISH = r"""
const feedback = () => document.getElementById("settings-feedback")?.textContent || "";
await start("settings");
for (const item of CASES) {
  window.pywebview.api.get_application_snapshot = async () => ok(item.snapshot);
  await window.ServerManSettings.open(item.snapshot); await wait(20);
  settingsState.pending = Object.freeze({operationId: item.record.operation_id, context: settingsContext()});
  window.ServerManSettings.operationFinished(item.record); await wait(60);
  check(feedback() === RESTART, `restart sentence ${item.name}: ${feedback()}`);
}
"""


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class SettingsRepairPageTests(unittest.TestCase):
    """The page request passes the shape check, the save succeeds, and the restart sentence shows."""

    def setUp(self) -> None:
        """Build one blocked manager per case below one disposable folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_repair_page_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.steam = self.base / "Steam CMD"
        self.steam.mkdir()
        (self.steam / "steamcmd.exe").write_bytes(b"fixture")
        self.custom = self.base / "Custom Backups"
        self.custom.mkdir()
        self.dayz = server_folder(self.base / "DayZ Server")
        self.cases = [self.case(index, block, backup, steam)
                      for index, (block, backup, steam) in enumerate(
                          (block, backup, steam) for block in BLOCKS
                          for backup in ("none", "portable", "custom") for steam in (True, False))]

    def case(self, index: int, block: tuple[str, str | None], backup: str, steam: bool) -> dict:
        """Store settings without a DayZ folder, set the block, and capture the snapshot and the selection."""
        root = self.base / f"case-{index}"
        operations = OperationManager(OperationStore(root / "ops"))
        self.addCleanup(operations.shutdown, 2)
        built = build_repair(root, operations)
        backups = {"none": None, "portable": str(built.paths.backups), "custom": str(self.custom)}[backup]
        stored = built.settings.save(SettingsInput(**expand_location_roots({
            "dayz_root": None, "steamcmd_root": str(self.steam) if steam else None,
            "custom_backup_root": backups})), None)
        operations.block_for_missing_dayz_root(*block)
        coordinator = ApplicationCoordinator(built.settings, operations,
                                             ShutdownCoordinator(operations, StructuredLogger(root / "log.jsonl")),
                                             built.repair)
        host = HostApi(BridgeFacade(coordinator.handlers()))
        # The native picker answers in another form: forward slashes and a trailing slash
        host._set_settings_path_selector(lambda _role, _kind: str(self.dayz).replace("\\", "/") + "/")
        return {"name": f"{block[0]} / backup {backup} / steam {steam}", "operations": operations, "built": built,
                "stored": stored, "coordinator": coordinator, "host": host,
                "snapshot": coordinator.get_application_snapshot({}),
                "selection": host.select_settings_path("dayz_root")}

    def wait(self, operations: OperationManager, operation_id: str):
        """Wait until the save is terminal and return its record."""
        deadline = time.monotonic() + 5
        while operations.get(operation_id).state not in TERMINAL_STATES:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        return operations.get(operation_id)

    @staticmethod
    def script(cases: list[dict], body: str) -> str:
        """Prefix a harness body with its cases and the restart sentence."""
        return f"const CASES = {json.dumps(cases)};\nconst RESTART = {json.dumps(RESTART)};\n{body}"

    def test_the_page_request_is_a_repair_and_the_restart_sentence_shows(self) -> None:
        """Every block variant and stored folder form: shape passes, the lane saves, the page asks for the restart."""
        self.assertTrue(all(case["selection"]["success"] for case in self.cases))
        page = [{"snapshot": case["snapshot"], "selection": case["selection"]} for case in self.cases]
        output = run_shell_harness(self.script(page, CHOOSE_AND_SAVE), budget=12000)
        sent = json.loads(html.unescape(output))
        self.assertEqual(len(sent), len(self.cases), output)
        finished = []
        for case, request in zip(self.cases, sent):
            with self.subTest(case=case["name"]):
                self.assertEqual(request["revision"], case["stored"].revision)
                self.assertTrue(case["built"].repair.is_repair_shaped(request["payload"]), request["payload"])
                accepted = case["host"].save_settings(request["payload"], request["revision"])
                self.assertTrue(accepted["success"], accepted)
                record = self.wait(case["operations"], accepted["value"]["operation_id"])
                self.assertEqual(record.state.value, "SUCCEEDED", record.terminal_error)
                self.assertIs(record.result["restart_required"], True)
                saved = case["built"].settings.load()
                self.assertEqual(Path(saved.dayz_root), self.dayz)
                for field in SETTINGS_FIELDS[2:]:
                    self.assertEqual(getattr(saved, field), getattr(case["stored"], field), field)
                finished.append({"name": case["name"], "snapshot": case["coordinator"].get_application_snapshot({}),
                                 "record": case["coordinator"].get_operation({"operation_id": record.operation_id})})
        self.assertEqual(run_shell_harness(self.script(finished, FINISH), budget=12000), "PASS")


if __name__ == "__main__":
    unittest.main()
