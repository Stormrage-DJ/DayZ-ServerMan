"""Verify collision policy and selected-world remapping on disposable binary fixtures."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.domain.profile_restore_destinations import (
    RestoreOccupancy, reserve_profile_ports, select_restore_destination,
    suggest_profile_id, suggest_restore_ports,
)
from dayz_serverman.domain.profile_restore_mapping import map_mission_inventory
from dayz_serverman.domain.profiles import ProfileValidationError


class RestoreMappingTests(unittest.TestCase):
    """Test preservation and isolation as separate destination decisions."""

    def test_destination_collision_table(self) -> None:
        """Missing originals preserve identity; shared or orphan missions force isolation."""
        mission = r"mpmissions\dayzOffline.chernarusplus"
        cases = (
            (RestoreOccupancy(), "preserve_original", mission, 12),
            (RestoreOccupancy(mission_paths=(mission,), instance_ids=(1, 2, 12)),
             "allocate_new", r"mpmissions\serverman-new.chernarusplus", 3),
            (RestoreOccupancy(referenced_missions=(mission,), instance_ids=(12,)),
             "allocate_new", r"mpmissions\serverman-new.chernarusplus", 1),
            (RestoreOccupancy(mission_paths=(mission, r"MPMISSIONS\SERVERMAN-NEW.chernarusplus")),
             "allocate_new", r"mpmissions\serverman-new-2.chernarusplus", 1),
        )
        for occupancy, policy, target, instance in cases:
            with self.subTest(occupancy=occupancy):
                result = select_restore_destination(mission, 12, "new", occupancy)
                self.assertEqual((result.storage_policy, result.mission_root, result.instance_id),
                                 (policy, target, instance))

    def test_profile_suggestions_cover_records_folders_and_length_limit(self) -> None:
        """Case collisions and orphan generated folders never reuse an occupied identity."""
        for original, occupancy, expected in (
            ("test", RestoreOccupancy(), "test"),
            ("test", RestoreOccupancy(profile_ids=("TEST",)), "test-restored"),
            ("test", RestoreOccupancy(generated_ids=("test", "test-restored")), "test-restored-2"),
            ("a" * 64, RestoreOccupancy(profile_ids=("a" * 64,)), "a" * 55 + "-restored"),
        ):
            with self.subTest(original=original):
                self.assertEqual(suggest_profile_id(original, occupancy), expected)

    def test_replacement_requires_registered_consumers_and_matching_common_content(self) -> None:
        """Orphan worlds and mismatched common mission files cannot be replaced."""
        mission = r"mpmissions\dayzOffline.enoch"
        occupancy = RestoreOccupancy(profile_ids=("owner",), mission_paths=(mission,))
        kwargs = dict(storage_policy="replace_existing", affected_profile_ids=("owner",),
                      common_mission_matches=True)
        result = select_restore_destination(mission, 12, "new", occupancy, **kwargs)
        self.assertEqual((result.mission_root, result.instance_id), (mission, 12))
        for changes in ({"affected_profile_ids": ()}, {"affected_profile_ids": ("unknown",)},
                        {"common_mission_matches": False}):
            with self.subTest(changes=changes):
                with self.assertRaises(ProfileValidationError):
                    select_restore_destination(mission, 12, "new", occupancy, **{**kwargs, **changes})

    def test_rejects_unknown_ownership_occupied_id_and_invalid_mission(self) -> None:
        """Unknown installation facts never silently turn into free destinations."""
        for occupancy in (RestoreOccupancy(ownership_known=False),
                          RestoreOccupancy(profile_ids=("NEW",)),
                          RestoreOccupancy(generated_ids=("new",))):
            with self.assertRaises(ProfileValidationError):
                select_restore_destination(r"mpmissions\dayzOffline.enoch", 1, "new", occupancy)
        for mission in (r"mpmissions\world", r"mpmissions\..\world.enoch"):
            with self.assertRaises(ProfileValidationError):
                select_restore_destination(mission, 1, "new", RestoreOccupancy(), storage_policy="allocate_new")
        for instance in (True, 0, -1, "1"):
            with self.assertRaises(ProfileValidationError):
                select_restore_destination(r"mpmissions\world.enoch", instance, "new", RestoreOccupancy())

    def test_port_collisions_reserve_blocks_query_endpoints_and_exhaustion(self) -> None:
        """Suggest deterministic alternatives and never wrap beyond the UDP port range."""
        self.assertEqual(suggest_restore_ports(2302, 2305, frozenset()), (2302, 2305))
        for port in (2302, 2303, 2304, 2305):
            self.assertEqual(suggest_restore_ports(2302, 2305, frozenset({port})), (2402, 2405))
        self.assertEqual(suggest_restore_ports(2402, 2700, frozenset({2700})), (2302, 2305))
        self.assertEqual(suggest_restore_ports(65535, 65535, frozenset()), (2302, 2305))
        for game, query in ((True, 2305), (0, 2305), (2302, 65536)):
            with self.assertRaises(ProfileValidationError):
                suggest_restore_ports(game, query, frozenset())
        with self.assertRaises(ProfileValidationError):
            reserve_profile_ports(65533, 65535)
        with self.assertRaises(ProfileValidationError):
            suggest_restore_ports(2302, 2305, frozenset(range(1, 65536)))

    def test_selected_player_object_bytes_and_empty_directories_round_trip(self) -> None:
        """Prove file mapping fidelity; this does not assert DayZ engine compatibility."""
        payload = {"storage_12/players.db": bytes(range(256)),
                   "storage_12/data/objects.bin": b"\x00\xffobject\x00",
                   "storage_7/players.db": b"unrelated world", "init.c": b"mission script"}
        directories = ("storage_12", "storage_12/data", "storage_12/empty", "storage_7")
        with tempfile.TemporaryDirectory(prefix="serverman_remap_") as temporary:
            root = Path(temporary)
            source, destination = root / "original", root / "isolated"
            for name in directories:
                source.joinpath(*PureWindowsPath(name).parts).mkdir(parents=True, exist_ok=True)
            for name, data in payload.items():
                source.joinpath(*PureWindowsPath(name).parts).write_bytes(data)
            # Apply the same primitive to both file and directory inventories.
            for old, new in map_mission_inventory(directories, 12, 3).items():
                destination.joinpath(*PureWindowsPath(new).parts).mkdir(parents=True, exist_ok=True)
            for old, new in map_mission_inventory(tuple(payload), 12, 3).items():
                destination.joinpath(*PureWindowsPath(new).parts).write_bytes(
                    source.joinpath(*PureWindowsPath(old).parts).read_bytes(),
                )
            self.assertTrue((destination / "storage_3" / "empty").is_dir())
            self.assertFalse((destination / "storage_7").exists())
            for name in ("players.db", "data/objects.bin"):
                expected = payload[f"storage_12/{name}"]
                actual = destination.joinpath("storage_3", *PureWindowsPath(name).parts).read_bytes()
                self.assertEqual(hashlib.sha256(actual).digest(), hashlib.sha256(expected).digest())
            for name, data in payload.items():
                self.assertEqual(source.joinpath(*PureWindowsPath(name).parts).read_bytes(), data)

    def test_inventory_rejects_case_collisions_and_excludes_old_owner(self) -> None:
        """Treat Windows aliases as duplicate targets, including leading-zero storage aliases."""
        for paths in (("storage_1/a", "STORAGE_1/A"), ("storage_1/a", "storage_01/a"),
                      ("../escape",), ("storage_1/a:stream",)):
            with self.assertRaises(ProfileValidationError):
                map_mission_inventory(paths, 1, 2)
        self.assertEqual(map_mission_inventory((".serverman-mission-owner.json", "storage_2/a"), 1, 3), {})


if __name__ == "__main__":
    unittest.main()
