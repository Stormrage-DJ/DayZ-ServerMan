"""Pure rules of the apply preview (8.6): object changes, encounter choices, infected notices and owned digests."""

from __future__ import annotations

import copy
import hashlib
import unittest

from dayz_serverman.domain.mission_map_preview_changes import (
    applied_global, applied_group_names, applied_objects, object_changes, unavailable_encounters,
)
from dayz_serverman.domain.mission_map_preview_texts import (
    CAPTURE_RISK_TEXT, ZOMBIE_MAX_COUNT_ZERO_TEXT, ZONE_SPAWN_DIST_ZERO_TEXT, infected_section,
)
from dayz_serverman.repositories.mission_map_infected import MARKER_BEGIN, MARKER_END, TerritoryRefused, infected_caps
from dayz_serverman.repositories.mission_map_layout import FILE_SETS, MISSION_FILE_SET, TargetClass
from dayz_serverman.repositories.mission_map_mapgroup_read import MapgroupRefused
from dayz_serverman.repositories.mission_map_owned import (
    WRITABLE_MISSION_FILES, captured_only, check_reserved, owned_digest,
)
from tests.mission_map_mapgroup_support import PROTOTYPE
from tests.test_mission_map_infected import BASELINE as TERRITORIES, encounter
from tests.test_mission_map_plan import circle

# The Product Owner texts as evidence 7.6 gives them (14:29:00 and 18:45:00), copied word for word
PO_ZOMBIE = ("ZombieMaxCount is 0 in db/globals.xml of this mission. With this value DayZ spawns no infected, "
             "also not in the infected encounters of this plan. DayZ-ServerMan does not change this value.")
PO_ZONE = ("ZoneSpawnDist is 0 in db/globals.xml of this mission. In testing with this value, DayZ spawned "
           "infected in the mission's own infected zones where the player stood. It spawned no infected in infected "
           "zones whose edge was 100 m or more from the player. This included infected zones made by the editor. "
           "DayZ-ServerMan does not change this value.")
CAPS = {"events": {"InfectedVillage": {"nominal": 50, "max": 100}}, "zombie_max_count": 0, "zone_spawn_dist": 0}


def globals_with(*pairs: tuple[str, str]) -> bytes:
    """Return a globals document with the named values."""
    rows = "".join(f'<var name="{name}" type="0" value="{value}"/>\n' for name, value in pairs)
    return f"<variables>\n{rows}</variables>\n".encode()


class ObjectChangeTests(unittest.TestCase):
    """Added, changed and removed objects since the last Apply, by object ID (6.9 ruling)."""

    def test_record_without_objects_lists_every_object_as_added(self) -> None:
        """A schema-1 record, or no record, has no previous plan."""
        objects = [circle("a"), encounter("e")]
        for record in (None, {"operation_kind": "restore_previous"}):
            result = object_changes(objects, applied_objects(record))
            self.assertEqual((result["previous"], [item["id"] for item in result["added"]]), (False, ["a", "e"]))
            self.assertEqual((result["changed"], result["removed"]), ([], []))
        self.assertIsNone(applied_global({"global_abundance": True}))
        self.assertEqual(applied_group_names({"generated_groups": "x"}), [])

    def test_added_changed_and_removed_by_object_id(self) -> None:
        """A new ID is added, a missing ID removed, a different configuration changed; presentation is no change."""
        kept, moved, renamed, gone = circle("k"), circle("m"), circle("r"), circle("g")
        record = {"plan_objects": [kept, moved, renamed, gone], "global_abundance": 25,
                  "generated_groups": [{"name": "ServerManMap_G_b"}, {"name": "ServerManMap_G_a"}, {"bad": 1}]}
        now_moved = dict(copy.deepcopy(moved), centre={"x": 101.0, "z": 100.0})
        now_renamed = dict(renamed, name="Other", visible=False, locked=True)
        # -0.0 and 0, and 2.0 and 2, are one number in the configuration form
        now_kept = dict(kept, radius=50)
        result = object_changes([now_kept, now_moved, now_renamed, circle("n")], applied_objects(record))
        self.assertTrue(result["previous"])
        self.assertEqual([item["id"] for item in result["added"]], ["n"])
        self.assertEqual([item["id"] for item in result["changed"]], ["m"])
        self.assertEqual([item["id"] for item in result["removed"]], ["g"])
        self.assertEqual(result["removed"][0], {"id": "g", "name": "Loot circle 1", "kind": "loot_circle",
                                                "provider": None})
        self.assertEqual(applied_global(record), 25)
        self.assertEqual(applied_group_names(record), ["ServerManMap_G_a", "ServerManMap_G_b"])
        self.assertEqual(object_changes([], applied_objects({"plan_objects": []})),
                         {"previous": True, "added": [], "changed": [], "removed": []})


class EncounterChoiceTests(unittest.TestCase):
    """Unavailable encounters become R11 choices; available ones are not listed."""

    def test_unavailable_reasons_and_choice_state(self) -> None:
        """Provider reason, event not offered, a missing provider entry and the plan's exclusion."""
        providers = {"native_infected": {"available": True, "reason": None, "event_names": ["InfectedVillage"]},
                     "ai_bandits": {"available": False, "reason": "Bandit text.", "event_names": []}}
        linked = dict(encounter("l", "Unzoned"), linked_loot={"enabled": True, "tier": None, "abundance": "rich",
                                                               "radius": None})
        objects = [encounter("ok"), linked, dict(encounter("x", "Unzoned"), excluded=True),
                   encounter("b", provider="ai_bandits"), encounter("w", provider="native_wolf"), circle("c")]
        result = {item["id"]: item for item in unavailable_encounters(objects, providers)}
        self.assertEqual(sorted(result), ["b", "l", "w", "x"])
        self.assertEqual((result["l"]["reason"], result["l"]["choice_required"], result["l"]["linked_loot"]),
                         ("event_not_offered", True, True))
        self.assertEqual((result["x"]["excluded"], result["x"]["choice_required"]), (True, False))
        self.assertEqual(result["b"]["reason"], "Bandit text.")
        self.assertEqual(result["w"]["reason"], "provider_unavailable")
        closed = {"native_infected": {"available": False, "reason": "territory_not_registered", "event_names": []}}
        self.assertEqual(unavailable_encounters([encounter("ok")], closed)[0]["reason"], "territory_not_registered")


class InfectedNoticeTests(unittest.TestCase):
    """The notice rule and the exact Product Owner texts (Architect 18:32:23, Product Owner 14:29:00, 18:45:00)."""

    def test_texts_equal_the_product_owner_texts(self) -> None:
        """Both texts are plain text, word for word."""
        self.assertEqual(ZOMBIE_MAX_COUNT_ZERO_TEXT, PO_ZOMBIE)
        self.assertEqual(ZONE_SPAWN_DIST_ZERO_TEXT, PO_ZONE)
        self.assertNotIn("`", ZOMBIE_MAX_COUNT_ZERO_TEXT + ZONE_SPAWN_DIST_ZERO_TEXT + CAPTURE_RISK_TEXT)

    def test_notices_need_a_written_encounter_and_a_zero_value(self) -> None:
        """Both notices in order when both are 0; none when nothing is written, for null or a value above 0."""
        both = infected_section(CAPS, True)
        self.assertEqual([item["code"] for item in both["notices"]], ["zombie_max_count_zero", "zone_spawn_dist_zero"])
        self.assertEqual([item["text"] for item in both["notices"]], [PO_ZOMBIE, PO_ZONE])
        self.assertEqual((both["events"], both["zombie_max_count"], both["zone_spawn_dist"]),
                         (CAPS["events"], 0, 0))
        self.assertEqual(infected_section(CAPS, False)["notices"], [])
        only_zone = infected_section(dict(CAPS, zombie_max_count=1000), True)
        self.assertEqual([item["code"] for item in only_zone["notices"]], ["zone_spawn_dist_zero"])
        only_zombie = infected_section(dict(CAPS, zone_spawn_dist=None), True)
        self.assertEqual([item["code"] for item in only_zombie["notices"]], ["zombie_max_count_zero"])
        self.assertEqual(infected_section(dict(CAPS, zombie_max_count=None, zone_spawn_dist=1), True)["notices"], [])

    def test_zone_spawn_dist_follows_the_zombie_max_count_rule(self) -> None:
        """One plain non-negative integer under the root variables; anything else is null ("not read")."""
        self.assertEqual(infected_caps(None, globals_with(("ZoneSpawnDist", "0")), [])["zone_spawn_dist"], 0)
        self.assertEqual(infected_caps(None, globals_with(("ZoneSpawnDist", "300")), [])["zone_spawn_dist"], 300)
        for document in (globals_with(("ZoneSpawnDist", "1"), ("ZoneSpawnDist", "1")),
                         globals_with(("ZoneSpawnDist", "-1")), globals_with(("ZoneSpawnDist", "2.5")),
                         globals_with(("ZoneSpawnDist", "x")), globals_with(("Other", "1")), None, b"<variables>",
                         b'<other><var name="ZoneSpawnDist" value="5"/></other>'):
            with self.subTest(document=document):
                caps = infected_caps(None, document, [])
                self.assertIsNone(caps["zone_spawn_dist"])
        both = infected_caps(None, globals_with(("ZombieMaxCount", "7"), ("ZoneSpawnDist", "9")), [])
        self.assertEqual((both["zombie_max_count"], both["zone_spawn_dist"]), (7, 9))


class OwnedDispatcherTests(unittest.TestCase):
    """The owned-digest and D8 callables of the commit seam, and captured-only files (7.9, QA75-F1)."""

    def test_captured_only_files_have_no_projection(self) -> None:
        """Wolf, bear and the runtime provider file are captured-only; the five writable files are not."""
        self.assertEqual([path for path in MISSION_FILE_SET if captured_only(TargetClass.MISSION, path)],
                         ["env/wolf_territories.xml", "env/bear_territories.xml"])
        self.assertEqual(WRITABLE_MISSION_FILES, tuple(path for path in MISSION_FILE_SET
                                                       if not captured_only(TargetClass.MISSION, path)))
        self.assertTrue(captured_only(TargetClass.RUNTIME, FILE_SETS[TargetClass.RUNTIME][0]))
        self.assertFalse(captured_only(TargetClass.MISSION, "db/globals.xml"))
        for path in ("env/wolf_territories.xml", "env/bear_territories.xml"):
            self.assertIsNone(owned_digest(TargetClass.MISSION, path, b"<x/>"))
            check_reserved(TargetClass.MISSION, path, b"ServerManMap_G_1 dayz-serverman mission-map")

    def test_owned_digests_of_writable_files(self) -> None:
        """areaflags.map is the whole file; an absent file has a null digest, also for the territory file."""
        self.assertEqual(owned_digest(TargetClass.MISSION, "areaflags.map", b"abc"), hashlib.sha256(b"abc").hexdigest())
        for path in WRITABLE_MISSION_FILES:
            self.assertIsNone(owned_digest(TargetClass.MISSION, path, None))
        self.assertEqual(owned_digest(TargetClass.MISSION, "env/zombie_territories.xml", TERRITORIES),
                         hashlib.sha256(b"").hexdigest())
        self.assertIsNotNone(owned_digest(TargetClass.MISSION, "mapgroupproto.xml", PROTOTYPE))
        damaged = TERRITORIES + MARKER_END + MARKER_BEGIN
        with self.assertRaises(TerritoryRefused):
            owned_digest(TargetClass.MISSION, "env/zombie_territories.xml", damaged)

    def test_reserved_checks_of_writable_files(self) -> None:
        """Reserved group names and marker text refuse; areaflags.map holds no names."""
        check_reserved(TargetClass.MISSION, "areaflags.map", b"ServerManMap_G_1")
        reserved = PROTOTYPE.replace(b"Land_House", b"ServerManMap_G_1")
        with self.assertRaises(MapgroupRefused):
            check_reserved(TargetClass.MISSION, "mapgroupproto.xml", reserved)
        with self.assertRaises(TerritoryRefused):
            check_reserved(TargetClass.MISSION, "env/zombie_territories.xml", TERRITORIES + MARKER_BEGIN)
        check_reserved(TargetClass.MISSION, "mapgroupproto.xml", PROTOTYPE)


if __name__ == "__main__":
    unittest.main()
