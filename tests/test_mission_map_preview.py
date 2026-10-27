"""Apply preview of leaf 8.6: answer parts, family gate, shared targets, capture check and refusals."""

from __future__ import annotations

import dataclasses
import shutil
import unittest
from pathlib import Path

from dayz_serverman.application.mission_map_plans import AssociationMismatch, MissionPathChanged
from dayz_serverman.application.mission_map_preview import MissionMapPreview, NoSavedPlan
from dayz_serverman.bridge.contracts import ensure_json_value
from dayz_serverman.domain.mission_map_families import family_gate
from dayz_serverman.domain.mission_map_preview_texts import CAPTURE_RISK_TEXT
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import TargetClass, association_folder, target_key
from dayz_serverman.repositories.mission_map_plans import PlanRecordInvalid
from dayz_serverman.repositories.mission_map_terrain import read_capabilities
from tests.mission_map_mapgroup_support import PROTOTYPE, zone
from tests.mission_map_preview_support import VERIFIED, PreviewFixture
from tests.test_mission_map_infected import encounter
from tests.test_mission_map_plan import stored_plan
from tests.test_mission_map_plan_service import profile

# Top-level fields of the preview answer
ANSWER_FIELDS = {"version", "profile", "settings_revision", "plan", "mission", "capabilities", "bounds", "refused",
                 "refusals", "choices", "base", "objects", "loot", "encounters", "infected", "affected_files",
                 "external_changes", "coordinated_changes", "other_profiles", "capture", "inputs", "fingerprint"}


def tree(root: Path) -> dict[str, bytes]:
    """Return every file below a folder with its bytes."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class PreviewAnswerTests(PreviewFixture):
    """The answer lists every accepted part and the preview writes nothing."""

    def test_answer_parts_and_no_write(self) -> None:
        """Loot report, encounters, files, other profiles, capture result and bounds; no file changes."""
        self.save([zone("rich"), encounter("e1", x=600.0, z=600.0, radius=40.0)], 25)
        before, area = tree(self.root), tree(self.area)
        answer = self.preview.preview("main")
        ensure_json_value(answer)
        self.assertEqual((tree(self.root), tree(self.area)), (before, area))
        self.assertEqual(set(answer), ANSWER_FIELDS)
        self.assertEqual((answer["refused"], answer["refusals"], answer["base"]), (False, [], "current"))
        self.assertEqual(answer["plan"]["revision"], 0)
        self.assertEqual(answer["mission"]["mission_name"], VERIFIED)
        self.assertEqual(answer["encounters"], {"written": ["e1"], "unavailable": [], "excluded": []})
        self.assertEqual([zone_["id"] for zone_ in answer["loot"]["zones"]], ["zone"])
        self.assertEqual(answer["other_profiles"], [{"profile_id": "second", "display_name": "Second"}])
        self.assertEqual((answer["capture"]["required"], answer["capture"]["result"], answer["capture"]["text"]),
                         (True, "passed", CAPTURE_RISK_TEXT))
        self.assertEqual([item["path"] for item in answer["capture"]["files"]][-2:],
                         ["env/wolf_territories.xml", "env/bear_territories.xml"])
        self.assertEqual([item["path"] for item in answer["affected_files"]],
                         ["mapgroupproto.xml", "mapgrouppos.xml", "mapgroupdirt.xml", "env/zombie_territories.xml"])
        self.assertTrue(answer["bounds"]["bounds_confirmed"])
        self.assertEqual(answer["objects"]["global"], {"current": 25, "applied": None})
        self.assertEqual(self.preview.review("main").fingerprint, answer["fingerprint"])

    def test_unconfirmed_bounds_are_listed_and_refuse_tier_writes(self) -> None:
        """Unconfirmed bounds show in the answer; a tier override then refuses with bounds_not_confirmed."""
        tiered = dict(zone("rich"), tier="Tier2")
        self.save([tiered], bounds_confirmed=False)
        answer = self.preview.preview("main")
        self.assertFalse(answer["bounds"]["bounds_confirmed"])
        self.assertEqual(answer["capabilities"]["tier_writes"]["reason"], "bounds_not_confirmed")
        self.assertIn("bounds_not_confirmed", [item["reason"] for item in answer["refusals"]])
        self.assertEqual(answer["loot"]["unavailable"][0]["reason"], "bounds_not_confirmed")
        self.save([tiered], bounds_confirmed=True, expected=0)
        self.assertEqual(self.preview.preview("main")["affected_files"][0]["path"], "areaflags.map")


class FamilyGateTests(PreviewFixture):
    """The gate comes from a fresh capability read of the target mission and never raises in the preview (6.11)."""

    def test_unverified_family_reports_the_refusal(self) -> None:
        """deerisle: the answer carries the gate and the refusal text; the plan terrain ID does not matter."""
        self.save([zone("rich")], identifier="other", terrain_id="chernarusplus")
        answer = self.preview.preview("other")
        self.assertEqual(answer["capabilities"]["family"], family_gate("deerisle").to_dict())
        refusal = next(item for item in answer["refusals"] if item["reason"] == "family_not_verified")
        self.assertEqual(refusal["message"], family_gate("deerisle").text)
        self.assertTrue(answer["refused"])
        # The verified mission stays verified for a plan that names an unverified terrain
        self.save([zone("rich")], terrain_id="deerisle")
        main = self.preview.preview("main")
        self.assertTrue(main["capabilities"]["family"]["verified"])
        self.assertFalse(main["refused"])

    def test_gate_value_is_bound_by_the_fingerprint(self) -> None:
        """Another capability read gives another gate and another fingerprint."""
        self.save([zone("rich")])
        reads = []

        def capabilities(mission: Path):
            reads.append(mission.name)
            return dataclasses.replace(read_capabilities(mission), terrain_id=None)
        other = MissionMapPreview(self.plans, self.profiles, medical_baseline=self.medical.zone_baseline_path,
                                  capabilities=capabilities)
        unknown = other.preview("main")
        self.assertEqual(reads, [VERIFIED])
        self.assertEqual(unknown["capabilities"]["family"]["reason"], "terrain_family_unknown")
        self.assertIn("terrain_family_unknown", [item["reason"] for item in unknown["refusals"]])
        self.assertNotEqual(unknown["fingerprint"], self.preview.preview("main")["fingerprint"])


class SharedTargetTests(PreviewFixture):
    """Other profiles of the same mission target are named (A4 condition 1)."""

    def test_profiles_that_resolve_to_the_target_or_have_a_folder_are_named(self) -> None:
        """second shares the mission, other does not; a deleted profile with a folder has no display name."""
        self.save([zone("rich")])
        self.save([zone("rich")], identifier="second")
        self.save([zone("rich")], identifier="other")
        self.assertEqual([item["profile_id"] for item in self.preview.preview("second")["other_profiles"]], ["main"])
        self.assertEqual(self.preview.preview("other")["other_profiles"], [])
        association_folder(self.area, "gone", self.key()).mkdir(parents=True)
        # A profile whose mission cannot be resolved resolves to no target
        self.profiles.records["broken"] = profile("broken", "mpmissions\\missing")
        self.assertEqual(self.preview.preview("main")["other_profiles"],
                         [{"profile_id": "gone", "display_name": None},
                          {"profile_id": "second", "display_name": "Second"}])


class CaptureCheckTests(PreviewFixture):
    """The capture check result before the first Apply on a target (D2)."""

    def test_copied_editor_output_refuses_the_capture(self) -> None:
        """A copy of an applied mission is recognized by the ledger; the refusal names the operation."""
        self.save([zone("rich")], 25)
        operation = self.apply(self.review())
        self.assertFalse(self.preview.preview("main")["capture"]["required"])
        copy = self.root / "mpmissions" / "copy.chernarusplus"
        shutil.copytree(self.mission, copy)
        self.profiles.records["third"] = profile("third", "mpmissions\\copy.chernarusplus")
        self.save([zone("rich")], identifier="third")
        answer = self.preview.preview("third")
        capture = answer["capture"]
        self.assertEqual((capture["result"], capture["reason"], capture["adoption_required"]),
                         ("refused", "editor_output", True))
        self.assertEqual((capture["match"]["operation_id"], capture["match"]["profile_id"]), (operation, "main"))
        self.assertIn(f"editor output from operation {operation}", capture["message"])
        self.assertIn("editor_output", [item["reason"] for item in answer["refusals"]])

    def test_reserved_names_refuse_the_capture(self) -> None:
        """A reserved group in a current file is a D8 conflict of the capture."""
        self.write("mapgroupproto.xml", PROTOTYPE.replace(b"Land_Unplaced", b"ServerManMap_G_0123456789ab"))
        self.save([zone("rich")])
        capture = self.preview.preview("main")["capture"]
        self.assertEqual((capture["result"], capture["reason"]), ("refused", "reserved_conflict"))


class SavedPlanTests(PreviewFixture):
    """The preview reviews only a valid saved plan of the active association."""

    def test_missing_invalid_and_stale_plans_raise(self) -> None:
        """No plan, invalid saved data (also an out-of-bounds encounter, QA75-O1) and another revision."""
        self.assertRaises(NoSavedPlan, self.preview.preview, "main")
        self.save([zone("rich")])
        self.assertRaises(RevisionConflict, self.preview.preview, "main", 3)
        self.assertEqual(self.preview.preview("main", 0)["plan"]["revision"], 0)
        raw = stored_plan([encounter("far", x=500.0, z=5e6)])
        raw.update(association=self.plans.active_association("main"), background=None)
        folder = association_folder(self.area, "main", self.key())
        VersionedJsonRepository(folder / "plan.json", 1).save({"plan": raw}, 0)
        self.assertRaises(PlanRecordInvalid, self.preview.preview, "main")

    def test_association_checks_and_records_stale(self) -> None:
        """A changed runtime folder refuses; a stale record reference is a refusal of the answer."""
        self.save([zone("rich")])
        plan = self.plans.load("main")["plan"]
        plan["records"]["baseline"] = {"revision": 4, "manifest_sha256": "0" * 64}
        self.plans.save("main", plan, 0)
        stale = self.preview.preview("main")
        self.assertIn("records_stale", [item["reason"] for item in stale["refusals"]])
        plan["records"]["baseline"] = None
        plan["association"] = self.plans.active_association("main")
        plan["association"]["runtime_profile_key"] = self.runtime_key()
        self.plans.save("main", plan, 1)
        self.assertNotIn("records_stale", [item["reason"] for item in self.preview.preview("main")["refusals"]])
        # The profile settings now name another runtime folder than the saved plan
        (self.root / "profile_other").mkdir()
        self.profiles.records["main"] = profile("main", f"mpmissions\\{VERIFIED}", "profile_other")
        self.assertRaises(AssociationMismatch, self.preview.preview, "main")

    def test_mission_path_change_raises(self) -> None:
        """A moved mission keeps the old plan read only; the preview refuses as save does."""
        self.save([zone("rich")])
        moved = self.root / "mpmissions" / "moved.chernarusplus"
        shutil.copytree(self.mission, moved)
        self.profiles.records["main"] = profile("main", "mpmissions\\moved.chernarusplus", "profile_main")
        old = association_folder(self.area, "main", self.key())
        plan = VersionedJsonRepository(old / "plan.json", 1).load().fields["plan"]
        plan["association"]["mission_root"] = "mpmissions\\moved.chernarusplus"
        VersionedJsonRepository(old / "plan.json", 1).save({"plan": plan}, 0)
        self.assertRaises(MissionPathChanged, self.preview.preview, "main")


    def runtime_key(self) -> str:
        """Return the target key of the runtime folder of profile main."""
        return target_key(TargetClass.RUNTIME, (self.root / "profile_main").resolve())


if __name__ == "__main__":
    unittest.main()
