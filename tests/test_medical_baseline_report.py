"""Leaf 6.5: the loot-zone baseline classification reaches `load_medical_features` and its wording.

The wording rows are the same in the Python and the frontend catalogue. A load answer gets the additive
`zone_baseline` field. A load error from the zone refusal keeps its code and message. Its details get the
classification. Nothing is written.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dayz_serverman.application import activity_wording as wording
from dayz_serverman.application.medical_feature_coordinator import MedicalFeatureCoordinator
from dayz_serverman.application.medical_features import EDITOR_MANAGED
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.repositories.atomic_file import ContentChangedError
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.medical_features import legacy_zone_transform, project_zones
from dayz_serverman.repositories.medical_zone_classification import classify_zone_baseline
from dayz_serverman.repositories.medical_zone_fragments import (
    ADDED, DIFFERS, MISSING, has_zone_changes, patch_zones, zones_enabled,
)
from dayz_serverman.repositories.medical_zone_scan import LAYOUT, MIXED_LINES
from tests.test_medical_features import ZONES, _zone_service
from tests.test_medical_zone_classification import ADDED_GROUP, EMPTY, ZONES_ON, _place, _snapshot
from tests.ui_harness_support import FRONTEND

# The stored original of the fixture mission in the legacy folder, as the load answer names it
LEGACY_SHOWN = str(Path("mpmissions/dayzOffline.test/.dayz_manager_backups/mapgroupproto.xml"))
# The way out that the Product Owner decided on 2026-10-13 15:19:49, word for word
PO_WAY_OUT = ("DayZ-ServerMan does not guess the original loot of these groups. If mapgroupproto.xml changed on "
              "purpose, for example after a game or mod update or by another tool, first make sure that its medical "
              "loot groups have the loot that you want when medical loot zones are off. Then move the stored "
              "original to another folder. The current file then becomes the original, which is the Off state. "
              "Otherwise, put back the mapgroupproto.xml that matches the stored original.")
# The sentence of a mixed state that the Product Owner decided on 2026-10-13 16:11:06, word for word
PO_MIXED = ("mapgroupproto.xml differs from its stored original and from the form that DayZ-ServerMan writes when "
            "medical loot zones are on. No file was changed.")
# A stored original with an undeclared namespace prefix: expat scans it, the whole-file parse refuses it (QA-6.5-1)
NS_BASE = ZONES.replace(b"</prototype>", b'    <group name="House"><container name="d"><ca:egory name="food"/>'
                                         b"</container></group>\n</prototype>")
# The fixture prototype with an editor-generated group, so the map editor manages the mission
EDITOR_GROUP = ZONES.replace(b"</prototype>", b'    <group name="ServerManMap_G_1"><usage name="Town"/></group>\n'
                                              b"</prototype>")


def frontend_rows() -> list[list[str]]:
    """Return the loot-zone refusal rows of `operation_messages.js`, with its way-out constant filled in."""
    source = (FRONTEND / "operation_messages.js").read_text(encoding="utf-8")
    way_out = source.split("const OPERATION_ZONE_ORIGINAL_WAY_OUT = ", 1)[1].split(";\n", 1)[0]
    way_out = "".join(json.loads(part.strip()) for part in way_out.split("\n  + "))
    body = source.split("const operationZoneRefusalTexts = Object.freeze([", 1)[1].split("]);", 1)[0]
    return json.loads("[" + body.replace("OPERATION_ZONE_ORIGINAL_WAY_OUT", json.dumps(way_out)) + "]")


class ZoneRefusalWordingTests(unittest.TestCase):
    """The rows, their openings and the admin text of each unmappable reason."""

    def test_python_and_frontend_rows_agree(self) -> None:
        """Both catalogues hold the same openings, sentences and ways out in the same order."""
        self.assertEqual([tuple(row) for row in frontend_rows()], list(wording.ZONE_REFUSAL_TEXTS))
        self.assertIn("zoneBaseline: zoneBaselineText,", (FRONTEND / "operation_messages.js").read_text("utf-8"))

    def test_the_decided_strings(self) -> None:
        """The way out, the layout text and the editor-managed text are the decided strings."""
        rows = wording.ZONE_REFUSAL_TEXTS
        self.assertEqual(wording.ZONE_ORIGINAL_WAY_OUT, PO_WAY_OUT)
        self.assertEqual([row[2] for row in rows], [PO_WAY_OUT, PO_WAY_OUT,
                                                    "Correct this in mapgroupproto.xml, then try again.", ""])
        self.assertEqual(rows[3][1], "Medical loot zones cannot change because the map editor manages this mission.")
        self.assertEqual(rows[1][1], PO_MIXED)

    def test_each_row_opens_a_reason_of_the_toggle(self) -> None:
        """The openings are the 6.3 refusals of the toggle that 6.4 gives as reasons."""
        openings = [opening for opening, _sentence, _way_out in wording.ZONE_REFUSAL_TEXTS]
        reasons = (ADDED.format(group="G"), MISSING.format(group="G"), DIFFERS, LAYOUT.format(cause=MIXED_LINES),
                   EDITOR_MANAGED)
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertEqual(sum(reason.startswith(opening) for opening in openings), 1)

    def test_operator_texts_hold_no_identifier(self) -> None:
        """The sentences and ways out are operator wording."""
        for _opening, sentence, way_out in wording.ZONE_REFUSAL_TEXTS:
            for text in filter(None, (sentence, way_out)):
                with self.subTest(text=text):
                    self.assertFalse(wording.leaks_identifier(text))

    def test_text_of_each_reason(self) -> None:
        """An unmappable reason gives its text and, for a mismatch, the stored original; otherwise None."""
        way_out = wording.ZONE_ORIGINAL_WAY_OUT
        added = ADDED.format(group="PlannerRich_00534")
        layout = LAYOUT.format(cause=MIXED_LINES)
        cases = (
            ({"mappable": False, "reason": added, "stored_original": "x.xml"}, (f"{added} {way_out}", "x.xml")),
            ({"mappable": False, "reason": DIFFERS, "stored_original": "x.xml"},
             (f"{wording.ZONE_REFUSAL_TEXTS[1][1]} {way_out}", "x.xml")),
            ({"mappable": False, "reason": layout, "stored_original": "x.xml"},
             (f"{layout} Correct this in mapgroupproto.xml, then try again.", None)),
            ({"mappable": False, "reason": EDITOR_MANAGED, "stored_original": "x.xml"},
             ("Medical loot zones cannot change because the map editor manages this mission.", None)),
            ({"mappable": True, "reason": None, "stored_original": "x.xml"}, None),
            ({"mappable": False, "reason": "malformed XML", "stored_original": "x.xml"}, None),
            (None, None),
        )
        for baseline, expected in cases:
            with self.subTest(baseline=baseline):
                self.assertEqual(wording.zone_baseline_text(baseline), expected)


def two_scan_class(current: bytes, baseline: bytes) -> tuple[bool, bool | None, str | None]:
    """Return the 6.4 classification as it was: state reading, then the projection of the next toggle."""
    try:
        enabled = zones_enabled(current, baseline)
        if has_zone_changes(current, baseline):
            project_zones(current, baseline, not enabled)
    except (ContentChangedError, ConfigurationFileError) as error:
        return False, None, str(error)
    return True, enabled, None


class OneScanClassificationTests(unittest.TestCase):
    """The single-scan reading gives the results of the earlier state reading and projection."""

    def test_results_equal_the_two_scan_classification(self) -> None:
        """Mappable, unmappable, layout, legacy whole-file and unreadable files classify as before."""
        comment = ZONES.replace(b'<usage name="Medic"/>\n', b'<usage name="Medic"/><!-- x -->\n')
        currents = (ZONES, ZONES_ON, ADDED_GROUP, EMPTY, ZONES.replace(b"Town", b"Village"),
                    ZONES_ON.replace(b"?>\n", b"?>\r\n", 1), ZONES.replace(b"?>\n", b"?>\r\n", 1),
                    legacy_zone_transform(ZONES), comment, b"\xff\xfe<", b"<broken", EDITOR_GROUP)
        for current in currents:
            for baseline in (ZONES, ZONES.replace(b"\n", b"\r\n"), EMPTY):
                with self.subTest(current=current[:60], baseline=baseline[:20]):
                    result = classify_zone_baseline(current, baseline, "legacy")
                    self.assertEqual((result.mappable, result.enabled, result.reason),
                                     two_scan_class(current, baseline))
        self.assertEqual(patch_zones(ZONES, ZONES, True), ZONES_ON)
        # QA-6.5-1, corpus pair 1074: the off toggle refuses in its whole-file step, in either declaration form
        ns_on = patch_zones(NS_BASE, NS_BASE, True)
        on_reason = "medical feature target is malformed XML"
        for current, reason in ((ns_on, on_reason), (NS_BASE, None),
                                (ns_on.replace(b'<?xml version="1.0"?>', b"<?xml version='1.0' encoding='utf-8'?>"),
                                 on_reason)):
            with self.subTest(current=current[:40]):
                result = classify_zone_baseline(current, NS_BASE, "legacy")
                self.assertEqual((result.mappable, result.enabled, result.reason), two_scan_class(current, NS_BASE))
                # Only the off toggle has the whole-file step, so an off file stays mappable
                self.assertEqual(result.reason, reason)
        # QA-6.5-7: without a changing group the toggle succeeds with no change and runs no whole-file step
        quiet = b'<prototype><group name="House"><container name="d"><ca:egory name="food"/></container></group></prototype>'
        result = classify_zone_baseline(quiet, quiet, "legacy")
        self.assertEqual((result.mappable, result.enabled, result.reason), two_scan_class(quiet, quiet))
        self.assertEqual((result.mappable, result.enabled), (True, True))


class ZoneBaselineLoadTests(unittest.TestCase):
    """The load answer and the load error of `load_medical_features`."""

    def setUp(self) -> None:
        """Create a temporary root for one fixture mission."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_medical_report_")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        """Remove the temporary root."""
        self.temporary.cleanup()

    def load(self, current: bytes, location: str | None, folder: str = "first") -> dict:
        """Load through the coordinator with a stored original at `location`, and prove that nothing is written."""
        root = self.root / folder
        service, _target = _zone_service(root, current)
        if location is not None:
            _place(root, location, ZONES)
        before = _snapshot(root)
        try:
            return MedicalFeatureCoordinator(service, None).load({"profile_id": "main"})  # type: ignore[arg-type]
        finally:
            self.assertEqual(_snapshot(root), before)

    def test_answer_names_the_classification_and_the_stored_original(self) -> None:
        """A mappable stored original is named; without one the field is None; item spawns have no field."""
        features = self.load(ZONES, "legacy")["features"]
        self.assertEqual(features["medical_loot_zones"]["zone_baseline"],
                         {"mappable": True, "reason": None, "location": "legacy", "stored_original": LEGACY_SHOWN})
        self.assertNotIn("zone_baseline", features["medical_item_spawns"])
        manager = self.load(ZONES, "manager", "second")["features"]["medical_loot_zones"]["zone_baseline"]
        stored = self.root / "second" / "baselines" / "main" / "medical_loot_zones.original.xml"
        self.assertEqual((manager["location"], manager["stored_original"]), ("manager", str(stored)))

    def test_no_stored_original_gives_none(self) -> None:
        """Without a stored original the current file is the original, so there is nothing to classify."""
        self.assertIsNone(self.load(ZONES, None)["features"]["medical_loot_zones"]["zone_baseline"])

    def test_editor_managed_answer_keeps_the_state_and_names_the_refusal(self) -> None:
        """The read state stays; the classification gives the toggle's editor-managed refusal."""
        zones = self.load(EDITOR_GROUP, "legacy")["features"]["medical_loot_zones"]
        self.assertEqual((zones["enabled"], zones["zone_baseline"]["mappable"], zones["zone_baseline"]["reason"]),
                         (False, False, EDITOR_MANAGED))

    def test_zone_refusal_keeps_its_error_and_adds_the_classification(self) -> None:
        """The code, message and retry flag stay; the details carry the unmappable classification."""
        reason = ADDED.format(group="PlannerRich_00534")
        with self.assertRaises(ApplicationCallError) as caught:
            self.load(ADDED_GROUP, "legacy")
        error = caught.exception
        self.assertEqual((error.code, error.safe_message, error.retryable), (ErrorCode.REVISION_CONFLICT, reason, True))
        self.assertEqual(error.details, {"zone_baseline": {"mappable": False, "reason": reason, "location": "legacy",
                                                           "stored_original": LEGACY_SHOWN}})
        # The service read itself still refuses as before 6.5
        service, _target = _zone_service(self.root / "again", ADDED_GROUP)
        _place(self.root / "again", "legacy", ZONES)
        with self.assertRaisesRegex(ContentChangedError, "PlannerRich_00534"):
            service.load("main")

    def test_an_item_spawn_error_gets_no_zone_details(self) -> None:
        """The same "differs" sentence from the item-spawn file is not the zone refusal."""
        service, _target = _zone_service(self.root, ZONES)
        _place(self.root, "legacy", ZONES)
        stored = self.root / "baselines" / "main" / "medical_item_spawns.original.xml"
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(b"<types><type name='Other'/></types>")
        with self.assertRaises(ApplicationCallError) as caught:
            MedicalFeatureCoordinator(service, None).load({"profile_id": "main"})  # type: ignore[arg-type]
        self.assertEqual((caught.exception.code, caught.exception.safe_message, caught.exception.details),
                         (ErrorCode.REVISION_CONFLICT, DIFFERS, None))


if __name__ == "__main__":
    unittest.main()
