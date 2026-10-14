"""Verify Inspect building capacity: object answers, point reason, base, building pass and staleness (6.9, R14)."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dayz_serverman.application import mission_map_inspection_run
from dayz_serverman.bridge.contracts import ensure_json_value
from dayz_serverman.domain.mission_map_loot_report_texts import CAPACITY_REASON_TEXTS
from dayz_serverman.repositories.mission_map_mapgroup_read import DIRT, POS, PROTO
from dayz_serverman.repositories.mission_map_object_capacity import POINT_TARGET, object_capacity
from dayz_serverman.repositories.mission_map_read_pass import MissionReadPass
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from tests.mission_map_inspection_support import InspectionFixture, StepClock, write_mission
from tests.mission_map_mapgroup_support import (
    BASELINE, DIRTS, INSIDE_SHED, PLANNER, PLANNER_POS, POSITIONS, PROTOTYPE, build, plan, zone,
)
from tests.test_mission_map_plan import circle, encounter
from tests.test_mission_map_records import baseline, sha

# An unknown placement inside the test zone, and a shed placement in mapgroupdirt.xml inside it
UNKNOWN = PLANNER_POS.replace(b"PlannerRich_00001", b"Land_Nowhere")
DIRT_SHED = DIRTS.replace(b"</map>", INSIDE_SHED + b"</map>")


def by_source(entries: list[dict]) -> dict[tuple, dict]:
    """Return the entries by source and adjustment."""
    return {(entry["source"], entry["adjustment"]): entry for entry in entries}


class ObjectCapacityTests(unittest.TestCase):
    """The pure capacity of the buildings that one object covers."""

    def test_covered_kept_and_unresolved_placements(self) -> None:
        """Covered groups get the 6.9 entry; planner content is kept; an unknown name has its reason."""
        value = plan(25, zone("rich"))
        base = dict(BASELINE, **{POS: POSITIONS.replace(PLANNER_POS, PLANNER_POS + UNKNOWN)})
        entries = by_source(object_capacity(value, "zone", base))
        self.assertEqual(set(entries), {("Land_City_Hospital", 75), ("Land_Shed", 75), ("PlannerRich_00001", 75),
                                        ("Land_Nowhere", 75)})
        clones = {clone.source[1]: clone.name for clone in build(value).clones}
        hospital = entries[("Land_City_Hospital", 75)]
        self.assertEqual((hospital["placements"], hospital["kept"], hospital["reason"]), (1, None, None))
        capacity = hospital["capacity"]
        self.assertEqual((capacity["mechanism"], capacity["generated_group"], capacity["zones"]),
                         ("generated", clones["Land_City_Hospital"], ["zone"]))
        self.assertEqual((capacity["group"]["requested"], capacity["group"]["realized"]), ("7", 7))
        self.assertEqual([item["point_limited"] for item in capacity["containers"]], [True, True])
        self.assertEqual((capacity["achievable"], capacity["qualified"]), (3, True))
        planner = entries[("PlannerRich_00001", 75)]
        self.assertEqual((planner["kept"], planner["capacity"]["mechanism"], planner["capacity"]["class"]),
                         ("planner_content", "none", "no_change"))
        unknown = entries[("Land_Nowhere", 75)]
        self.assertEqual((unknown["reason"], unknown["capacity"]), ("source_missing", None))
        ensure_json_value(list(entries.values()))

    def test_objects_without_covered_placements(self) -> None:
        """A zone away from every placement and an encounter without linked loot cover no building."""
        self.assertEqual(object_capacity(plan(25, zone("rich", 300.0, 700.0, 20.0)), "zone", BASELINE), [])
        self.assertEqual(object_capacity(plan(25, encounter()), "e1", BASELINE), [])
        self.assertEqual(object_capacity(plan(25, zone("rich")), "zone", {PROTO: PROTOTYPE}), [])

    def test_a_normal_zone_follows_the_ordinary_group(self) -> None:
        """Adjustment 0 under global 0 keeps the baseline ceilings in the ordinary group."""
        hospital = by_source(object_capacity(plan(0, zone("normal")), "zone", BASELINE))[("Land_City_Hospital", 0)]
        self.assertEqual((hospital["capacity"]["mechanism"], hospital["capacity"]["class"],
                          hospital["capacity"]["generated_group"]), ("ordinary", "no_change", None))

    def test_a_dirt_placement_that_needs_a_generated_group_has_the_apply_reason(self) -> None:
        """Apply cannot move an existing Dirt binding, so its entry has no capacity."""
        base = dict(BASELINE, **{POS: POSITIONS.replace(INSIDE_SHED, b""), DIRT: DIRT_SHED})
        shed = by_source(object_capacity(plan(0, zone("rich")), "zone", base))[("Land_Shed", 50)]
        self.assertEqual((shed["reason"], shed["capacity"]), ("dirt_placement_not_movable", None))

    def test_global_value_rules_follow_6_8(self) -> None:
        """A cancelling zone still affects planner content; a Normal zone follows the global group (QA S4, S5)."""
        planner = by_source(object_capacity(plan(-50, zone("rich")), "zone", BASELINE))[("PlannerRich_00001", 0)]
        self.assertEqual(planner["kept"], "planner_content")
        hospital = by_source(object_capacity(plan(25, zone("normal")), "zone", BASELINE))[("Land_City_Hospital", 25)]
        self.assertEqual((hospital["capacity"]["mechanism"], hospital["capacity"]["class"],
                          hospital["capacity"]["generated_group"]), ("ordinary", "changed", None))

    def test_kept_planner_placement_without_its_group(self) -> None:
        """6.8 keeps it without a refusal, so the entry has the kept reason only (QA F3)."""
        base = dict(BASELINE, **{PROTO: PROTOTYPE.replace(PLANNER, b"")})
        kept = by_source(object_capacity(plan(25, zone("rich")), "zone", base))[("PlannerRich_00001", 75)]
        self.assertEqual((kept["kept"], kept["reason"], kept["capacity"]), ("planner_content", None, None))
        unaffected = by_source(object_capacity(plan(0, zone("normal")), "zone", base))[("PlannerRich_00001", 0)]
        self.assertEqual((unaffected["kept"], unaffected["reason"]), (None, "source_missing"))

    def test_bases_that_6_8_refuses_show_only_the_reason(self) -> None:
        """A reserved name refuses even a neutral plan; invalid geometry refuses a plan that changes capacity (F4)."""
        reserved = PLANNER_POS.replace(b"PlannerRich_00001", b"ServerManMap_G_0123456789ab")
        refused = {"source": None, "adjustment": None, "placements": 0, "kept": None, "capacity": None}
        for path, data in ((POS, POSITIONS.replace(PLANNER_POS, PLANNER_POS + reserved)),
                           (PROTO, PROTOTYPE.replace(b"PlannerRich_00001", b"ServerManMap_G_0123456789ab"))):
            for value in (plan(0, zone("normal")), plan(25, zone("rich"))):
                self.assertEqual(object_capacity(value, "zone", dict(BASELINE, **{path: data})),
                                 [dict(refused, reason="reserved_conflict")])
        broken = dict(BASELINE, **{POS: POSITIONS.replace(b'pos="100 3 100"', b'pos="nan 3 100"')})
        self.assertEqual(object_capacity(plan(0, zone("rich")), "zone", broken),
                         [dict(refused, reason="placement_geometry")])
        neutral = object_capacity(plan(0, zone("normal")), "zone", broken)
        self.assertIn(("Land_City_Hospital", 0), by_source(neutral))

    def test_point_reason_wording(self) -> None:
        """The reason code of a point answer has the Product Owner text."""
        self.assertEqual(CAPACITY_REASON_TEXTS[POINT_TARGET],
                         "Building capacity: inspect a zone to see the buildings it covers.")


class InspectCapacityServiceTests(InspectionFixture):
    """Inspect answers with the mission files or the working baseline as the capacity base."""

    def setUp(self) -> None:
        """Write the mapgroup files into the mission and record the building flag of each read pass."""
        super().setUp()
        for path, data in ((PROTO, PROTOTYPE), (POS, POSITIONS), (DIRT, DIRTS)):
            (self.mission / path).write_bytes(data)
        self.passes: list[tuple[bool, bool]] = []

        def factory(mission, *, retain, clock, buildings=False):
            """Record the retain and buildings flags, then build the real pass."""
            self.passes.append((retain, buildings))
            return MissionReadPass(mission, retain=retain, clock=clock, buildings=buildings)
        patcher = mock.patch.object(mission_map_inspection_run, "MissionReadPass", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def store_baseline(self, proto: bytes) -> AssociationRecordStore:
        """Save a working baseline whose prototype differs from the mission file; Dirt is absent in it."""
        key = self.service.active_association("main")["mission_key"]
        raw = baseline(key=key)
        raw["files"] = [{"target_key": key, "path": path, "existed": data is not None,
                         "sha256": sha(data) if data is not None else None, "size": len(data) if data else None}
                        for path, data in ((PROTO, proto), (POS, POSITIONS), (DIRT, None))]
        store = AssociationRecordStore(self.service._area, "main", key, "baseline")
        store.save(raw, {sha(proto): proto, sha(POSITIONS): POSITIONS}, None)
        return store

    def hospital(self, answer: dict) -> dict:
        """Return the capacity entry of the hospital in a done object answer."""
        self.assertEqual(answer["status"], "done")
        self.assertIsNone(answer["result"]["capacity_reason"])
        return by_source(answer["result"]["capacity"])[("Land_City_Hospital", 50)]

    def test_point_answer_has_null_capacity_and_its_reason(self) -> None:
        """A point selects no building; its pass reads no mapgroup file."""
        self.saved([circle("c1", x=500.0, z=500.0, radius=50.0)])
        answer, _phases = self.finish(self.start({"point": {"x": 500.0, "z": 500.0}}))
        self.assertEqual((answer["result"]["capacity"], answer["result"]["capacity_reason"]), (None, POINT_TARGET))
        self.assertEqual(self.passes, [(True, False)])

    def test_object_without_a_working_baseline_reads_the_mission_files(self) -> None:
        """Capture and proof both read the mapgroup files; the hospital comes from the mission prototype."""
        self.saved([circle("c1", x=500.0, z=500.0, radius=50.0)])
        answer, _phases = self.finish(self.start({"object": "c1"}))
        self.assertEqual(self.hospital(answer)["capacity"]["group"]["baseline"], 4)
        self.assertEqual(self.passes, [(True, True), (False, True)])
        ensure_json_value(answer)

    def test_object_with_a_working_baseline_uses_it(self) -> None:
        """The working baseline is the base, so the pass reads no mapgroup file of the mission."""
        self.store_baseline(PROTOTYPE.replace(b'name="Land_City_Hospital" lootmax="4"',
                                              b'name="Land_City_Hospital" lootmax="10"'))
        self.saved([circle("c1", x=500.0, z=500.0, radius=50.0)])
        answer, _phases = self.finish(self.start({"object": "c1"}))
        self.assertEqual(self.hospital(answer)["capacity"]["group"]["baseline"], 10)
        self.assertEqual(self.passes, [(True, False), (False, False)])

    def test_a_changed_mapgroup_file_ends_stale(self) -> None:
        """Without a working baseline, a mapgroup file is part of the capture and the proof."""
        self.saved([circle("c1", x=500.0, z=500.0, radius=50.0)])
        started = self.start({"object": "c1"})
        (self.mission / POS).write_bytes(POSITIONS + b"\n")
        answer, _phases = self.finish(started)
        self.assertEqual(answer["status"], "stale")

    def test_a_new_working_baseline_ends_stale(self) -> None:
        """The manifest digest of the working baseline joins the recheck before publication."""
        store = self.store_baseline(PROTOTYPE)
        self.saved([circle("c1", x=500.0, z=500.0, radius=50.0)])
        started = self.start({"object": "c1"})
        changed = PROTOTYPE.replace(b'lootmax="4"', b'lootmax="5"', 1)
        raw = baseline(key=store.mission_key)
        raw["files"] = [{"target_key": store.mission_key, "path": PROTO, "existed": True, "sha256": sha(changed),
                         "size": len(changed)}]
        store.save(raw, {sha(changed): changed}, 0)
        answer, _phases = self.finish(started)
        self.assertEqual(answer["status"], "stale")


class BuildingPassTests(unittest.TestCase):
    """The pass reads the three mapgroup files with the same signature and digest rules."""

    def setUp(self) -> None:
        """Write a mission with the tier files, the prototype and the positions; Dirt is absent."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_capacity_pass_")
        self.addCleanup(self.temp.cleanup)
        self.mission = Path(self.temp.name)
        write_mission(self.mission)
        (self.mission / PROTO).write_bytes(PROTOTYPE)
        (self.mission / POS).write_bytes(POSITIONS)

    def test_full_digests_cover_the_building_files(self) -> None:
        """Capture and proof give one full digest per file; the absent Dirt file has none (QA S7)."""
        read = MissionReadPass(self.mission, retain=True, buildings=True)
        read.read_slice(float("inf"))
        expected = tuple(hashlib.sha256((self.mission / name).read_bytes()).hexdigest()
                         for name in ("areaflags.map", "cfglimitsdefinition.xml", PROTO, POS)) + (None,)
        self.assertEqual(read.digests(), expected)
        self.assertEqual(read.content().buildings, {PROTO: PROTOTYPE, POS: POSITIONS, DIRT: None})
        proof = MissionReadPass(self.mission, retain=False, buildings=True)
        proof.read_slice(float("inf"))
        self.assertEqual(proof.digests(), expected)

    def test_an_absent_file_that_appears_is_a_change(self) -> None:
        """Absence is explicit: a Dirt file that appears between two slices ends the pass as changed (QA S6)."""
        clock = StepClock(0.2)
        read = MissionReadPass(self.mission, retain=True, buildings=True, clock=clock, block_size=100)
        read.read_slice(clock.now + 0.5)
        self.assertFalse(read.complete or read.changed)
        (self.mission / DIRT).write_bytes(DIRTS)
        read.read_slice(clock.now + 0.5)
        self.assertTrue(read.changed)


if __name__ == "__main__":
    unittest.main()
