"""Verify the set-aside handler of invalid saved plans (leaf 5.3, D2): dated name, nothing deleted, revision 0."""

from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from dayz_serverman.bridge.contracts import ErrorCode, ensure_json_value
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.domain.mission_map_plan import canonical_json
from dayz_serverman.repositories.mission_map_layout import association_folder
from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.test_mission_map_editor_load_save import CoordinatorFixture


# The fixed clock of the service fixture: 2026-10-10 13:00:00 UTC
DATED_NAME = "plan.invalid-20261010T130000Z.json"
# A leftover of an interrupted plan save; set aside never touches it
LEFTOVER = ".plan.json.0123abcd.tmp"


class SetAsideTests(CoordinatorFixture):
    """Call the real coordinator over the real plan service and store."""

    def setUp(self) -> None:
        """Keep the active association folder of profile main."""
        super().setUp()
        self.key = self.service.active_association("main")["mission_key"]
        self.folder = association_folder(self.area, "main", self.key)

    def set_aside(self, key: object = None) -> dict:
        """Call the complete set-aside request of profile main directly."""
        return self.coordinator.set_aside_mission_map_plan(
            {"profile_id": "main", "mission_key": self.key if key is None else key})

    def invalid_samples(self) -> list[tuple[bytes, str]]:
        """Return one stored record for each invalid saved-data state."""
        plan = self.draft()
        newer = dict(copy.deepcopy(plan), version=2)
        return [(b"{bad JSON", "CORRUPT"),
                (canonical_json({"schema_version": 2, "revision": 3, "plan": plan}), "FUTURE_SCHEMA"),
                (canonical_json({"schema_version": 1, "revision": 3, "plan": newer}), "NEWER_PLAN"),
                (canonical_json({"schema_version": 1, "revision": 3, "plan": {"format": 1}}), "INVALID_PLAN")]

    def contents(self) -> dict[str, bytes]:
        """Return every file of the association folder with its bytes."""
        return {path.name: path.read_bytes() for path in sorted(self.folder.iterdir())}

    def test_invalid_plan_moves_to_its_dated_name_and_nothing_is_deleted(self) -> None:
        """Each invalid state moves its exact bytes to the D2 name; leftovers and other records stay."""
        for number, (data, state) in enumerate(self.invalid_samples()):
            with self.subTest(state=state):
                moment = datetime(2026, 10, 10, 13, 0, number, tzinfo=timezone.utc)
                self.service._clock = lambda moment=moment: moment
                self.folder.mkdir(parents=True, exist_ok=True)
                (self.folder / "plan.json").write_bytes(data)
                (self.folder / LEFTOVER).write_bytes(b"interrupted")
                (self.folder / "applied.json").write_bytes(b"other record")
                before = self.contents()
                self.assertEqual(self.load()["state"], state)
                result = self.set_aside()
                ensure_json_value(result)
                name = f"plan.invalid-20261010T13000{number}Z.json"
                self.assertEqual(result, {"set_aside": name, "association": self.service.active_association("main")})
                # Only the plan record changed its name; every byte is still in the folder
                after = self.contents()
                expected = {**{key: value for key, value in before.items() if key != "plan.json"}, name: data}
                self.assertEqual(after, expected)

    def test_after_set_aside_a_new_empty_plan_starts_at_revision_zero(self) -> None:
        """The next load is a new, writable, empty plan; its first save is revision 0."""
        self.add_header()
        self.folder.mkdir(parents=True)
        (self.folder / "plan.json").write_bytes(b"{bad JSON")
        self.assertEqual(self.load()["read_only_reason"], "invalid_saved_data")
        self.assertEqual(self.set_aside()["set_aside"], DATED_NAME)
        loaded = self.load()
        self.assertEqual((loaded["state"], loaded["revision"], loaded["read_only"], loaded["read_only_reason"]),
                         ("MISSING", None, False, None))
        self.assertEqual((loaded["plan"]["objects"], loaded["plan"]["association"]["mission_key"]), ([], self.key))
        plan = copy.deepcopy(loaded["plan"])
        self.assertEqual(self.save(plan, None)["revision"], 0)
        self.assertEqual((self.folder / DATED_NAME).read_bytes(), b"{bad JSON")

    def test_valid_or_missing_plan_is_refused_and_unchanged(self) -> None:
        """A missing plan creates no folder; a valid plan keeps its bytes; both answer plan_not_invalid."""
        error = self.refused(self.set_aside, ErrorCode.REVISION_CONFLICT, "plan_not_invalid")
        self.assertFalse(error.retryable)
        self.assertFalse(self.area.exists())
        self.save(self.draft(), None)
        before = self.contents()
        self.refused(self.set_aside, ErrorCode.REVISION_CONFLICT, "plan_not_invalid")
        self.assertEqual(self.contents(), before)
        self.assertEqual(self.load()["revision"], 0)

    def test_an_existing_dated_name_is_never_replaced(self) -> None:
        """A second set aside in the same second refuses as a retryable storage failure and keeps both files."""
        self.folder.mkdir(parents=True)
        (self.folder / "plan.json").write_bytes(b"{bad JSON")
        (self.folder / DATED_NAME).write_bytes(b"earlier evidence")
        before = self.contents()
        error = self.refused(self.set_aside, ErrorCode.STORAGE_FAILURE, "storage_failure")
        self.assertTrue(error.retryable)
        self.assertEqual(self.contents(), before)

    def test_another_mission_key_bad_parameters_and_a_path_change_are_refused(self) -> None:
        """A stale or missing key, unknown fields and the path-change state leave the invalid record in place."""
        self.folder.mkdir(parents=True)
        (self.folder / "plan.json").write_bytes(b"{bad JSON")
        for key in ("0" * 32, 7, False):
            with self.subTest(key=key):
                self.refused(lambda: self.set_aside(key), ErrorCode.PROFILE_CONTEXT_MISMATCH, "association_mismatch")
        self.refused(lambda: self.coordinator.set_aside_mission_map_plan({"profile_id": "main"}),
                     ErrorCode.INVALID_REQUEST, "invalid_document")
        with mock.patch.object(self.profiles, "read", side_effect=ProfileNotFound("gone")):
            self.refused(self.set_aside, ErrorCode.NOT_FOUND, "profile_not_found")
        # A moved mission keeps its earlier plan read only; set aside waits for adoption
        self.old_folder("old-root")
        self.refused(self.set_aside, ErrorCode.PROFILE_CONTEXT_MISMATCH, "mission_path_changed")
        self.assertEqual(self.contents(), {"plan.json": b"{bad JSON"})

    def test_the_bridge_envelope_carries_the_result_and_the_refusal(self) -> None:
        """Through the strict bridge the answer is JSON and a refusal keeps its closed reason."""
        bridge = BridgeFacade(self.coordinator.handlers())
        request = {"contract_version": 1, "request_id": "editor-1", "method": "set_aside_mission_map_plan",
                   "parameters": {"profile_id": "main", "mission_key": self.key}}
        refused = bridge.dispatch(request)
        self.assertEqual((refused["success"], refused["error"]["code"], refused["error"]["details"]["reason"]),
                         (False, "REVISION_CONFLICT", "plan_not_invalid"))
        self.folder.mkdir(parents=True)
        (self.folder / "plan.json").write_bytes(b"{bad JSON")
        answer = bridge.dispatch(request)
        self.assertTrue(answer["success"])
        self.assertEqual(answer["value"]["set_aside"], DATED_NAME)
        self.assertTrue(Path(self.folder / DATED_NAME).is_file())


if __name__ == "__main__":
    unittest.main()
