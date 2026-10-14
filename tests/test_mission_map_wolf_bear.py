"""Verify that wolves and bears stay unavailable with the 7.9 text and that no candidate names their files."""

from __future__ import annotations

import builtins
import io
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from dayz_serverman.domain.mission_map_areaflags_candidate import build_areaflags_candidate
from dayz_serverman.domain.mission_map_providers import (
    UNVERIFIED_TERRITORY_PROVIDERS, WOLF_BEAR_REASON, unverified_territory_capability,
)
from dayz_serverman.repositories.mission_map_infected import build_territory_candidate
from dayz_serverman.repositories.mission_map_loot_report import build_loot_report
from dayz_serverman.repositories.mission_map_providers_paths import ProviderReader
from dayz_serverman.repositories.mission_map_terrain import AREAFLAGS_FILE
from tests.mission_map_evaluation_support import FLAGS
from tests.mission_map_loot_report_support import CAPABILITIES, MISSION, loot
from tests.mission_map_mapgroup_support import build, plan
from tests.test_mission_map_infected import BASELINE as TERRITORIES, EVENTS, GLOBALS
from tests.test_mission_map_plan import encounter
from tests.test_mission_map_providers import ProviderFixture, tree_state

# The exact Product Owner text of leaf 7.9, written out here so that a change of the constant fails
TEXT = "Wolf and bear territories are not supported in this version; the pack territory mapping is not verified."
# The two native files that no adapter of this plan writes, in any case or separator spelling
ANIMAL_FILES = re.compile(r"(wolf|bear)_territories", re.IGNORECASE)
REPOSITORY = Path(__file__).resolve().parent.parent
PRODUCTION = REPOSITORY / "runnable" / "src" / "python" / "dayz_serverman"
FRONTEND = REPOSITORY / "runnable" / "src" / "frontend"
# The editor captures the original of each managed file (A4); this is a read set, not a candidate
ORIGINAL_CAPTURE_SET = PRODUCTION / "repositories" / "mission_map_layout.py"
# An offered event and valid population values; an infected filter that ignores the provider takes them
OFFERED = {"event_name": "InfectedVillage", "smin": 0, "smax": 0, "dmin": 3, "dmax": 5}
# Every route by which the load can open, read or inspect a file, as (owner, attribute)
FILE_ROUTES = ((builtins, "open"), (io, "open"), (os, "open"),
               (Path, "read_bytes"), (Path, "read_text"), (Path, "stat"))


def animal(identifier: str, provider: str, *, x: float = 500.0, z: float = 500.0, linked: bool = True,
           excluded: bool = False) -> dict:
    """Return a wolf or bear encounter at a point; its linked loot holds Tier2, so the loot candidates change.

    Its open properties hold an offered event name, so only the provider keeps it out of the infected candidate.
    """
    item = encounter(identifier, provider)
    item.update(centre={"x": x, "z": z}, radius=60.0, properties=dict(OFFERED), excluded=excluded,
                linked_loot={"enabled": linked, "tier": "Tier2" if linked else None, "abundance": "rich",
                             "radius": None})
    return item


def named(test: unittest.TestCase, data: bytes | str | None, where: str) -> None:
    """Fail when the data names the wolf or bear territory file."""
    text = data.decode("latin-1") if isinstance(data, bytes) else (data or "")
    test.assertIsNone(ANIMAL_FILES.search(text), f"{where} names a wolf or bear territory file")


class WolfBearSnapshotTests(ProviderFixture):
    """The load snapshot marks both providers unavailable with the exact text and reads no file for them."""

    def test_text_is_the_exact_product_owner_text(self) -> None:
        """The constant, the domain entry and the window fallback hold the same exact text."""
        self.assertEqual(WOLF_BEAR_REASON, TEXT)
        self.assertEqual(UNVERIFIED_TERRITORY_PROVIDERS, ("native_wolf", "native_bear"))
        self.assertEqual(unverified_territory_capability().to_dict(),
                         {"detected": False, "available": False, "reason": TEXT, "event_names": [], "evidence": {}})
        wording = (FRONTEND / "map_editor_encounter_wording.js").read_text(encoding="utf-8")
        self.assertIn(f'wolfBearReason: "{TEXT}"', wording)

    def test_load_snapshot_marks_wolves_and_bears_unavailable(self) -> None:
        """Without providers and with both other providers present, the two entries stay fixed and come last."""
        expected = {"detected": False, "available": False, "reason": TEXT, "event_names": [], "evidence": {}}
        cases = {"empty": lambda: None, "providers": lambda: (self.seed_native(), self.seed_bandits())}
        for case, seed in cases.items():
            with self.subTest(case=case):
                seed()
                providers = self.providers()
                self.assertEqual(list(providers), ["native_infected", "ai_bandits", "native_wolf", "native_bear"])
                self.assertEqual((providers["native_wolf"], providers["native_bear"]), (expected, expected))
        self.assertTrue(self.providers()["native_infected"]["available"])

    def test_load_reads_no_wolf_or_bear_file_and_changes_nothing(self) -> None:
        """Present wolf and bear files are neither resolved nor opened by any route; the load changes no root."""
        self.seed_native()
        for name in ("wolf_territories.xml", "bear_territories.xml"):
            (self.mission / "env" / name).write_text("<territory-type><territory/></territory-type>", encoding="utf-8")
        before = (tree_state(self.root), tree_state(self.area.parent.parent))
        opened: list[str] = []

        def spy(real):
            """Record the target of one file route and call the real route."""
            def route(target, *args, **kwargs):
                opened.append(str(target))
                return real(target, *args, **kwargs)
            return route

        original = ProviderReader.contained
        patches = [mock.patch.object(owner, name, spy(getattr(owner, name))) for owner, name in FILE_ROUTES]
        with mock.patch.object(ProviderReader, "contained", autospec=True, side_effect=original) as resolved:
            for patch in patches:
                patch.start()
            try:
                providers = self.providers()
            finally:
                for patch in reversed(patches):
                    patch.stop()
        paths = [call.args[1] for call in resolved.call_args_list]
        self.assertIn("env/zombie_territories.xml", paths)
        # The file routes see the infected read too, so a silent spy cannot pass this test
        self.assertTrue(any("zombie_territories" in path for path in opened))
        for path in paths + opened:
            named(self, path, "a provider read")
        self.assertEqual(providers["native_wolf"]["reason"], TEXT)
        self.assertEqual((tree_state(self.root), tree_state(self.area.parent.parent)), before)


class WolfBearCandidateTests(unittest.TestCase):
    """No candidate of 6.6, 6.8, 6.9 or 7.5 names either file, also when the plan holds wolves and bears."""

    def objects(self) -> list[dict]:
        """Return wolf and bear encounters (one excluded, one without linked loot), an infected one and a zone."""
        infected = encounter("inf", "native_infected")
        infected["properties"] = dict(infected["properties"], event_name="InfectedVillage")
        return [animal("wolf", "native_wolf"), animal("bear", "native_bear", x=200.0, z=200.0),
                animal("wolf2", "native_wolf", x=800.0, z=800.0, excluded=True),
                animal("bear2", "native_bear", x=100.0, z=900.0, linked=False), infected, loot("zone", tier="Tier3")]

    def test_loot_candidates_and_report_name_neither_file(self) -> None:
        """The areaflags and mapgroup candidates change, and their paths, bytes and the report name no animal file."""
        value = plan(25, *self.objects())
        areaflags = build_areaflags_candidate(MISSION[AREAFLAGS_FILE], value, FLAGS)
        self.assertNotEqual(areaflags.content, MISSION[AREAFLAGS_FILE])
        named(self, areaflags.content, "the areaflags candidate")
        mapgroups = build(value, MISSION)
        self.assertFalse(mapgroups.refused)
        self.assertTrue(mapgroups.files)
        for item in mapgroups.files:
            named(self, item.path, "a mapgroup candidate path")
            named(self, item.content, f"the mapgroup candidate {item.path}")
        report = build_loot_report(value, capabilities=CAPABILITIES, baseline=MISSION, areaflags=areaflags,
                                   mapgroups=mapgroups)
        # The linked loot of the wolf is a loot object, so the report is not empty of animal encounters
        self.assertIn("wolf", [zone["id"] for zone in report["zones"]])
        named(self, json.dumps(report), "the loot report")
        for zone in report["zones"]:
            for path in zone["files"]:
                named(self, path, f"the files of zone {zone['id']}")

    def test_infected_territory_candidate_takes_no_animal_encounter(self) -> None:
        """Only the infected encounter enters env/zombie_territories.xml; wolves and bears with an offered event add none."""
        candidate = build_territory_candidate(TERRITORIES, self.objects(), events=EVENTS, globals_xml=GLOBALS)
        self.assertEqual(candidate.encounter_ids, ("inf",))
        self.assertEqual(candidate.content.count(b"<zone "), TERRITORIES.count(b"<zone ") + 1)
        named(self, candidate.content, "the infected territory candidate")
        named(self, candidate.owned, "the owned infected block")
        only_animals = [item for item in self.objects() if item.get("provider") in UNVERIFIED_TERRITORY_PROVIDERS]
        self.assertEqual(build_territory_candidate(TERRITORIES, only_animals, events=EVENTS,
                                                   globals_xml=GLOBALS).content, TERRITORIES)

    def test_no_adapter_source_names_either_file(self) -> None:
        """Only the original capture set names the files; no other module and no window file does."""
        found = []
        for path in sorted(PRODUCTION.rglob("*.py")) + sorted(FRONTEND.rglob("*.*")):
            if path.suffix in (".py", ".js", ".html", ".css") and ANIMAL_FILES.search(path.read_text(encoding="utf-8")):
                found.append(path)
        self.assertEqual(found, [ORIGINAL_CAPTURE_SET])


if __name__ == "__main__":
    unittest.main()
