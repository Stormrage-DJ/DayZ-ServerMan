"""Cover the editor output ledger: entry shape, the D2 capture match rule and create-once storage."""

from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dayz_serverman.domain.mission_map_ledger import matches, parse_entry, refusal, restore_entry_id
from dayz_serverman.domain.mission_map_records import RecordShapeError, sealed
from dayz_serverman.domain.mission_map_values import IDENTIFIER
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, TargetClass, target_key
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_records import RecordConflict, RecordCorrupt
from tests.test_mission_map_records import OUTPUT, VANILLA, sha


# Two byte-identical vanilla missions of different profiles
FIRST = target_key(TargetClass.MISSION, Path("C:/DayZ/mpmissions/dayzOffline.main"))
SECOND = target_key(TargetClass.MISSION, Path("C:/DayZ/mpmissions/dayzOffline.test20261001"))
# Owned-value digests of vanilla and editor lootmax values
OWNED_VANILLA, OWNED_OUTPUT = "1" * 64, "2" * 64


def entry(key: str = FIRST, operation_id: str = "op-1", kind: str = "apply", **file_fields: Any) -> dict[str, Any]:
    """Return an unsealed entry where mapgroupproto.xml changed from vanilla to editor output."""
    changed = {"path": "mapgroupproto.xml", "before_sha256": sha(VANILLA), "after_sha256": sha(OUTPUT),
               "baseline_sha256": sha(VANILLA), "owned_before_sha256": OWNED_VANILLA,
               "owned_after_sha256": OWNED_OUTPUT, "owned_baseline_sha256": OWNED_VANILLA, **file_fields}
    return {"operation_id": operation_id, "operation_kind": kind, "recorded_at": "2026-10-10T12:00:00Z",
            "profile_id": "main", "target_class": "mission", "target_key": key,
            "display_path": "C:\\DayZ\\mpmissions\\dayzOffline.main", "mission_root": "mpmissions\\dayzOffline.main",
            "output": kind in ("apply", "profile_restore"),
            "carried_operation_id": "op-0" if kind == "profile_restore" else None, "files": [changed]}


class MatchRuleTests(unittest.TestCase):
    """Verify the D2 match rule of the capture check."""

    def test_identical_vanilla_missions_do_not_refuse_each_other(self) -> None:
        """An apply on the first mission does not match the vanilla bytes of the second (Main and test20261001)."""
        first = parse_entry(sealed(entry()), MISSION_FILE_SET)
        self.assertFalse(matches(first, "mapgroupproto.xml", sha(VANILLA), OWNED_VANILLA))

    def test_copied_output_matches_by_whole_file_and_changed_output_by_owned_values(self) -> None:
        """Copied output matches its after digest; output edited elsewhere matches its owned-value digest."""
        first = parse_entry(sealed(entry()), MISSION_FILE_SET)
        self.assertTrue(matches(first, "mapgroupproto.xml", sha(OUTPUT), "f" * 64))
        self.assertTrue(matches(first, "mapgroupproto.xml", sha(b"other writer changed a medical group"),
                                OWNED_OUTPUT))

    def test_unchanged_absent_null_and_restore_never_match(self) -> None:
        """Files the entry did not change, absent files, null digests and restore entries never match."""
        first = parse_entry(sealed(entry()), MISSION_FILE_SET)
        self.assertFalse(matches(first, "mapgrouppos.xml", sha(OUTPUT), OWNED_OUTPUT))
        self.assertFalse(matches(first, "mapgroupproto.xml", None, None))
        for kind in ("restore_original", "restore_previous"):
            restore = parse_entry(sealed(entry(kind=kind)), MISSION_FILE_SET)
            self.assertFalse(matches(restore, "mapgroupproto.xml", sha(OUTPUT), OWNED_OUTPUT))
        # Output that equals the before or baseline state is not editor output
        back = parse_entry(sealed(entry(after_sha256=sha(VANILLA), before_sha256=sha(OUTPUT),
                                        owned_after_sha256=OWNED_VANILLA, owned_before_sha256=OWNED_OUTPUT)),
                           MISSION_FILE_SET)
        self.assertFalse(matches(back, "mapgroupproto.xml", sha(VANILLA), OWNED_VANILLA))
        # A file the apply deleted has a null after digest and never matches
        deleted = parse_entry(sealed(entry(after_sha256=None, owned_after_sha256=None)), MISSION_FILE_SET)
        self.assertFalse(matches(deleted, "mapgroupproto.xml", None, None))

    def test_refusal_names_the_folder(self) -> None:
        """A refusal names the operation, the display path and mission root, and a profile that still exists."""
        first = parse_entry(sealed(entry()), MISSION_FILE_SET)
        reported = refusal(first, {"main"})
        self.assertEqual((reported["display_path"], reported["mission_root"], reported["profile_id"]),
                         ("C:\\DayZ\\mpmissions\\dayzOffline.main", "mpmissions\\dayzOffline.main", "main"))
        self.assertIsNone(refusal(first, set())["profile_id"])


class EntryShapeTests(unittest.TestCase):
    """Verify the entry shape rules and the derived restore operation ID."""

    def test_shape_rules(self) -> None:
        """The output flag follows the kind; only profile-restore entries name a carried operation; files changed."""
        self.assertTrue(parse_entry(sealed(entry(kind="profile_restore")), MISSION_FILE_SET)["output"])
        edits = [
            lambda raw: raw.update(output=False),
            lambda raw: raw.update(carried_operation_id="op-0"),
            lambda raw: raw["files"][0].update(path="init.c"),
            lambda raw: raw["files"].append(dict(raw["files"][0])),
            lambda raw: raw["files"][0].update(after_sha256=sha(VANILLA), owned_after_sha256=OWNED_VANILLA),
            lambda raw: raw.update(files=[]),
            lambda raw: raw.pop("display_path"),
            lambda raw: raw.update(operation_kind="adopt"),
        ]
        for edit in edits:
            raw = copy.deepcopy(entry())
            edit(raw)
            with self.subTest(raw=raw), self.assertRaises(RecordShapeError):
                parse_entry(sealed(raw), MISSION_FILE_SET)
        # A file whose owned values changed while its whole digest stayed is a change
        owned_only = entry(after_sha256=sha(VANILLA))
        self.assertTrue(parse_entry(sealed(owned_only), MISSION_FILE_SET)["files"])

    def test_restore_entry_id_is_derived_and_unique(self) -> None:
        """Each restore and carried record pair gets its own path-safe operation ID."""
        derived = restore_entry_id("restore-1", "op-2")
        self.assertEqual(derived, restore_entry_id("restore-1", "op-2"))
        self.assertRegex(derived, "^pr-[0-9a-f]{32}$")
        self.assertIsNotNone(IDENTIFIER.fullmatch(derived))
        self.assertNotEqual(derived, restore_entry_id("restore-2", "op-2"))
        self.assertNotEqual(derived, restore_entry_id("restore-1", "op-3"))


class LedgerStoreTests(unittest.TestCase):
    """Verify create-once entries and the lookup across targets of one class."""

    def setUp(self) -> None:
        """Create a temporary editor area with a ledger."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_ledger_")
        self.area = Path(self.temp.name) / "mission-map"
        self.ledger = LedgerStore(self.area)

    def tearDown(self) -> None:
        """Remove the temporary folders."""
        self.temp.cleanup()

    def test_create_once(self) -> None:
        """An identical rewrite does nothing; a different entry for the same key and operation is refused."""
        created = self.ledger.create(entry())
        self.assertEqual(self.ledger.create(entry()), created)
        self.assertRaises(RecordConflict, self.ledger.create, entry(owned_after_sha256="3" * 64))
        self.assertEqual(self.ledger.read(FIRST, "op-1"), created)
        self.assertTrue((self.area / "ledger" / FIRST / "op-1.json").is_file())

    def test_lookup_across_targets_of_one_class(self) -> None:
        """An entry of the first mission refuses capture of its copied output on the second, never the vanilla."""
        self.ledger.create(entry())
        self.ledger.create(dict(entry(SECOND, "op-9"), target_class="runtime",
                                files=[dict(entry()["files"][0], path="AI_Bandits/DynamicAIB.json")]))
        found = self.ledger.entries(TargetClass.MISSION, "mapgroupproto.xml")
        self.assertEqual([item["operation_id"] for item in found], ["op-1"])
        self.assertEqual(self.ledger.capture_matches(TargetClass.MISSION, "mapgroupproto.xml",
                                                     sha(VANILLA), OWNED_VANILLA), [])
        copied = self.ledger.capture_matches(TargetClass.MISSION, "mapgroupproto.xml", sha(OUTPUT), None)
        self.assertEqual([item["target_key"] for item in copied], [FIRST])
        self.assertEqual(self.ledger.entries(TargetClass.RUNTIME, "mapgroupproto.xml"), [])

    def test_profile_deletion_keeps_entries(self) -> None:
        """Removing a profile's association folders leaves the ledger as it was."""
        self.ledger.create(entry())
        profiles = self.area / "profiles" / "main"
        profiles.mkdir(parents=True)
        shutil.rmtree(profiles)
        self.assertEqual(len(self.ledger.entries(TargetClass.MISSION, "mapgroupproto.xml")), 1)

    def test_entry_under_another_path_is_corrupt(self) -> None:
        """An entry copied under another target folder is not trusted."""
        self.ledger.create(entry())
        moved = self.area / "ledger" / SECOND
        moved.mkdir(parents=True)
        (moved / "op-1.json").write_bytes((self.area / "ledger" / FIRST / "op-1.json").read_bytes())
        self.assertRaises(RecordCorrupt, self.ledger.entries, TargetClass.MISSION, "mapgroupproto.xml")


if __name__ == "__main__":
    unittest.main()
