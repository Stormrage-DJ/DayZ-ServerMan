"""A181 tests 9 to 11 and 15: S1 validation, point and object phases, answer shapes and the evaluation field."""

from __future__ import annotations

import copy
import json
from unittest import mock

from dayz_serverman.application import mission_map_inspection_run
from dayz_serverman.application.mission_map_evaluation import ObjectTierRead
from dayz_serverman.bridge.contracts import ErrorCode, ensure_json_value
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain.mission_map_plan import configuration_fingerprint
from dayz_serverman.repositories import mission_map_read_pass
from dayz_serverman.repositories.mission_map_layout import association_folder
from dayz_serverman.repositories.mission_map_read_pass import MissionReadPass
from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.mission_map_inspection_support import InspectionFixture
from tests.test_mission_map_plan import circle, square


def failure(action) -> ApplicationCallError:
    """Return the refusal that an action raises."""
    try:
        action()
    except ApplicationCallError as error:
        return error
    raise AssertionError("the action did not refuse")


def facts(error: ApplicationCallError) -> tuple:
    """Return the public facts of a refusal."""
    return error.code, error.safe_message, error.retryable, json.dumps(error.details, sort_keys=True)


class InspectValidationTests(InspectionFixture):
    """S1: the start validates like save, with no reader, before any mission file opens."""

    def test_9_an_invalid_draft_gives_saves_exact_errors_and_opens_no_mission_file(self) -> None:
        """Image path, D1 problems, a wrong document type and association defects refuse exactly as save does.

        Save's association rule covers the mission root and the runtime profile key (QA-4.2-1).
        """
        image = self.draft(objects=[circle("c1")])
        image["background"] = {"file_name": "map.png", "sha256": "ab" * 32, "byte_size": 10, "pixel_width": 64,
                               "pixel_height": 64, "path": "C:\\maps\\map.png",
                               "calibration": {"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": -1.0, "f": 64.0}}
        overlapping = self.draft(objects=[circle("c1", radius=-5.0), circle("c1")])
        root = self.draft(objects=[circle("c1")])
        root["association"]["mission_root"] = "mpmissions\\dayzOffline.other"
        runtime = self.draft(objects=[circle("c1")])
        runtime["association"]["runtime_profile_key"] = "f" * 32
        cases = {"image path": image, "d1 problems": overlapping, "not a document": ["plan"],
                 "mission root": root, "runtime profile key": runtime}
        for name, draft in cases.items():
            with self.subTest(name):
                saved = failure(lambda: self.save(copy.deepcopy(draft)))
                holds = self.reader.holds
                with mock.patch.object(mission_map_read_pass, "open_shared", side_effect=AssertionError) as opener:
                    inspected = failure(lambda: self.start({"point": {"x": 1, "z": 1}}, copy.deepcopy(draft)))
                opener.assert_not_called()
                self.assertEqual(facts(inspected), facts(saved))
                # D1 refusals take no reader; an association check runs in the context step
                self.assertEqual(self.reader.holds - holds, 1 if name in ("mission root", "runtime profile key") else 0)
        self.assertEqual(self.jobs.live(), 0)
        self.assertEqual(facts(failure(lambda: self.save(copy.deepcopy(root))))[:2],
                         (ErrorCode.PROFILE_CONTEXT_MISMATCH, "the plan names another mission root"))
        self.assertEqual(facts(failure(lambda: self.save(copy.deepcopy(runtime))))[:2],
                         (ErrorCode.PROFILE_CONTEXT_MISMATCH, "the plan names another runtime profile folder"))

    def test_9_the_path_change_state_checks_the_draft_against_the_shown_association(self) -> None:
        """Inspect stays available in the path-change state (Product Owner, QA-4.2-1).

        A draft of the shown association is read; a different mission root refuses as save's rule does.
        """
        self.old_folder("earlier-root")
        loaded = self.load()
        self.assertEqual(loaded["read_only_reason"], "mission_path_changed")
        draft = dict(copy.deepcopy(loaded["plan"]), objects=[circle("c1")])
        answer, _phases = self.finish(self.start({"point": {"x": 100.0, "z": 100.0}}, draft))
        self.assertEqual((answer["status"], answer["result"]["plan_source"]), ("done", "draft"))
        other = copy.deepcopy(draft)
        other["association"]["mission_root"] = "mpmissions\\dayzOffline.other"
        error = failure(lambda: self.start({"point": {"x": 1, "z": 1}}, other))
        self.assertEqual((error.code, error.safe_message, error.details["reason"]),
                         (ErrorCode.PROFILE_CONTEXT_MISMATCH, "the plan names another mission root",
                          "association_mismatch"))
        active = self.draft(objects=[circle("c1")])
        error = failure(lambda: self.start({"point": {"x": 1, "z": 1}}, active))
        self.assertEqual((error.code, error.safe_message),
                         (ErrorCode.PROFILE_CONTEXT_MISMATCH, "the plan belongs to another profile or mission"))

    def test_saved_plan_states_and_targets(self) -> None:
        """Missing and invalid saved plans and bad targets keep their accepted refusals."""
        missing = failure(lambda: self.start({"point": {"x": 1, "z": 1}}))
        self.assertEqual((missing.code, missing.details["reason"], missing.safe_message),
                         (ErrorCode.INVALID_REQUEST, "invalid_document", "No saved plan is available to inspect."))
        plan = self.saved([circle("c1")])
        for target in ({"object": "absent"}, {"point": {"x": True, "z": 1}}, {"point": {"x": float("nan"), "z": 1}},
                       {"point": {"x": 1}}, {"object": "c1", "point": {"x": 1, "z": 1}}, None):
            with self.subTest(target=target):
                error = failure(lambda: self.start(target))
                self.assertEqual((error.code, error.details["reason"]), (ErrorCode.INVALID_REQUEST, "invalid_target"))
        folder = association_folder(self.area, "main", plan["association"]["mission_key"])
        (folder / "plan.json").write_text("{", encoding="utf-8")
        invalid = failure(lambda: self.start({"point": {"x": 1, "z": 1}}))
        self.assertEqual((invalid.code, invalid.details["reason"]), (ErrorCode.REVISION_CONFLICT, "invalid_saved_data"))
        with mock.patch.object(self.profiles, "read", side_effect=ProfileNotFound("gone")):
            unknown = failure(lambda: self.start({"point": {"x": 1, "z": 1}}))
        self.assertEqual((unknown.code, unknown.details["reason"]), (ErrorCode.NOT_FOUND, "profile_not_found"))


class InspectPhaseTests(InspectionFixture):
    """Point and object answers with a stepping clock: two capture slices, then sampling across calls."""

    def counted_passes(self) -> list[bool]:
        """Record the retain flag of every read pass that a run creates."""
        created: list[bool] = []

        def factory(mission, *, retain, clock, buildings=False):
            """Count, then build the real pass."""
            created.append(retain)
            return MissionReadPass(mission, retain=retain, clock=clock, buildings=buildings)
        patcher = mock.patch.object(mission_map_inspection_run, "MissionReadPass", factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        return created

    def test_10_a_point_answer_publishes_in_the_call_that_ends_capture(self) -> None:
        """The capture takes two calls; the second one answers without a proof pass."""
        self.saved([circle("c1", x=500.0, z=500.0, radius=600.0)])
        created = self.counted_passes()
        first = self.start({"point": {"x": 500.0, "z": 500.0}})
        self.assertEqual(first["job"]["phase"], "reading")
        answer = self.proceed(first["job"]["id"])
        self.assertEqual(answer["status"], "done")
        self.assertEqual(created, [True])
        result = answer["result"]
        self.assertEqual((result["plan_source"], result["plan_revision"], result["winner"]["id"]), ("saved", 0, "c1"))
        self.assertEqual(result["header_cell"], {"i": 16, "j": 16})
        self.assertIsNotNone(result["original_tier"])
        self.assertEqual(self.jobs.live(), 0)
        self.assertEqual(self.reader.holds, 3)

    def test_11_object_runs_capture_sampling_proof_and_done(self) -> None:
        """Sampling calls take no reader; every capture and proof slice holds it and releases it."""
        self.saved([circle("c1", x=500.0, z=500.0, radius=400.0), square("p1", x=0.0, z=0.0, size=300.0)])
        created = self.counted_passes()
        real_slice, real_sample = MissionReadPass.read_slice, ObjectTierRead.advance
        slice_holds, sample_holds = [], []

        def read_slice(read, deadline):
            """Record whether the reader side is held during a slice."""
            slice_holds.append(self.reader.held)
            return real_slice(read, deadline)

        def sample(read, deadline=None):
            """Record whether the reader side is held during sampling."""
            sample_holds.append(self.reader.held)
            return real_sample(read, deadline)
        with mock.patch.object(MissionReadPass, "read_slice", read_slice), \
                mock.patch.object(ObjectTierRead, "advance", sample):
            answer, phases = self.finish(self.start({"object": "c1"}))
        self.assertEqual(answer["status"], "done")
        self.assertEqual(created, [True, False])
        self.assertTrue(slice_holds and all(slice_holds))
        self.assertTrue(len(sample_holds) > 2 and not any(sample_holds))
        self.assertFalse(self.reader.held)
        # Phases in order: capture reading, sampling, proof reading
        collapsed = [phase for index, phase in enumerate(phases) if index == 0 or phases[index - 1] != phase]
        self.assertEqual(collapsed, ["reading", "sampling", "reading"])
        result = answer["result"]
        self.assertEqual(result["plan_source"], {"saved": 0})
        self.assertEqual(result["cells"], "d9")
        self.assertTrue(result["original_tiers"])

    def test_a_draft_object_is_bound_to_its_fingerprint(self) -> None:
        """A draft that differs from saved is evaluated as sent and names its fingerprint."""
        self.saved([circle("c1")])
        draft = self.draft(objects=[circle("c1"), circle("c2", x=700.0, z=700.0)])
        answer, _phases = self.finish(self.start({"object": "c2"}, draft))
        self.assertEqual(answer["result"]["plan_source"], {"draft": configuration_fingerprint(draft)})
        point, _phases = self.finish(self.start({"point": {"x": 700.0, "z": 700.0}}, draft))
        self.assertEqual((point["result"]["plan_source"], point["result"]["plan_revision"]), ("draft", None))


class InspectShapeTests(InspectionFixture):
    """A181 test 15: the contract shapes of running, done and stale answers."""

    def test_15_running_done_and_stale_shapes(self) -> None:
        """Reading has null rows, sampling has integer rows, done has only the result, stale ends the job."""
        self.saved([circle("c1", x=500.0, z=500.0, radius=400.0)])
        answer = self.start({"object": "c1"})
        seen = {}
        while answer["status"] == "running":
            ensure_json_value(answer)
            self.assertEqual(set(answer), {"status", "job"})
            self.assertEqual(set(answer["job"]), {"id", "phase", "rows_done", "rows_total"})
            seen.setdefault(answer["job"]["phase"], answer["job"])
            answer = self.proceed(answer["job"]["id"])
        self.assertEqual((seen["reading"]["rows_done"], seen["reading"]["rows_total"]), (None, None))
        self.assertIsInstance(seen["sampling"]["rows_done"], int)
        self.assertEqual(seen["sampling"]["rows_total"], 32)
        self.assertEqual(set(answer), {"status", "result"})
        ensure_json_value(answer)
        # A changed file between capture and proof ends the job stale
        stale = self.start({"object": "c1"})
        job_id = stale["job"]["id"]
        (self.mission / "cfglimitsdefinition.xml").write_text("<lists/>", encoding="utf-8")
        stale, _phases = self.finish(stale)
        self.assertEqual(stale, {"status": "stale", "job": {"id": job_id, "ended": True}})
        retired = failure(lambda: self.proceed(job_id))
        self.assertEqual(retired.details["reason"], "inspection_unknown")


class EvaluationFieldTests(InspectionFixture):
    """Load and save also return the evaluation of the plan (4.6 needs it before 5.1)."""

    def test_load_and_save_return_the_evaluation(self) -> None:
        """The evaluation names the saved revision and one entry per object, without tier sets."""
        plan = self.draft(objects=[circle("c1"), square("p1")])
        saved = self.save(plan)
        self.assertEqual(saved["evaluation"]["revision"], 0)
        loaded = self.load()
        self.assertEqual(loaded["evaluation"], saved["evaluation"])
        entries = {entry["id"]: entry for entry in loaded["evaluation"]["objects"]}
        self.assertEqual(set(entries), {"c1", "p1"})
        self.assertTrue(entries["p1"]["tier_writable"])
        self.assertNotIn("effective_tiers", entries["p1"])
        ensure_json_value(loaded["evaluation"])
