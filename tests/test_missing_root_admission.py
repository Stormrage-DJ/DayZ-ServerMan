"""QF-069 (D19): the lane lets a repair save pass only "no DayZ server folder" blocks; tests (a) to (h) and (v)."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    from tests.ui_harness_support import ROOT
except ModuleNotFoundError:
    from ui_harness_support import ROOT

from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import (
    TERMINAL_STATES, OperationFailure, QueueUnavailable,
)
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.observability.structured_log import StructuredLogger

PACKAGE = ROOT / "runnable" / "src" / "python" / "dayz_serverman"
# The three startup reasons that carry the cause, with their owners
TAGGED = (("Mutations are blocked by unresolved mod publication.", None),
          ("Direct profile restore recovery requires a configured DayZ root.", None),
          ("Backup restore recovery requires a configured DayZ root.", "RESTORE_BACKUP"))
# Blocks of other causes: SteamCMD, publication not stopped, migration
OTHER = (("Mutations are blocked by an interrupted SteamCMD update with an unknown result.", None),
         ("Mutations are blocked by an interrupted mod publication while the server is not proven stopped.", None),
         ("Mutations are blocked by unresolved migration recovery.", None))
# Kinds that must never pass the blocks
OTHER_KINDS = ("PUBLISH_MODS", "APPLY_MODS_AND_RESTART", "START_SERVER", "STOP_SERVER", "RESTORE_BACKUP",
               "CREATE_PROFILE")


class MissingRootAdmissionTests(unittest.TestCase):
    """The admission rule of recovery_blocks.py, driven through the operation manager."""

    def setUp(self) -> None:
        """Create a lane with a log file below a disposable folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_admission_")
        self.addCleanup(temporary.cleanup)
        self.log = Path(temporary.name) / "manager.jsonl"
        self.manager = OperationManager(OperationStore(Path(temporary.name) / "ops"), queue_limit=1,
                                        logger=StructuredLogger(self.log))
        self.addCleanup(self.manager.shutdown, 2)
        self.seen: list[bool] = []
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)

    def work(self, context) -> dict:
        """Record whether the lane admitted this work through the blocks."""
        self.seen.append(context.admitted_through_recovery_block)
        return {}

    def wait(self, operation_id: str):
        """Wait until the operation is terminal and return its record."""
        deadline = time.monotonic() + 5
        while self.manager.get(operation_id).state not in TERMINAL_STATES:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        return self.manager.get(operation_id)

    def repair(self, work=None):
        """Submit a repair save."""
        return self.manager.submit("SAVE_SETTINGS", work or self.work, repairs_missing_dayz_root=True)

    def tag_all(self) -> None:
        """Set the three cause-tagged blocks."""
        for reason, owner in TAGGED:
            self.manager.block_for_missing_dayz_root(reason, owner)

    def assert_refused(self, reason: str, owner: str | None) -> None:
        """A repair save is refused with the latest block's reason and owner."""
        with self.assertRaises(QueueUnavailable) as refused:
            self.repair()
        self.assertEqual((str(refused.exception), refused.exception.reason, refused.exception.owner),
                         (reason, "RECOVERY_BLOCK", owner))

    def test_a_repair_save_passes_only_tagged_blocks(self) -> None:
        """(a) Every block has the cause: the repair save is admitted and the context says so."""
        self.tag_all()
        self.assertEqual(self.wait(self.repair().operation_id).state.value, "SUCCEEDED")
        self.assertEqual(self.seen, [True])
        self.assertEqual(self.manager.recovery_block, TAGGED[-1][0])

    def test_b_mixed_blocks_refuse_in_both_orders(self) -> None:
        """(b) One block of another cause refuses with the latest reason and owner, in either order."""
        for other in (*OTHER, ("Mutations are blocked until restore recovery is inspected.", "RESTORE_BACKUP")):
            for tagged_first in (True, False):
                with self.subTest(other=other[0], tagged_first=tagged_first):
                    self.manager.clear_recovery_block()
                    if tagged_first:
                        self.tag_all()
                        self.manager.block_for_recovery(*other)
                        self.assert_refused(*other)
                    else:
                        self.manager.block_for_recovery(*other)
                        self.tag_all()
                        self.assert_refused(*TAGGED[-1])
        self.assertEqual(self.seen, [])

    def test_c_the_flag_belongs_to_the_settings_save_only(self) -> None:
        """(c) The flag with another kind is a programming error, with or without blocks."""
        for blocked in (False, True):
            if blocked:
                self.tag_all()
            for kind in OTHER_KINDS:
                with self.subTest(kind=kind, blocked=blocked), self.assertRaises(ValueError):
                    self.manager.submit(kind, self.work, repairs_missing_dayz_root=True)
        # Without the flag the settings save is refused like every other change
        with self.assertRaises(QueueUnavailable):
            self.manager.submit("SAVE_SETTINGS", self.work)
        self.assertEqual(self.seen, [])

    def test_d_the_flag_without_a_block_is_a_normal_admission(self) -> None:
        """(d) No block: admitted as usual, and the context flag is False."""
        self.assertEqual(self.wait(self.repair().operation_id).state.value, "SUCCEEDED")
        self.assertEqual(self.seen, [False])

    def test_e_draining_and_a_full_queue_still_refuse(self) -> None:
        """(e) The bypass does not skip the shutdown test or the queue limit."""
        self.tag_all()
        started = threading.Event()

        def hold(_context) -> dict:
            """Hold the lane until the test ends."""
            started.set()
            self.gate.wait(5)
            return {}
        self.repair(hold)
        self.assertTrue(started.wait(5))
        self.repair()
        with self.assertRaises(QueueUnavailable) as full:
            self.repair()
        self.assertEqual(full.exception.reason, "QUEUE_FULL")
        self.manager.begin_shutdown()
        with self.assertRaises(QueueUnavailable) as draining:
            self.repair()
        self.assertEqual(draining.exception.reason, "SHUTTING_DOWN")

    def test_f_a_lane_set_block_has_no_cause(self) -> None:
        """(f) A failed operation that requires recovery blocks without the cause; a repair save is refused."""
        def fail(_context) -> dict:
            """Fail and require recovery."""
            raise OperationFailure("PUBLICATION_FAILED", "Mod publication requires recovery.", recovery_required=True)
        self.assertEqual(self.wait(self.manager.submit("PUBLISH_MODS_AND_KEYS", fail).operation_id).state.value,
                         "RECOVERY_REQUIRED")
        self.assert_refused("Mod publication requires recovery.", "PUBLISH_MODS_AND_KEYS")
        self.tag_all()
        self.assert_refused(*TAGGED[-1])
        self.assertEqual(self.seen, [])

    def test_g_setting_a_tagged_block_again_without_the_cause_ends_its_qualification(self) -> None:
        """(g) The same reason and owner, set again as an ordinary block, no longer qualifies."""
        self.tag_all()
        self.manager.block_for_recovery(*TAGGED[0])
        self.assert_refused(*TAGGED[0])

    def test_h_one_bypass_record_is_logged(self) -> None:
        """(h) One WARNING record names the kind and the active reasons."""
        self.tag_all()
        self.wait(self.repair().operation_id)
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        bypass = [record for record in records if record["event"] == "operation_lane.recovery_block_bypass"]
        self.assertEqual([(record["level"], record["fields"]["kind"]) for record in bypass],
                         [("WARNING", "SAVE_SETTINGS")])
        self.assertEqual(bypass[0]["fields"]["reasons"], [reason for reason, _owner in TAGGED])

    def test_v_only_the_settings_save_and_the_three_startup_paths_use_the_rule(self) -> None:
        """(v) Static: one place passes the flag, three startup places set the cause."""
        flags, causes = {}, {}
        for path in PACKAGE.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if found := re.findall(r"repairs_missing_dayz_root=", source):
                flags[path.name] = len(found)
            if found := re.findall(r"\.block_for_missing_dayz_root\(", source):
                causes[path.name] = len(found)
        self.assertEqual(flags, {"coordinator.py": 1})
        self.assertEqual(causes, {"mod_publication_startup.py": 1, "startup_recoveries.py": 2})


if __name__ == "__main__":
    unittest.main()
