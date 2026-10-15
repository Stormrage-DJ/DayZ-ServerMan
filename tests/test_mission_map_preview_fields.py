"""Answer fields and edge cases of the apply preview that QA-8.6-F1 and QA-8.6-F4 named (leaf 8.6 rework)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import unittest

from dayz_serverman.application.mission_configuration import MissionPathError, _contained
from dayz_serverman.repositories.medical_zone_fragments import patch_zones
from dayz_serverman.repositories.mission_map_infected import MARKER_BEGIN, infected_caps
from dayz_serverman.repositories.mission_map_layout import TargetClass
from dayz_serverman.repositories.mission_map_owned import owned_digest
from dayz_serverman.repositories.mission_map_providers import read_encounter_providers
from tests.mission_map_mapgroup_support import PROTOTYPE, zone
from tests.mission_map_preview_support import PreviewFixture
from tests.test_mission_map_infected import BASELINE as TERRITORIES, encounter

INFECTED = encounter("e1", x=600.0, z=600.0, radius=40.0)


class AnswerFieldTests(PreviewFixture):
    """Fields of the answer that the window and Apply read."""

    def test_affected_file_digests_and_existence(self) -> None:
        """Each affected file names its owned digest after; a candidate that keeps a file absent says so."""
        self.save([zone("rich"), INFECTED], 25)
        review = self.review()
        contents = review.candidates.contents()
        for item in review.answer["affected_files"]:
            with self.subTest(path=item["path"]):
                after = contents[item["path"]]
                self.assertEqual(item["owned_sha256_after"], owned_digest(TargetClass.MISSION, item["path"], after))
                self.assertIsNotNone(item["owned_sha256_after"])
                self.assertEqual((item["exists_before"], item["exists_after"]), (True, True))
        # The territory file was absent at Apply; an admin file that appears later is removed by the candidate
        self.save([zone("rich")], 25, expected=0)
        self.write("env/zombie_territories.xml", None)
        self.apply(self.review())
        self.write("env/zombie_territories.xml", TERRITORIES)
        affected = {item["path"]: item for item in self.review().answer["affected_files"]}
        territory = affected["env/zombie_territories.xml"]
        self.assertEqual((territory["exists_before"], territory["exists_after"]), (True, False))
        self.assertEqual((territory["sha256_after"], territory["owned_sha256_after"]), (None, None))

    def test_detected_bounds_and_encounter_providers(self) -> None:
        """The answer shows the header bounds next to the plan bounds, and the provider snapshot of the load."""
        self.save([INFECTED])
        answer = self.preview.preview("main")
        self.assertEqual(answer["bounds"]["detected"], {"x_min": 0, "z_min": 0, "width": 1000, "height": 1000})
        expected = read_encounter_providers(self.root.resolve(), self.mission.resolve(),
                                            self.profiles.records["main"], containment=_contained)
        self.assertEqual(answer["capabilities"]["encounter_providers"], expected)
        self.assertEqual(answer["capabilities"]["encounter_providers"]["native_infected"]["event_names"],
                         ["InfectedArmy", "InfectedVillage"])

    def test_economy_file_and_medical_baseline_content_are_bound(self) -> None:
        """cfgeconomycore.xml has its digest in the freshness set; the medical baseline digest follows its content."""
        self.save([zone("rich")], 25)
        self.write("cfgeconomycore.xml", b"<economycore></economycore>")
        first = self.review()
        self.assertEqual(first.answer["inputs"]["files"]["cfgeconomycore.xml"],
                         hashlib.sha256(b"<economycore></economycore>").hexdigest())
        # The same registration with other bytes stales the review
        self.write("cfgeconomycore.xml", b"<economycore> </economycore>")
        self.assertNotEqual(self.review().fingerprint, first.fingerprint)
        self.set_medical_baseline()
        on = self.review()
        self.assertEqual(on.answer["inputs"]["medical_baseline"], hashlib.sha256(PROTOTYPE).hexdigest())
        # A baseline with other bytes and the same medical meaning still stales the review
        self.set_medical_baseline(PROTOTYPE.replace(b"keep this comment", b"other comment"))
        changed = self.review()
        self.assertNotEqual(changed.answer["inputs"]["medical_baseline"], on.answer["inputs"]["medical_baseline"])
        self.assertNotEqual(changed.fingerprint, on.fingerprint)

    def test_capture_files_state_existence(self) -> None:
        """The capture object names each managed file with its real existence and digest."""
        self.save([zone("rich")])
        files = {item["path"]: item for item in self.preview.preview("main")["capture"]["files"]}
        self.assertEqual(files["env/bear_territories.xml"], {"path": "env/bear_territories.xml", "existed": False,
                                                             "sha256": None})
        self.assertTrue(files["env/wolf_territories.xml"]["existed"])

    def test_nested_globals_var_is_not_read(self) -> None:
        """Only a var directly under the root variables counts; a nested one gives null and no notice."""
        nested = b'<variables><group><var name="ZoneSpawnDist" value="0"/></group></variables>'
        self.assertIsNone(infected_caps(None, nested, [])["zone_spawn_dist"])
        self.save([INFECTED])
        self.write("db/globals.xml", nested.replace(b"ZoneSpawnDist", b"ZombieMaxCount"))
        infected = self.preview.preview("main")["infected"]
        self.assertEqual((infected["zombie_max_count"], infected["notices"]), (None, []))


class ChangeEdgeTests(PreviewFixture):
    """External and coordinated changes at the edges of the reference rules."""

    def test_removed_prototype_with_a_medical_baseline(self) -> None:
        """A removed mapgroupproto.xml is an external change of owned values, also with a medical baseline."""
        self.set_medical_baseline()
        self.save([zone("rich")], 25)
        self.apply(self.review())
        self.write("mapgroupproto.xml", None)
        answer = self.preview.preview("main")
        change = next(item for item in answer["external_changes"] if item["path"] == "mapgroupproto.xml")
        self.assertEqual((change["exists_now"], change["owned_changed"]), (False, True))
        self.assertEqual(answer["coordinated_changes"], [])

    def test_damaged_block_on_a_file_absent_at_apply(self) -> None:
        """A territory file absent at Apply that returns with a damaged marker block changes owned values."""
        self.save([zone("rich")], 25)
        self.write("env/zombie_territories.xml", None)
        self.apply(self.review())
        self.assertEqual(self.review().answer["external_changes"], [])
        self.write("env/zombie_territories.xml", TERRITORIES + MARKER_BEGIN)
        changes = {item["path"]: item for item in self.review().answer["external_changes"]}
        self.assertEqual((changes["env/zombie_territories.xml"]["existed_before"],
                          changes["env/zombie_territories.xml"]["owned_changed"]), (False, True))

    def test_coordinated_change_names_its_reference(self) -> None:
        """A second profile on a captured target compares with the original; a medical toggle names that source."""
        self.save([INFECTED])
        self.apply(self.review())
        self.set_medical_baseline()
        (self.baselines / "second").mkdir()
        (self.baselines / "second" / "medical_loot_zones.original.xml").write_bytes(PROTOTYPE)
        self.write("mapgroupproto.xml", patch_zones(PROTOTYPE, PROTOTYPE, True))
        self.save([zone("rich")], identifier="second")
        answer = self.review("second").answer
        self.assertEqual(answer["base"], "original")
        self.assertEqual(answer["coordinated_changes"], [{"path": "mapgroupproto.xml",
                                                          "feature": "medical_loot_zones", "reference": "original"}])


class MissionReadTests(PreviewFixture):
    """Reads stay inside the mission folder; a folder with a cluster-like name is no input (QA-8.6-F4)."""

    def test_folder_with_a_cluster_name_is_skipped(self) -> None:
        """The preview answers; the folder is not in the freshness set and no cluster placement comes from it."""
        self.save([zone("rich")], 25)
        before = self.review().fingerprint
        (self.mission / "mapgroupcluster_dir.xml").mkdir()
        review = self.review()
        self.assertNotIn("mapgroupcluster_dir.xml", review.answer["inputs"]["files"])
        self.assertEqual(review.fingerprint, before)

    def test_junction_below_the_mission_is_refused(self) -> None:
        """A db folder that is a junction to a folder outside the mission refuses the preview; nothing is read."""
        self.save([zone("rich"), INFECTED], 25)
        outside = self.root.parent / "outside_db"
        shutil.move(str(self.mission / "db"), str(outside))
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.mission / "db"), str(outside)],
                              capture_output=True, text=True)
        if made.returncode != 0:
            self.skipTest("the junction could not be created: " + made.stdout + made.stderr)
        try:
            self.assertRaises(MissionPathError, self.preview.preview, "main")
        finally:
            subprocess.run(["cmd", "/c", "rmdir", str(self.mission / "db")], capture_output=True)


if __name__ == "__main__":
    unittest.main()
