"""Ownership record through real compositions (A6): the observer reads it, an owner adopts it, D11 holds."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_fixtures import PROFILE_ID, dispatch, populate, tree_hashes  # noqa: E402
from dayz_serverman.adapters.steam_query import SteamQueryProbe  # noqa: E402
from dayz_serverman.adapters.windows.processes import WindowsProcessInventory  # noqa: E402
from dayz_serverman.application.lifecycle_coordinator import OTHER_PROFILE_RUNNING  # noqa: E402
from dayz_serverman.application.lifecycle_ownership import canonical_manager_root  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.lifecycle import InventorySnapshot, ProcessObservation, canonical_process_path  # noqa: E402
from dayz_serverman.repositories.server_ownership import OWNERSHIP_FILE  # noqa: E402
from dayz_serverman.session_observer import open_observer_session  # noqa: E402

PID, CREATED = 4242, 133_000_000_000_000_000


class CompositionOwnershipTests(unittest.TestCase):
    """A record that another session wrote, read by an observer composition and by an owner composition."""

    def setUp(self) -> None:
        """Populate a manager root and write a record of a server that the patched inventory shows running."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_ownership_sessions_")
        self.addCleanup(temporary.cleanup)
        self.manager, self.dayz = populate(Path(temporary.name))
        self.executable = canonical_process_path(self.dayz / "Bin" / "DayZ Server_x64.exe")
        self.record = self.manager / "data" / OWNERSHIP_FILE
        self.record.write_text(json.dumps({
            "schema_version": 1, "revision": 0, "manager_root": canonical_manager_root(self.manager),
            "server": {"pid": PID, "executable_path": self.executable, "creation_time_ns": CREATED,
                       "launch_token": "token", "profile_id": PROFILE_ID, "started_after_ns": 1,
                       "query_port": 27016, "rpt_directory": None, "session_id": "session-a"},
        }), encoding="utf-8")
        # The server of the record runs; no readiness query leaves the test
        running = InventorySnapshot((ProcessObservation(PID, self.executable, CREATED),))
        for target, name, value in ((WindowsProcessInventory, "candidates", lambda _self, _path: running),
                                    (SteamQueryProbe, "information", lambda _self, _port: None)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_observer_adopts_in_memory_ignores_staging_and_writes_nothing(self) -> None:
        """server status names the recorded profile; a staging file beside the record changes nothing (4.2.1)."""
        staging = self.record.with_name(f".{self.record.name}.{uuid.uuid4().hex}.tmp")
        staging.write_text('{"partial": ', encoding="utf-8")
        before = tree_hashes(self.manager, self.dayz)
        session = open_observer_session(self.manager)
        try:
            status = session.call("get_server_status", {})
        finally:
            session.close()
        self.assertEqual((status["state"], status["profile_id"], status["readiness"]),
                         ("RUNNING_MANAGED", PROFILE_ID, "UNRESPONSIVE"))
        self.assertEqual(tree_hashes(self.manager, self.dayz), before)

    def test_an_owner_composition_adopts_and_refuses_another_profile_d11(self) -> None:
        """The owner reports the server as managed with its profile; a stop for another profile is refused."""
        composition = build_composition(self.manager)
        try:
            status = dispatch(composition, "get_server_status", {})["value"]
            self.assertEqual((status["state"], status["profile_id"]), ("RUNNING_MANAGED", PROFILE_ID))
            # OD3: the adopted server does not hold the window open
            self.assertTrue(composition.lifecycle.shutdown_safe())
            refused = dispatch(composition, "stop_server", {
                "profile_id": "chernarus-main", "expected_profile_revision": 0,
                "expected_settings_revision": 0, "backup_after_stop": False})
            self.assertEqual((refused["error"]["code"], refused["error"]["message"]),
                             ("INVALID_REQUEST", OTHER_PROFILE_RUNNING))
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def test_a_record_of_another_manager_root_is_not_adopted(self) -> None:
        """The same server under another manager root's record stays outside this manager."""
        document = json.loads(self.record.read_text(encoding="utf-8"))
        document["manager_root"] = canonical_manager_root(self.manager.parent / "Elsewhere")
        self.record.write_text(json.dumps(document), encoding="utf-8")
        session = open_observer_session(self.manager)
        try:
            self.assertEqual(session.call("get_server_status", {})["state"], "RUNNING_EXTERNAL")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
