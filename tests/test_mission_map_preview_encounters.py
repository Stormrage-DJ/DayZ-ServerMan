"""Infected caps and notices, unavailable encounters and the bound loot report in the apply preview (8.6)."""

from __future__ import annotations

import hashlib
import unittest

from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.domain.mission_map_loot_report_texts import CAPACITY_EXPLANATION
from dayz_serverman.domain.mission_map_plan import canonical_json
from dayz_serverman.domain.mission_map_preview_changes import excluded_encounters
from dayz_serverman.domain.mission_map_preview_texts import ZOMBIE_MAX_COUNT_ZERO_TEXT, ZONE_SPAWN_DIST_ZERO_TEXT
from tests.mission_map_mapgroup_support import INSIDE_SHED, zone
from tests.mission_map_preview_support import GLOBALS, PreviewFixture
from tests.test_mission_map_infected import encounter

# One included infected encounter whose event the mission offers
INFECTED = encounter("e1", x=600.0, z=600.0, radius=40.0)


class InfectedPreviewTests(PreviewFixture):
    """The `infected` object: caps, null as "not read", and the two notices (Architect 2026-10-14 18:32:23)."""

    def infected(self, objects: list, globals_xml: bytes | None) -> dict:
        """Save the objects, write the globals file and return the infected object of the preview."""
        self.save(objects)
        self.write("db/globals.xml", globals_xml)
        return self.preview.preview("main")["infected"]

    def test_zombie_max_count_zero_with_one_included_encounter(self) -> None:
        """Cap 0 and one written encounter give the first notice; Apply stays allowed."""
        result = self.infected([INFECTED], GLOBALS.replace(b'value="1000"', b'value="0"'))
        self.assertEqual(result["notices"], [{"code": "zombie_max_count_zero", "text": ZOMBIE_MAX_COUNT_ZERO_TEXT}])
        self.assertEqual((result["zombie_max_count"], result["zone_spawn_dist"]), (0, 300))
        self.assertEqual(result["events"], {"InfectedVillage": {"nominal": 50, "max": 100}})
        self.assertFalse(self.preview.preview("main")["refused"])

    def test_zone_spawn_dist_zero_and_both_notices(self) -> None:
        """ZoneSpawnDist 0 gives the second notice; both values 0 give both, in order."""
        zone_only = self.infected([INFECTED], GLOBALS.replace(b'value="300"', b'value="0"'))
        self.assertEqual(zone_only["notices"], [{"code": "zone_spawn_dist_zero", "text": ZONE_SPAWN_DIST_ZERO_TEXT}])
        both = GLOBALS.replace(b'value="300"', b'value="0"').replace(b'value="1000"', b'value="0"')
        self.write("db/globals.xml", both)
        notices = self.preview.preview("main")["infected"]["notices"]
        self.assertEqual([item["code"] for item in notices], ["zombie_max_count_zero", "zone_spawn_dist_zero"])

    def test_no_notice_without_a_written_encounter_or_a_zero_value(self) -> None:
        """All infected encounters excluded, or both values above 0, give no notice."""
        zeros = GLOBALS.replace(b'value="300"', b'value="0"').replace(b'value="1000"', b'value="0"')
        excluded = self.infected([dict(INFECTED, excluded=True)], zeros)
        self.assertEqual((excluded["notices"], excluded["zombie_max_count"]), ([], 0))
        self.assertEqual(self.preview.preview("main")["encounters"]["written"], [])
        self.write("db/globals.xml", GLOBALS)
        self.save([INFECTED], expected=0)
        self.assertEqual(self.preview.preview("main")["infected"]["notices"], [])

    def test_unreadable_values_are_null_without_a_notice(self) -> None:
        """An absent file, a duplicated, negative or non-integer value is null, shown as "not read"."""
        absent = self.infected([INFECTED], None)
        self.assertEqual((absent["zombie_max_count"], absent["zone_spawn_dist"], absent["notices"]), (None, None, []))
        for document in (GLOBALS.replace(b'value="300"', b'value="-1"'),
                         GLOBALS.replace(b'value="300"', b'value="3.5"'),
                         GLOBALS.replace(b"</variables>", b'<var name="ZoneSpawnDist" type="0" value="0"/></variables>')):
            with self.subTest(document=document):
                self.write("db/globals.xml", document)
                self.assertIsNone(self.preview.preview("main")["infected"]["zone_spawn_dist"])


class EncounterChoicePreviewTests(PreviewFixture):
    """Unavailable encounters are listed as R11 choices and are never written."""

    def test_event_not_offered_and_bandits(self) -> None:
        """An infected encounter with an event that the mission does not offer, and an AI_Bandits encounter."""
        linked = dict(encounter("u", "Unzoned", x=500.0, z=500.0, radius=50.0),
                      linked_loot={"enabled": True, "tier": None, "abundance": "rich", "radius": None})
        self.save([INFECTED, linked, encounter("b", provider="ai_bandits", x=700.0, z=700.0)])
        answer = self.preview.preview("main")
        unavailable = {item["id"]: item for item in answer["encounters"]["unavailable"]}
        self.assertEqual(sorted(unavailable), ["b", "u"])
        self.assertEqual((unavailable["u"]["reason"], unavailable["u"]["linked_loot"]), ("event_not_offered", True))
        self.assertEqual(answer["choices"]["unavailable_encounters"], ["u", "b"])
        self.assertEqual(answer["encounters"]["written"], ["e1"])
        self.assertFalse(answer["refused"])
        # Its linked loot still applies (R11)
        self.assertEqual([item["id"] for item in answer["loot"]["zones"]], ["u"])
        self.save([INFECTED, dict(linked, excluded=True)], expected=0)
        self.assertEqual(self.preview.preview("main")["choices"]["unavailable_encounters"], [])

    def test_unregistered_territory_writes_no_encounter(self) -> None:
        """Without the registration in cfgenvironment.xml no infected encounter is written."""
        self.save([INFECTED])
        self.write("cfgenvironment.xml", b"<env><territories/></env>")
        answer = self.preview.preview("main")
        self.assertEqual(answer["encounters"]["unavailable"][0]["reason"], "territory_not_registered")
        self.assertEqual((answer["encounters"]["written"], answer["affected_files"]), ([], []))


class ExcludedEncounterTests(PreviewFixture):
    """`encounters.excluded`: every encounter that the plan excludes (Architect 2026-10-15 18:04:57, ruling A-1)."""

    def objects(self) -> list[dict]:
        """Return, in plan order: an unavailable plan-excluded encounter, an included one, an available plan-excluded
        one, an unavailable included one and a loot zone."""
        return [encounter("b", provider="ai_bandits", x=700.0, z=700.0, excluded=True), INFECTED,
                encounter("x", x=800.0, z=800.0, radius=40.0, excluded=True),
                encounter("u", "Unzoned", x=500.0, z=500.0, radius=50.0), zone("rich")]

    def test_the_rule_lists_plan_exclusions_only_in_plan_order_with_the_exact_keys(self) -> None:
        """Available or not, in plan order; no included encounter and no loot object, also one marked excluded."""
        loot = dict(zone("rich"), id="loot", excluded=True)
        self.assertEqual(excluded_encounters([*self.objects(), loot]), [
            {"id": "b", "name": "Encounter b", "kind": "encounter", "provider": "ai_bandits"},
            {"id": "x", "name": "Encounter x", "kind": "encounter", "provider": "native_infected"}])
        self.assertEqual(excluded_encounters([INFECTED, zone("rich")]), [])

    def test_the_answer_lists_both_and_keeps_the_unavailable_list(self) -> None:
        """The unavailable plan-excluded encounter is in both lists and needs no choice."""
        self.save(self.objects())
        encounters = self.preview.preview("main")["encounters"]
        self.assertEqual([item["id"] for item in encounters["excluded"]], ["b", "x"])
        self.assertEqual([set(item) for item in encounters["excluded"]], [{"id", "name", "kind", "provider"}] * 2)
        unavailable = {item["id"]: item for item in encounters["unavailable"]}
        self.assertEqual(sorted(unavailable), ["b", "u"])
        self.assertEqual((unavailable["b"]["excluded"], unavailable["b"]["choice_required"]), (True, False))
        self.assertEqual((unavailable["u"]["excluded"], unavailable["u"]["choice_required"]), (False, True))
        self.assertEqual(encounters["written"], ["e1"])

    def test_the_fingerprint_binds_the_list(self) -> None:
        """The fingerprint is the digest of the answer with the list; a review without it is stale."""
        self.save(self.objects())
        review = self.review()
        bound = {name: value for name, value in review.answer.items() if name != "fingerprint"}

        def digest(answer: dict) -> str:
            """Return the preview fingerprint of an answer: version, DayZ root and answer, as canonical JSON."""
            return hashlib.sha256(canonical_json({"version": 1, "dayz_root": str(review.context.root),
                                                  "answer": answer})).hexdigest()

        self.assertEqual(review.fingerprint, digest(bound))
        hidden = dict(bound, encounters=dict(bound["encounters"], excluded=[]))
        self.assertNotEqual(digest(hidden), review.fingerprint)
        with self.assertRaises(PreviewStale):
            self.preview.revalidate("main", digest(hidden))
        self.assertEqual(self.preview.revalidate("main", review.fingerprint).fingerprint, review.fingerprint)


class LootReportPreviewTests(PreviewFixture):
    """The bound loot report: capacity exceptions, the live-effect limitation and cluster placements."""

    def test_report_with_exceptions_and_cluster_placements(self) -> None:
        """Planner content and a differing cluster placement are kept and listed; the R08 explanation is shown."""
        self.write("mapgroupcluster01.xml", b'<?xml version="1.0"?>\r\n<map>\r\n' + INSIDE_SHED + b"</map>\r\n")
        self.save([zone("rich")], 25)
        report = self.preview.preview("main")["loot"]
        self.assertEqual(report["explanation"], CAPACITY_EXPLANATION)
        self.assertEqual(sorted({item["reason"] for item in report["kept"]}), ["cluster_placement", "planner_content"])
        self.assertTrue(report["capacity"])
        self.assertIsNone(report["native_limit"])

    def test_missing_tier_inputs_refuse_and_item_types_feed_missing_supply(self) -> None:
        """A tier zone without areaflags.map or value flags refuses; registered types decide missing supply."""
        self.save([dict(zone("rich"), tier="Tier2")])
        self.write("cfglimitsdefinition.xml", None)
        self.assertIn("limits_missing", [item["reason"] for item in self.preview.preview("main")["refusals"]])
        self.write("areaflags.map", None)
        self.assertIn("areaflags_missing", [item["reason"] for item in self.preview.preview("main")["refusals"]])
        self.save([zone("rich")], 25, expected=0)
        self.write("db/types.xml", b'<types><type name="A"><nominal>1</nominal><category name="food"/></type></types>')
        known = self.preview.preview("main")
        self.assertIsNotNone(known["loot"]["mechanisms"]["missing_supply"]["categories"])
        # An economy file that cannot be read makes the item types unknown, so missing supply is unknown
        self.write("cfgeconomycore.xml", b"<economycore>")
        unknown = self.preview.preview("main")
        self.assertIsNone(unknown["loot"]["mechanisms"]["missing_supply"]["categories"])
        self.assertNotIn("db/types.xml", unknown["inputs"]["files"])

    def test_refused_territory_is_a_refusal_of_the_preview(self) -> None:
        """A territory file that the builder cannot change refuses with its reason and keeps the caps."""
        self.save([INFECTED])
        self.write("env/zombie_territories.xml", b"<territory-type>")
        answer = self.preview.preview("main")
        refusal = next(item for item in answer["refusals"] if item["file"] == "env/zombie_territories.xml")
        self.assertEqual(refusal["reason"], "territory_file_invalid")
        self.assertEqual(answer["infected"]["zombie_max_count"], 1000)
        self.assertEqual(answer["infected"]["notices"], [])


if __name__ == "__main__":
    unittest.main()
