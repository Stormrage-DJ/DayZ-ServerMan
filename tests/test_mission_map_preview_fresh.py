"""Fingerprint binding and staleness of the apply preview (8.6): every relevant change, and only those."""

from __future__ import annotations

import unittest
from typing import Callable

from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.domain.mission_map_preview_texts import PREVIEW_STALE_TEXT
from dayz_serverman.domain.mission_map_records import sealed
from dayz_serverman.domain.profiles import ProfileRecord
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import original_folder
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.mission_map_mapgroup_support import INSIDE_SHED, PROTOTYPE, zone
from tests.mission_map_preview_support import GLOBALS, PreviewFixture
from tests.test_mission_map_infected import EVENTS, encounter
from tests.test_mission_map_terrain_files import limits_text

# A cluster file with one placement of a placed group inside the zone, and its changed form
CLUSTER = b'<?xml version="1.0"?>\r\n<map>\r\n' + INSIDE_SHED + b"</map>\r\n"
CLUSTER_MOVED = CLUSTER.replace(b"510.25", b"511.25")
# A types document that supplies the categories of the test groups, and the economy file that registers another
TYPES = b'<types><type name="Apple"><nominal>5</nominal><category name="food"/></type></types>'
ECONOMY = b'<economycore><ce folder="extra"><file name="more_types.xml" type="types"/></ce></economycore>'


class PreviewFreshnessTests(PreviewFixture):
    """A review again under Apply compares the fingerprint; there is no preview cache."""

    def setUp(self) -> None:
        """Save a plan with a capacity zone and one included infected encounter, and review it."""
        super().setUp()
        self.save([zone("rich"), encounter("e1", x=600.0, z=600.0, radius=40.0)], 25)
        self.reviewed = self.review().fingerprint

    def stale_after(self, change: Callable[[], None]) -> None:
        """Assert that the change gives another fingerprint and that revalidation refuses it as stale."""
        self.assertEqual(self.review().fingerprint, self.reviewed)
        change()
        with self.assertRaises(PreviewStale) as caught:
            self.preview.revalidate("main", self.reviewed)
        # The Product Owner text of 2026-10-15 01:16:10 in evidence 8.7
        self.assertEqual(caught.exception.args[0], PREVIEW_STALE_TEXT)
        self.reviewed = self.review().fingerprint

    def fresh_after(self, change: Callable[[], None]) -> None:
        """Assert that the change keeps the fingerprint and that revalidation returns the review."""
        change()
        self.assertEqual(self.preview.revalidate("main", self.reviewed).fingerprint, self.reviewed)

    def test_plan_profile_and_settings_revisions(self) -> None:
        """An equal plan saved again, a new profile revision and a new settings revision each stale the review."""
        plan = self.plans.load("main")["plan"]
        self.stale_after(lambda: self.plans.save("main", plan, 0))
        values = self.profiles.records["main"].values
        self.stale_after(lambda: self.profiles.records.update(main=ProfileRecord(2, values)))
        self.stale_after(lambda: setattr(self.settings, "revision", 2))

    def test_managed_and_unowned_input_files(self) -> None:
        """Each managed writable file and each unowned input of a candidate is in the freshness set."""
        for relative, data in (("areaflags.map", (self.mission / "areaflags.map").read_bytes()[:-1] + b"\x07"),
                               ("mapgroupproto.xml", PROTOTYPE.replace(b"keep this comment", b"keep it")),
                               ("mapgrouppos.xml", None), ("mapgroupdirt.xml", b"<map></map>"),
                               ("env/zombie_territories.xml", (self.mission / "env/zombie_territories.xml")
                                .read_bytes() + b"\r\n"),
                               ("db/events.xml", EVENTS + b"\n"),
                               ("cfglimitsdefinition.xml", limits_text("Tier1", "Tier2", "Tier3", "Tier5").encode()),
                               ("db/types.xml", TYPES)):
            with self.subTest(relative=relative):
                self.stale_after(lambda: self.write(relative, data))

    def test_cluster_files_are_inputs(self) -> None:
        """A new cluster file, a changed one and a removed one each stale the review (6.8 F3)."""
        self.stale_after(lambda: self.write("mapgroupcluster01.xml", CLUSTER))
        self.assertIn("mapgroupcluster01.xml", self.review().answer["inputs"]["files"])
        self.stale_after(lambda: self.write("mapgroupcluster01.xml", CLUSTER_MOVED))
        self.stale_after(lambda: self.write("MapGroupCluster02.XML", CLUSTER))
        self.stale_after(lambda: self.write("mapgroupcluster01.xml", None))
        # A file that only resembles the name pattern is no input
        self.fresh_after(lambda: self.write("mapgroupcluster.txt", CLUSTER))

    def test_economy_registration_and_its_types(self) -> None:
        """cfgeconomycore.xml and every types file that it registers are inputs."""
        self.stale_after(lambda: self.write("db/types.xml", TYPES))
        (self.mission / "extra").mkdir()
        self.stale_after(lambda: self.write("cfgeconomycore.xml", ECONOMY))
        self.assertIn("extra/more_types.xml", self.review().answer["inputs"]["files"])
        self.stale_after(lambda: self.write("extra/more_types.xml", TYPES))

    def test_globals_values_bind_but_the_file_does_not(self) -> None:
        """ZombieMaxCount and ZoneSpawnDist bind, also to and from null; another globals value does not."""
        self.fresh_after(lambda: self.write("db/globals.xml", GLOBALS.replace(
            b'name="AnimalMaxCount" type="0" value="5"', b'name="AnimalMaxCount" type="0" value="6"')))
        self.fresh_after(lambda: self.write("db/globals.xml", GLOBALS + b"\n"))
        self.stale_after(lambda: self.write("db/globals.xml", GLOBALS.replace(b'value="1000"', b'value="0"')))
        self.stale_after(lambda: self.write("db/globals.xml", GLOBALS.replace(b'value="300"', b'value="0"')))
        self.stale_after(lambda: self.write("db/globals.xml", GLOBALS.replace(b"ZoneSpawnDist", b"Other")))
        self.stale_after(lambda: self.write("db/globals.xml", None))
        self.assertNotIn("db/globals.xml", self.review().answer["inputs"]["files"])

    def test_medical_baseline_records_and_other_profiles(self) -> None:
        """The medical baseline, the editor records and the profiles of the target are bound."""
        self.stale_after(self.set_medical_baseline)
        self.stale_after(lambda: self.apply(self.review()))
        self.stale_after(lambda: self.profiles.records.pop("second"))
        # Another protected original manifest stales the review, also when no candidate byte changes
        self.stale_after(self.restamp_original)

    def restamp_original(self) -> None:
        """Rewrite the protected original with another capture time, as only a damaged store could."""
        store = VersionedJsonRepository(original_folder(self.area, self.key()) / "original.json", 1)
        record = OriginalStore(self.area, self.key()).read()
        store.save(sealed(dict(record, captured_at="2026-10-14T20:00:00Z")), 0)

    def test_captured_only_files_bind_only_through_the_first_capture(self) -> None:
        """Before the first Apply the wolf file is captured; after it, an admin edit changes nothing (7.9)."""
        self.assertIn("env/wolf_territories.xml", self.review().answer["inputs"]["files"])
        self.stale_after(lambda: self.write("env/wolf_territories.xml", b"<territory-type/>"))
        self.stale_after(lambda: self.write("env/bear_territories.xml", b"<territory-type/>"))
        self.apply(self.review())
        self.reviewed = self.review().fingerprint
        self.fresh_after(lambda: self.write("env/wolf_territories.xml", b"<territory-type><x/></territory-type>"))
        self.fresh_after(lambda: self.write("env/bear_territories.xml", None))
        answer = self.review().answer
        self.assertEqual((answer["affected_files"], answer["external_changes"]), ([], []))
        self.assertNotIn("env/wolf_territories.xml", answer["inputs"]["files"])

    def test_equal_contexts_share_a_fingerprint(self) -> None:
        """Two reviews of one context are equal; the fingerprint is a SHA-256 text."""
        first, second = self.review(), self.review()
        self.assertEqual(first.answer, second.answer)
        self.assertRegex(first.fingerprint, r"^[0-9a-f]{64}$")
        self.assertEqual(first.answer["fingerprint"], first.fingerprint)


if __name__ == "__main__":
    unittest.main()
