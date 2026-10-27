"""Leaf 6.5 QA cases: which load errors carry the classification, the whole-file step, and unscannable files.

Each case names the QA finding or mutant that it covers (QA entry 2026-10-13 16:08:12 in the 6.5 evidence).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dayz_serverman.application.activity_wording import zone_baseline_text
from dayz_serverman.application.medical_feature_coordinator import MedicalFeatureCoordinator
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.medical_features import legacy_zone_transform, project_zones
from dayz_serverman.repositories.medical_zone_classification import classify_zone_baseline
from dayz_serverman.repositories.medical_zone_fragments import ADDED
from dayz_serverman.repositories.medical_zone_scan import LAYOUT, NOT_UTF8, TEXT_RUN, scan_groups
from dayz_serverman.repositories.mission_map_layout import TargetClass, target_key
from tests.test_medical_features import ZONES, _zone_service
from tests.test_medical_zone_classification import ADDED_GROUP, _place, _snapshot

# A prototype whose XML declaration names an encoding that does not exist (QA-6.5-4)
# The admin text of that file, which the Product Owner accepted at 23:22:28: file, cause, "No file was changed."
ENCODING_TEXT = ("mapgroupproto.xml has a layout that this setting cannot change safely: the file declares an unknown "
                 "encoding. No file was changed. Correct this in mapgroupproto.xml, then try again.")
UNKNOWN_ENCODING = ZONES.replace(b'<?xml version="1.0"?>', b'<?xml version="1.0" encoding="rtf-8"?>')


class LoadErrorDetailTests(unittest.TestCase):
    """The load error keeps its code and message; only the zone refusal itself carries the classification."""

    def setUp(self) -> None:
        """Create a temporary root for one fixture mission."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_medical_cases_")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        """Remove the temporary root."""
        self.temporary.cleanup()

    def load_error(self, current: bytes, *, record: bool = False) -> ApplicationCallError:
        """Load a mission with a legacy stored original, optionally with an editor record; return the error."""
        service, target = _zone_service(self.root, current)
        _place(self.root, "legacy", ZONES)
        if record:
            key = target_key(TargetClass.MISSION, target.parent.resolve(strict=True))
            folder = self.root / "mission-map" / "profiles" / "other" / key
            folder.mkdir(parents=True); (folder / "applied.json").write_text("{}", encoding="utf-8")
        before = _snapshot(self.root)
        with self.assertRaises(ApplicationCallError) as caught:
            MedicalFeatureCoordinator(service, None).load({"profile_id": "main"})  # type: ignore[arg-type]
        self.assertEqual(_snapshot(self.root), before)
        self.service = service
        return caught.exception

    def test_an_editor_managed_refusal_carries_no_details(self) -> None:
        """M01: on an editor-managed mission the load error is not the classification reason, so no details."""
        current = ADDED_GROUP.replace(b"</prototype>", b'<group name="ServerManMap_G_1"/></prototype>')
        error = self.load_error(current)
        self.assertEqual((error.code, error.safe_message, error.details),
                         (ErrorCode.REVISION_CONFLICT, ADDED.format(group="PlannerRich_00534"), None))

    def test_an_unscannable_file_keeps_recovery_required_and_its_scan_error(self) -> None:
        """M03 and M15: a UTF-16 file with an editor record keeps its class and gives the scan error as reason."""
        reason = LAYOUT.format(cause=NOT_UTF8)
        error = self.load_error("<prototype/>".encode("utf-16"), record=True)
        self.assertEqual((error.code, error.safe_message, error.retryable), (ErrorCode.RECOVERY_REQUIRED, reason, False))
        self.assertEqual((error.details["zone_baseline"]["mappable"], error.details["zone_baseline"]["reason"]),
                         (False, reason))
        self.assertEqual(self.service.classify_zone_baseline("main").reason, reason)

    def test_an_unknown_encoding_names_the_file_and_the_cause(self) -> None:
        """QA-6.5-4 and QA-6.5-6: an unknown declared encoding is a file refusal with its cause, not a storage failure."""
        reason = ENCODING_TEXT.split(" Correct this", 1)[0]
        with self.assertRaises(ConfigurationFileError) as caught:
            scan_groups(UNKNOWN_ENCODING)
        self.assertEqual(str(caught.exception), reason)
        self.assertEqual(classify_zone_baseline(UNKNOWN_ENCODING, ZONES, "legacy").reason, reason)
        error = self.load_error(UNKNOWN_ENCODING)
        self.assertEqual((error.code, error.safe_message), (ErrorCode.RECOVERY_REQUIRED, reason))
        self.assertEqual(zone_baseline_text(error.details["zone_baseline"]), (ENCODING_TEXT, None))


class WholeFileStepTests(unittest.TestCase):
    """M11: the off toggle of the earlier whole-file form puts back the stored original without a patch."""

    def test_whole_file_form_is_mappable_where_a_patch_would_refuse(self) -> None:
        """A comment in the usage run refuses a patch, but the whole-file form returns to the original bytes."""
        baseline = ZONES.replace(b'<usage name="Medic"/>\n', b'<usage name="Medic"/><!-- c -->\n')
        current = legacy_zone_transform(baseline)
        result = classify_zone_baseline(current, baseline, "legacy")
        self.assertEqual((result.mappable, result.enabled, result.reason), (True, True, None))
        self.assertEqual(project_zones(current, baseline, False), baseline)
        # Without the whole-file step the patch of the same file refuses with the layout cause
        patched = current.replace(b"<?xml version='1.0' encoding='utf-8'?>", b'<?xml version="1.0"?>')
        result = classify_zone_baseline(patched, baseline, "legacy")
        self.assertEqual(result.reason, LAYOUT.format(cause=TEXT_RUN.format(group="Hospital_Main")))


if __name__ == "__main__":
    unittest.main()
