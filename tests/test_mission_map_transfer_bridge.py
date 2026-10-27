"""Leaves 10.6 and 10.33: the transfer coordinator: export, the import preview and the import, with D16 reasons."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.mission_map_transfer import ConfirmationRequired, TransferRefused  # noqa: E402
from dayz_serverman.application.mission_map_transfer_coordinator import (  # noqa: E402
    REFUSAL_TEXTS, MissionMapTransferCoordinator, transfer_refusal,
)
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.bridge_composition import OBSERVER_READ_METHODS  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.mission_map_values import PlanProblem, PlanValidationError  # noqa: E402
from dayz_serverman.domain.models import RevisionConflict  # noqa: E402


class FakeTransfer:
    """A transfer service that answers or refuses as the test says."""

    def __init__(self, answer=None, error: Exception | None = None) -> None:
        """Keep the answer or the error of `export_plan`."""
        self.answer, self.error, self.calls = answer, error, []

    def export_plan(self, profile_id):
        """Record the profile, then answer or raise."""
        self.calls.append(profile_id)
        if self.error is not None:
            raise self.error
        return self.answer


def dispatch(transfer: FakeTransfer, parameters: dict) -> dict:
    """Dispatch one export call through a bridge facade with only the transfer handlers."""
    bridge = BridgeFacade(MissionMapTransferCoordinator(transfer, None).handlers())
    return bridge.dispatch({"contract_version": 1, "request_id": "t", "method": "export_mission_map_plan",
                            "parameters": parameters})


class ExportHandlerTests(unittest.TestCase):
    """The handler takes only `profile_id` and returns `text` and `revision`; it takes no path."""

    def test_the_answer_is_text_and_revision(self) -> None:
        """The service answer passes through, without other fields."""
        transfer = FakeTransfer({"text": "{}\n", "revision": 4, "plan": {"x": 1}})
        result = dispatch(transfer, {"profile_id": "alpha"})
        self.assertEqual((result["success"], result["value"]), (True, {"text": "{}\n", "revision": 4}))
        self.assertEqual(transfer.calls, ["alpha"])

    def test_other_parameters_are_refused_before_the_read(self) -> None:
        """A path or a missing profile is an invalid document; the service is not called."""
        transfer = FakeTransfer({"text": "", "revision": 0})
        for parameters in ({}, {"profile_id": "alpha", "path": "C:/x.json"}):
            with self.subTest(parameters=parameters):
                result = dispatch(transfer, parameters)
                self.assertEqual((result["error"]["code"], result["error"]["details"]["reason"]),
                                 ("INVALID_REQUEST", "invalid_document"))
        self.assertEqual(transfer.calls, [])

    def test_no_valid_plan_has_its_reason_and_the_product_owner_text(self) -> None:
        """INVALID_REQUEST with `no_valid_plan`, which exits 3 (D13)."""
        error = TransferRefused("there is no valid plan to export (CORRUPT)", "no_valid_plan", "export")
        result = dispatch(FakeTransfer(error=error), {"profile_id": "alpha"})["error"]
        self.assertEqual((result["code"], result["details"]["reason"], result["message"]),
                         ("INVALID_REQUEST", "no_valid_plan",
                          "There is no valid map plan to export. Run map show for the state of the plan."))

    def test_other_failures_keep_the_map_editor_translation(self) -> None:
        """A storage failure of the read is STORAGE_FAILURE."""
        result = dispatch(FakeTransfer(error=OSError("disk")), {"profile_id": "alpha"})["error"]
        self.assertEqual((result["code"], result["details"]["reason"]), ("STORAGE_FAILURE", "storage_failure"))


class ClosedReasonTests(unittest.TestCase):
    """Each closed reason of D16 gets its code and the Product Owner text of its side."""

    def test_each_reason_and_side(self) -> None:
        """`mission_path_changed` is PROFILE_CONTEXT_MISMATCH; the others are INVALID_REQUEST."""
        for (reason, side), text in REFUSAL_TEXTS.items():
            if reason == "plan_limit":
                continue
            with self.subTest(reason=reason, side=side):
                error = transfer_refusal(TransferRefused("developer text", reason, side))
                code = "PROFILE_CONTEXT_MISMATCH" if reason == "mission_path_changed" else "INVALID_REQUEST"
                self.assertEqual((error.code.value, error.details["reason"], error.safe_message), (code, reason, text))
        self.assertEqual(len(REFUSAL_TEXTS), 7)

    def test_a_plan_limit_has_the_product_owner_text_with_the_counts(self) -> None:
        """The plan-level limit names the result counts (10.33); the details carry them too."""
        error = transfer_refusal(TransferRefused("developer text", "plan_limit", "destination",
                                                 {"objects": 2001, "vertices": 120}))
        self.assertEqual((error.code.value, error.details, error.safe_message),
                         ("INVALID_REQUEST", {"reason": "plan_limit", "objects": 2001, "vertices": 120},
                          "The result would hold 2001 objects and 120 polygon vertices. The limits are 2,000 objects "
                          "and 50,000 vertices. Nothing was changed."))


class FakeImport:
    """A transfer service whose import preview and import answer or raise as the test says (10.33)."""

    def __init__(self, error: Exception | None = None) -> None:
        """Keep the error of both calls; record each call."""
        self.error, self.calls = error, []

    def preview_import(self, profile_id, text, mode, availability):
        """Record the call, then answer a report or raise."""
        self.calls.append(("preview", profile_id, text, mode))
        if self.error is not None:
            raise self.error
        return {"mode": mode, "destination_revision": 2}

    def import_plan(self, profile_id, text, mode, expected_revision, confirmed, availability):
        """Record the call with its confirmation, then answer with the saved plan or raise."""
        self.calls.append(("import", profile_id, text, mode, expected_revision, confirmed))
        if self.error is not None:
            raise self.error
        return {"revision": 3, "plan": {"objects": []}, "report": {"mode": mode}}

    def preview_copy(self, source_profile_id, profile_id, mode, availability):
        """Record the copy preview, then answer a report or raise (10.7)."""
        self.calls.append(("preview_copy", source_profile_id, profile_id, mode, availability is not None))
        if self.error is not None:
            raise self.error
        return {"mode": mode, "destination_revision": 5}

    def copy_plan(self, source_profile_id, profile_id, mode, expected_revision, confirmed, availability):
        """Record the copy with its confirmation, then answer with the saved plan or raise (10.7)."""
        self.calls.append(("copy", source_profile_id, profile_id, mode, expected_revision, confirmed))
        if self.error is not None:
            raise self.error
        return {"revision": 6, "plan": {"objects": []}, "report": {"mode": mode}}


def call(transfer: FakeImport, method: str, parameters: dict) -> dict:
    """Dispatch one import call through a bridge facade with only the transfer handlers."""
    coordinator = MissionMapTransferCoordinator(transfer, None)
    # The destination read is the subject of test_mission_map_transfer_availability; here it is empty
    coordinator.availability = lambda _profile_id: (lambda _item: None)  # type: ignore[method-assign]
    bridge = BridgeFacade(coordinator.handlers())
    return bridge.dispatch({"contract_version": 1, "request_id": "t", "method": method, "parameters": parameters})


IMPORT_PARAMETERS = {"profile_id": "alpha", "text": "{}", "mode": "merge", "expected_revision": 2, "confirmed": True}


class ImportHandlerTests(unittest.TestCase):
    """The import preview and the import: exact parameters, the mode, the confirmation and the refusals (D16)."""

    def test_preview_and_import_answers(self) -> None:
        """The import answer has the revision and the report and never the plan; only true confirms."""
        transfer = FakeImport()
        preview = call(transfer, "preview_mission_map_import", {"profile_id": "alpha", "text": "{}", "mode": "merge"})
        self.assertEqual(preview["value"], {"mode": "merge", "destination_revision": 2})
        saved = call(transfer, "import_mission_map_plan", IMPORT_PARAMETERS)
        self.assertEqual(saved["value"], {"revision": 3, "report": {"mode": "merge"}})
        call(transfer, "import_mission_map_plan", {**IMPORT_PARAMETERS, "confirmed": "yes"})
        self.assertEqual([entry[-1] for entry in transfer.calls[1:]], [True, False])

    def test_mode_text_and_parameters_are_checked_before_the_service(self) -> None:
        """Another mode is `invalid_mode`, a text that is no string `invalid_document`; nothing reaches the service."""
        transfer = FakeImport()
        cases = (({**IMPORT_PARAMETERS, "mode": "sideways"}, "invalid_mode", "Choose --mode replace or --mode merge."),
                 ({**IMPORT_PARAMETERS, "text": 7}, "invalid_document", "The file is not a valid map plan."),
                 ({**IMPORT_PARAMETERS, "path": "C:/x.json"}, "invalid_document",
                  "The map editor parameters are invalid."))
        for parameters, reason, message in cases:
            with self.subTest(reason=reason, parameters=sorted(parameters)):
                error = call(transfer, "import_mission_map_plan", parameters)["error"]
                self.assertEqual((error["code"], error["details"]["reason"], error["message"]),
                                 ("INVALID_REQUEST", reason, message))
        self.assertEqual(transfer.calls, [])

    def test_service_refusals_get_their_reasons(self) -> None:
        """A missing confirmation keeps the report; a D1 failure lists each problem; a stale save is stale_revision."""
        report = {"confirmation_required": True}
        error = call(FakeImport(ConfirmationRequired(report)), "import_mission_map_plan", IMPORT_PARAMETERS)["error"]
        self.assertEqual((error["code"], error["details"], error["message"]),
                         ("INVALID_REQUEST", {"reason": "confirmation_required", "report": report},
                          "This import has items that DayZ-ServerMan does not import, or it needs a placement review. "
                          "Nothing was changed. Check the list above, then add --confirm-listed."))
        problem = PlanValidationError((PlanProblem("objects must be a list", 0, "c1"),))
        error = call(FakeImport(problem), "preview_mission_map_import",
                     {"profile_id": "alpha", "text": "{}", "mode": "replace"})["error"]
        self.assertEqual((error["details"]["reason"], error["message"], error["details"]["problems"]),
                         ("invalid_document", "The file is not a valid map plan.",
                          [{"object_id": "c1", "rule": "invalid_document", "message": "objects must be a list"}]))
        error = call(FakeImport(RevisionConflict("changed")), "import_mission_map_plan", IMPORT_PARAMETERS)["error"]
        self.assertEqual((error["code"], error["details"]["reason"]), ("REVISION_CONFLICT", "stale_revision"))
        refused = TransferRefused("dev", "mission_path_changed", "destination")
        error = call(FakeImport(refused), "import_mission_map_plan", IMPORT_PARAMETERS)["error"]
        self.assertEqual((error["code"], error["details"]["reason"]),
                         ("PROFILE_CONTEXT_MISMATCH", "mission_path_changed"))


COPY_PARAMETERS = {"profile_id": "alpha", "source_profile_id": "bravo", "mode": "replace", "expected_revision": 5,
                   "confirmed": True}


class CopyHandlerTests(unittest.TestCase):
    """The copy preview and the copy (10.7): the source, the destination check, the copy texts of the refusals."""

    def test_preview_and_copy_answers(self) -> None:
        """The source and the destination reach the service with the destination check; the plan is not echoed."""
        transfer = FakeImport()
        preview = call(transfer, "preview_mission_map_copy", {"profile_id": "alpha", "source_profile_id": "bravo",
                                                              "mode": "replace"})
        self.assertEqual(preview["value"], {"mode": "replace", "destination_revision": 5})
        saved = call(transfer, "copy_mission_map_plan", COPY_PARAMETERS)
        self.assertEqual(saved["value"], {"revision": 6, "report": {"mode": "replace"}})
        call(transfer, "copy_mission_map_plan", {**COPY_PARAMETERS, "confirmed": 1})
        self.assertEqual(transfer.calls, [("preview_copy", "bravo", "alpha", "replace", True),
                                          ("copy", "bravo", "alpha", "replace", 5, True),
                                          ("copy", "bravo", "alpha", "replace", 5, False)])

    def test_refusals_of_copy(self) -> None:
        """Mode, source type, the copy confirmation text and the source reasons of D16."""
        transfer = FakeImport()
        for parameters, reason in (({**COPY_PARAMETERS, "mode": "sideways"}, "invalid_mode"),
                                   ({**COPY_PARAMETERS, "source_profile_id": 7}, "invalid_document")):
            with self.subTest(reason=reason):
                error = call(transfer, "copy_mission_map_plan", parameters)["error"]
                self.assertEqual((error["code"], error["details"]["reason"]), ("INVALID_REQUEST", reason))
        self.assertEqual(transfer.calls, [])
        error = call(FakeImport(ConfirmationRequired({})), "copy_mission_map_plan", COPY_PARAMETERS)["error"]
        self.assertEqual(error["message"], "This copy has items that DayZ-ServerMan does not copy, or it needs a "
                         "placement review. Nothing was changed. Check the list above, then add --confirm-listed.")
        for reason, code, text in (
                ("source_is_destination", "INVALID_REQUEST", "Copy needs another profile as its source. Name it with "
                 "--from-profile."),
                ("no_valid_plan", "INVALID_REQUEST", "The source profile has no valid saved map plan to copy. "
                 "Nothing was changed."),
                ("mission_path_changed", "PROFILE_CONTEXT_MISMATCH", "The mission path of the source profile "
                 "changed, so it has no map plan to copy.")):
            with self.subTest(reason=reason):
                refused = FakeImport(TransferRefused("dev", reason, "source"))
                error = call(refused, "preview_mission_map_copy", {"profile_id": "alpha", "source_profile_id": "bravo",
                                                                   "mode": "merge"})["error"]
                self.assertEqual((error["code"], error["details"]["reason"], error["message"]), (code, reason, text))


class CompositionTests(unittest.TestCase):
    """The export is registered in both compositions, as an observer read, over the composition's service."""

    def test_owner_and_observer_hold_the_export(self) -> None:
        """A real owner composition answers the export of a missing plan with `no_valid_plan`."""
        self.assertIn("export_mission_map_plan", OBSERVER_READ_METHODS)
        with tempfile.TemporaryDirectory(prefix="serverman_transfer_bridge_") as temporary:
            composition = build_composition(Path(temporary) / "Manager")
            try:
                self.assertIn("export_mission_map_plan", composition.bridge.allowed_methods)
                answer = composition.bridge.dispatch({"contract_version": 1, "request_id": "t",
                                                      "method": "export_mission_map_plan",
                                                      "parameters": {"profile_id": "missing"}})
                self.assertFalse(answer["success"], json.dumps(answer))
                self.assertEqual(answer["error"]["code"], "NOT_FOUND")
            finally:
                composition.shutdown.request_shutdown()
                composition.shutdown.wait_for_close(5)


if __name__ == "__main__":
    unittest.main()
