"""Workshop recovery tests for child absence proof before clearing blocks."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.composition import build_composition
from dayz_serverman.adapters.windows.process_tree import ChildEvidence, ProcessIdentity, WindowsChildProbe
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.workshop_recovery import inspect_workshop_recovery


class FakeProbe:
    """Child probe stand-in with a fixed absence answer."""
    def __init__(self, absent: bool) -> None:
        """Store the scripted absence answer."""
        self.absent = absent
        self.seen = []

    def is_absent(self, evidence):
        """Record the evidence and answer with the scripted absence flag."""
        self.seen.append(evidence)
        return self.absent


def interrupted(operation_id: str = "update-one") -> dict[str, object]:
    """Build a persisted running update record with a launched child tree."""
    child = ChildEvidence(
        71, "fake:71",
        (ProcessIdentity(71, "fake:71"), ProcessIdentity(72, "fake:72")),
        "Local\\DayZServerMan-SteamCMD-fixture",
    )
    return {
        "schema_version": 1, "revision": 4, "operation_id": operation_id,
        "kind": "UPDATE_WORKSHOP_ITEMS", "state": "RUNNING",
        "accepted_at": "2026-09-27T00:00:00.000+00:00", "started_at": None,
        "finished_at": None, "cancellation_requested": False,
        "progress_percent": 30, "progress_phase": "steamcmd",
        "result": {"child_state": "CHILD_LAUNCHED", "result_state": "UPDATE_RESULT_UNKNOWN",
                   "child": child.to_dict()},
        "terminal_error": None,
    }


class WorkshopRecoveryTests(unittest.TestCase):
    """Interrupted update recovery contracts for absence proof and mutation blocks."""
    def test_nonterminal_update_becomes_durable_recovery_block_evidence(self) -> None:
        """A nonterminal update becomes durable recovery block evidence."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "update-one.json"
            path.write_text(json.dumps(interrupted()), encoding="utf-8")
            # Prove the child tree absent so recovery may clear the record
            probe = FakeProbe(True)
            result = inspect_workshop_recovery(root, probe)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(result["blocked"])
            self.assertTrue(result["absence_proven"]["update-one"])
            self.assertEqual(stored["state"], "RECOVERY_REQUIRED")
            self.assertEqual(stored["terminal_error"]["code"], "UPDATE_RESULT_UNKNOWN")
            self.assertEqual(probe.seen[0].creation_identity, "fake:71")
            self.assertEqual([item.process_id for item in probe.seen[0].members], [71, 72])

    def test_root_absent_but_descendant_alive_fails_closed(self) -> None:
        """A live descendant keeps the tree non-absent."""
        evidence = ChildEvidence(
            71, "root", (ProcessIdentity(71, "root"), ProcessIdentity(72, "child")), None)
        probe = WindowsChildProbe()
        # Force the root job absent while one descendant stays alive
        probe._job_absent = lambda _name: True  # type: ignore[method-assign]
        probe._identity_absent = lambda item: item.process_id == 71  # type: ignore[method-assign]
        self.assertFalse(probe.is_absent(evidence))

    def test_startup_absence_proof_waits_for_every_descendant(self) -> None:
        """The absence proof stays incomplete while any descendant is alive."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "update-one.json"
            path.write_text(json.dumps(interrupted()), encoding="utf-8")
            # A live descendant must keep the absence proof incomplete
            probe = FakeProbe(False)
            first = inspect_workshop_recovery(root, probe)
            self.assertFalse(first["absence_proven"]["update-one"])
            self.assertTrue(first["blocked"])

    def test_fresh_composition_blocks_mutations_but_keeps_queries_available(self) -> None:
        """A fresh composition blocks mutations while queries stay available."""
        with tempfile.TemporaryDirectory() as temporary:
            manager = Path(temporary) / "Manager"
            paths = PortablePaths.from_root(manager)
            paths.create_layout()
            # Plant an interrupted update before the composition starts
            record = paths.operations / "update-one.json"
            record.write_text(json.dumps(interrupted()), encoding="utf-8")
            composition = build_composition(manager)
            try:
                # Queries stay available under the recovery block
                snapshot = composition.bridge.dispatch({
                    "contract_version": 1, "request_id": "snapshot",
                    "method": "get_application_snapshot", "parameters": {},
                })
                self.assertTrue(snapshot["success"])
                self.assertIn("interrupted SteamCMD", snapshot["value"]["mutation_block"])
                # Mutations are rejected while the recovery block stands
                rejected = composition.bridge.dispatch({
                    "contract_version": 1, "request_id": "save",
                    "method": "save_steam_settings", "parameters": {
                        "expected_revision": 0, "authentication_mode": "ANONYMOUS",
                        "account_name": None,
                    },
                })
                self.assertFalse(rejected["success"])
                self.assertEqual(rejected["error"]["code"], "MUTATION_CONFLICT")
            finally:
                composition.operations.shutdown(2)


if __name__ == "__main__":
    unittest.main()
