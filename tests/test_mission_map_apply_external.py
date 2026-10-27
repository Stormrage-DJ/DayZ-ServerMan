"""Reconciliation choices of leaf 8.7: incorporate with provenance, replace after backup, unavailable encounters."""

from __future__ import annotations

import hashlib
import re
import unittest

from dayz_serverman.application.mission_map_apply_choices import ApplyRefused
from dayz_serverman.repositories.mission_map_incorporate import OWNED_VALUE_CHANGED, OWNED_VALUE_TEXT
from dayz_serverman.repositories.mission_map_layout import TargetClass
from dayz_serverman.repositories.mission_map_mapgroup_projection import owned_digest as mapgroup_digest
from dayz_serverman.repositories.mission_map_owned import owned_digest
from tests.mission_map_apply_support import ApplyFixture
from tests.mission_map_mapgroup_support import BASELINE, PROTOTYPE, build, contents, plan, zone
from tests.test_mission_map_infected import encounter

PROTO, POS, DIRT, TERRITORY = "mapgroupproto.xml", "mapgrouppos.xml", "mapgroupdirt.xml", "env/zombie_territories.xml"
# A change of a value that the editor does not own: the usage of an ordinary group
FARM, VILLAGE = b'<usage name="Farm" />', b'<usage name="Village" />'
# Line end of the synthetic territory file
CRLF = bytes((13, 10))
# One included infected encounter whose event the mission offers
INFECTED = encounter("e1", x=600.0, z=600.0, radius=40.0)


def choices(external: dict[str, str] | None = None, encounters: dict[str, str] | None = None) -> dict:
    """Return the choices object of an Apply request."""
    return {"external_changes": external or {}, "unavailable_encounters": encounters or {}}


class IncorporateTests(ApplyFixture):
    """A valid external edit becomes part of the working baseline; editor values never compound."""

    def read(self, path: str) -> bytes:
        """Return the current bytes of a mission file."""
        return (self.mission / path).read_bytes()

    def baseline_bytes(self, path: str) -> bytes | None:
        """Return the working-baseline bytes of one file."""
        _original, (baseline, _revision), _applied = self.records()
        item = next(entry for entry in baseline["files"] if entry["path"] == path)
        return (self.folder() / "files" / item["sha256"]).read_bytes() if item["existed"] else None

    def test_global_only_reapply_after_an_incorporated_edit_does_not_compound(self) -> None:
        """Incorporate a usage edit, then apply another global value: ceilings come from the baseline only."""
        self.save([], 50)
        self.run_apply()
        first = self.read(PROTO)
        self.write(PROTO, first.replace(FARM, VILLAGE))
        change = self.review().answer["external_changes"][0]
        self.assertEqual((change["path"], change["owned_changed"], change["incorporate"]),
                         (PROTO, False, {"available": True, "reason": None, "message": None}))
        result = self.run_apply(choices({PROTO: "incorporate"}))
        self.assertEqual((result["result"], result["files"], result["incorporated"]), ("COMMITTED", [], [PROTO]))
        # An incorporation-only commit: the previous state equals the current files, so no restore point
        _original, _baseline, (applied, _revision) = self.records()
        self.assertEqual((applied["files_before"], applied["generated_groups_before"]), ([], applied["generated_groups"]))
        self.assertEqual(self.read(PROTO), first.replace(FARM, VILLAGE))
        _original, (baseline, revision), _applied = self.records()
        self.assertEqual((revision, [item["source"] for item in baseline["provenance"]]), (1, ["original", "adoption"]))
        self.assertEqual(baseline["provenance"][-1]["paths"], [PROTO])
        # The new baseline holds the edit and the original ceilings, never an editor value
        self.assertEqual(self.baseline_bytes(PROTO), PROTOTYPE.replace(FARM, VILLAGE))
        self.assertEqual(self.review().answer["external_changes"], [])
        self.assertEqual(self.run_apply()["result"], "UNCHANGED")
        # A global-only change after the incorporation builds from the baseline, so nothing compounds
        self.save([], 75, expected=0)
        self.run_apply()
        edited = {**BASELINE, PROTO: PROTOTYPE.replace(FARM, VILLAGE)}
        self.assertEqual(self.read(PROTO), contents(build(plan(75), edited))[PROTO])

    def test_external_edit_of_an_editor_owned_value_refuses_incorporation(self) -> None:
        """A hand-edited lootmax refuses Incorporate with the reason; Replace backs the edit up and writes."""
        self.save([], 50)
        self.run_apply()
        first = self.read(PROTO)
        edited = re.sub(rb'(name="Land_Shed" lootmax=")[0-9]+', rb"\g<1>99", first)
        self.assertNotEqual(edited, first)
        self.write(PROTO, edited)
        change = self.review().answer["external_changes"][0]
        self.assertEqual(change["incorporate"], {"available": False, "reason": OWNED_VALUE_CHANGED,
                                                 "message": OWNED_VALUE_TEXT.format(path=PROTO)})
        before = self.snapshot()
        with self.assertRaises(ApplyRefused) as refused:
            self.run_apply(choices({PROTO: "incorporate"}))
        self.assertEqual((refused.exception.reason, refused.exception.path, str(refused.exception)),
                         ("incorporation_refused", PROTO, OWNED_VALUE_TEXT.format(path=PROTO)))
        self.assertEqual(self.snapshot(), before)
        result = self.run_apply(choices({PROTO: "replace"}))
        digest = hashlib.sha256(edited).hexdigest()
        self.assertEqual((result["files"], result["replaced"]), ([PROTO], [{"path": PROTO, "sha256": digest}]))
        self.assertEqual(self.read(PROTO), first)
        _original, (baseline, revision), (applied, _revision) = self.records()
        self.assertEqual((revision, baseline["provenance"][-1]["source"]), (1, "replaced"))
        self.assertEqual(baseline["provenance"][-1]["files"][0]["sha256"], digest)
        self.assertEqual((self.folder() / "files" / digest).read_bytes(), edited)
        backup = next(item for item in applied["files_before"] if item["path"] == PROTO)
        self.assertEqual(backup["sha256"], digest)

    def test_territory_edit_outside_the_block_is_incorporated(self) -> None:
        """An admin comment outside the marked block stays; the new baseline has no editor marker."""
        self.save([INFECTED])
        self.run_apply()
        edited = self.read(TERRITORY).replace(b"<territory-type>\r\n", b"<territory-type>\r\n<!-- admin -->\r\n")
        self.write(TERRITORY, edited)
        self.assertTrue(self.review().answer["external_changes"][0]["incorporate"]["available"])
        self.run_apply(choices({TERRITORY: "incorporate"}))
        self.assertEqual(self.read(TERRITORY), edited)
        self.assertNotIn(b"mission-map", self.baseline_bytes(TERRITORY))
        self.assertIn(b"<!-- admin -->", self.baseline_bytes(TERRITORY))
        # An edit inside the block is an editor value
        self.write(TERRITORY, edited.replace(b'r="40"', b'r="41"'))
        self.assertFalse(self.review().answer["external_changes"][0]["incorporate"]["available"])

    def test_placement_edits_are_incorporated_with_detached_placements_reversed(self) -> None:
        """Comments in both placement files stay; the baselines keep the original placement bindings."""
        self.save([zone("rich")])
        self.run_apply()
        pos, dirt = self.read(POS), self.read(DIRT)
        self.assertIn(b"ServerManMap_G_", dirt)
        self.write(POS, pos.replace(b"<map>\r\n", b"<map>\r\n<!-- admin pos -->\r\n"))
        self.write(DIRT, dirt.replace(b"<map>\r\n", b"<map>\r\n<!-- admin dirt -->\r\n"))
        answer = self.review().answer
        self.assertEqual([item["incorporate"]["available"] for item in answer["external_changes"]], [True, True])
        self.run_apply(choices({POS: "incorporate", DIRT: "incorporate"}))
        self.assertIn(b"<!-- admin pos -->", self.read(POS))
        self.assertIn(b"<!-- admin dirt -->", self.read(DIRT))
        for path, data in ((POS, pos), (DIRT, dirt)):
            self.assertEqual(mapgroup_digest(path, self.read(path)), mapgroup_digest(path, data))
            self.assertEqual(mapgroup_digest(path, self.baseline_bytes(path)), mapgroup_digest(path, BASELINE[path]))
        self.assertEqual(self.review().answer["external_changes"], [])
        self.assertEqual(self.run_apply()["result"], "UNCHANGED")

    def test_incorporated_edit_that_the_builder_refuses_refuses_apply(self) -> None:
        """A default ceiling is not an owned value; when it becomes unreadable the new candidate refuses, no write."""
        self.save([], 50)
        self.run_apply()
        self.write(PROTO, self.read(PROTO).replace(b'<default name="group" lootmax="6" />',
                                                   b'<default name="group" lootmax="six" />'))
        self.assertTrue(self.review().answer["external_changes"][0]["incorporate"]["available"])
        before = self.snapshot()
        with self.assertRaises(ApplyRefused) as refused:
            self.run_apply(choices({PROTO: "incorporate"}))
        self.assertEqual(refused.exception.reason, "candidate_refused")
        self.assertEqual(self.snapshot(), before)

    def test_incorporated_territory_edit_that_the_builder_refuses_refuses_apply(self) -> None:
        """Text after the root end tag is not an owned value, but the territory builder refuses it; no write."""
        self.save([INFECTED])
        self.run_apply()
        self.write(TERRITORY, self.read(TERRITORY) + b"<!-- trailing -->" + CRLF + b"<extra/>" + CRLF)
        self.assertTrue(self.review().answer["external_changes"][0]["incorporate"]["available"])
        before = self.snapshot()
        # The provider reads the offered events from the current file, so the encounter also needs its choice
        with self.assertRaises(ApplyRefused) as refused:
            self.run_apply(choices({TERRITORY: "incorporate"}, {"e1": "exclude"}))
        self.assertEqual(refused.exception.reason, "candidate_refused")
        self.assertEqual(self.snapshot(), before)

    def test_removed_dirt_placement_is_an_owned_change(self) -> None:
        """Removing a generated placement changes the placement multiset: only Replace is available."""
        self.save([zone("rich")])
        self.run_apply()
        dirt = self.read(DIRT)
        line = re.search(rb"[^\n]*ServerManMap_G_[^\n]*\n", dirt).group(0)
        self.write(DIRT, dirt.replace(line, b"", 1))
        self.assertFalse(self.review().answer["external_changes"][0]["incorporate"]["available"])
        self.run_apply(choices({DIRT: "replace"}))
        self.assertEqual(self.read(DIRT), dirt)
        self.assertEqual(owned_digest(TargetClass.MISSION, DIRT, self.read(DIRT)),
                         owned_digest(TargetClass.MISSION, DIRT, dirt))


class ChoiceTests(ApplyFixture):
    """Every external change and unavailable encounter needs exactly one valid choice."""

    def test_missing_extra_and_invalid_choices_refuse_without_a_write(self) -> None:
        """No choice, a choice for an unchanged file and an unknown value refuse before any write."""
        self.save([], 50)
        self.run_apply()
        self.write(PROTO, self.read_proto().replace(FARM, VILLAGE))
        before = self.snapshot()
        cases = [(choices(), "choice_required"), (choices({PROTO: "replace", POS: "replace"}), "choice_required"),
                 (choices({PROTO: "keep"}), "choices_invalid"), ({"external_changes": {}}, "choices_invalid"),
                 (choices({PROTO: "replace"}, {"x": "exclude"}), "choice_required"),
                 (choices(encounters={"x": "include"}), "choices_invalid")]
        for value, reason in cases:
            with self.subTest(value=value):
                with self.assertRaises(ApplyRefused) as refused:
                    self.run_apply(value)
                self.assertEqual(refused.exception.reason, reason)
                self.assertEqual(self.snapshot(), before)

    def read_proto(self) -> bytes:
        """Return the current prototype bytes."""
        return (self.mission / PROTO).read_bytes()

    def test_unavailable_encounter_needs_the_exclude_choice(self) -> None:
        """An encounter whose event is not offered is never written; Apply records the exclusion (R11)."""
        unzoned = encounter("u", "Unzoned", x=500.0, z=500.0, radius=50.0)
        self.save([unzoned, INFECTED])
        with self.assertRaises(ApplyRefused) as refused:
            self.run_apply()
        self.assertEqual(refused.exception.reason, "choice_required")
        result = self.run_apply(choices(encounters={"u": "exclude"}))
        self.assertEqual(result["excluded_encounters"], ["u"])
        _original, _baseline, (applied, _revision) = self.records()
        self.assertEqual(applied["excluded_encounters"], ["u"])
        territory = (self.mission / TERRITORY).read_bytes()
        self.assertNotIn(b"Unzoned", territory)
        self.assertIn(b'r="40"', territory)
        self.assertEqual(hashlib.sha256(territory).hexdigest(),
                         next(item["sha256"] for item in applied["files_after"] if item["path"] == TERRITORY))


if __name__ == "__main__":
    unittest.main()
