"""8.13 QA-8.13-F4: the second validation under the guard (`MedicalFeatureService._reread`).

A changed profile revision, settings revision or DayZ root under the guard is the stale review. A missing
or unreadable resource and a plain fault keep their own error, and an R17 refusal keeps its text.
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any, Callable

from dayz_serverman.application.medical_feature_coordinator import _error
from dayz_serverman.application.mission_configuration import MissionPathError
from dayz_serverman.application.settings import SettingsValidationError
from dayz_serverman.domain.medical_editor_texts import (
    FINGERPRINT_STALE_TEXT, MISMATCH_TEXT, ZoneReviewStale, ZoneToggleRefused,
)
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.medical_editor_support import ToggleFixture


class RereadFixture(ToggleFixture):
    """An editor-managed mission after an Apply with medical off and a stored loot-zone original."""

    def setUp(self) -> None:
        """Apply a capacity plan and keep the applied bytes."""
        super().setUp()
        self.applied = self.applied_with_medical()

    def settings_revision(self) -> None:
        """Change the settings revision."""
        self.settings.revision += 1

    def profile_revision(self) -> None:
        """Change the revision of the main profile."""
        record = self.profiles.records["main"]
        self.profiles.records["main"] = dataclasses.replace(record, revision=record.revision + 1)

    def root_unset(self) -> None:
        """Remove the DayZ root from the settings; the validation raises a ValueError."""
        self.settings.dayz_root = None

    def inject(self, where: str, action: Callable[[], None]) -> list[bool]:
        """Run action once: at the guard's server-state read (first check), or after the first check."""
        done = [False]
        if where == "guard":
            status = self.ports.status

            def changed() -> Any:
                """Run the action at the first server-state read."""
                if not done[0]:
                    done[0] = True
                    action()
                return status()
            self.ports.status = changed
        else:
            def hook(phase: str, _index: int) -> None:
                """Run the action after the first check."""
                if phase == "AFTER_REVALIDATE" and not done[0]:
                    done[0] = True
                    action()
            self.fault = hook
        return done

    def refusal(self) -> BaseException:
        """Return the error of a reviewed toggle to on."""
        try:
            self.run_toggle(True)
        except Exception as error:
            return error
        self.fail("the toggle did not refuse")

    def armed(self, name: str, error: Exception) -> None:
        """Make one service method raise error once the guard reads the server state."""
        original, armed = getattr(self.medical, name), [False]

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Raise error after the guard took the first check."""
            if armed[0]:
                raise error
            return original(*args, **kwargs)
        setattr(self.medical, name, wrapper)
        self.inject("guard", lambda: armed.__setitem__(0, True))


class MappedTests(RereadFixture):
    """A change of a validated input under the guard is the stale review, at both checks; nothing is written."""

    def test_changes_under_the_guard(self) -> None:
        """Revision changes and an unset DayZ root give ZoneReviewStale, REVISION_CONFLICT and the stale text."""
        cases = (("settings_revision", RevisionConflict), ("profile_revision", RevisionConflict),
                 ("root_unset", SettingsValidationError))
        for where in ("guard", "between"):
            for name, cause in cases:
                with self.subTest(where=where, case=name):
                    self.tearDown(); self.setUp()
                    manager = self.snapshot()[1]
                    done = self.inject(where, getattr(self, name))
                    error = self.refusal()
                    self.assertTrue(done[0])
                    self.assertIs(type(error), ZoneReviewStale)
                    self.assertEqual(str(error), FINGERPRINT_STALE_TEXT)
                    self.assertIsInstance(error.__cause__, cause)
                    call = _error(error)
                    self.assertEqual((call.code.value, call.safe_message, call.retryable),
                                     ("REVISION_CONFLICT", FINGERPRINT_STALE_TEXT, True))
                    self.assertEqual((self.proto(), self.snapshot()[1]), (self.applied, manager))
                    self.assertFalse(list(self.journals.root.glob("*.json")))


class NotRemappedTests(RereadFixture):
    """Errors under the guard that come from a missing or unreadable resource keep their type and code."""

    def check(self, kind: type[BaseException], code: str) -> None:
        """The refusal keeps its type and bridge code, and no journal stays."""
        error = self.refusal()
        self.assertNotIsInstance(error, ZoneReviewStale)
        self.assertIsInstance(error, kind)
        self.assertEqual(_error(error).code.value, code)  # type: ignore[arg-type]
        self.assertEqual(self.proto(), self.applied)
        self.assertFalse(list(self.journals.root.glob("*.json")))

    def test_os_error(self) -> None:
        """A read failure stays an OSError."""
        self.armed("_state", PermissionError("locked"))
        self.check(OSError, "STORAGE_FAILURE")

    def test_profile_not_found(self) -> None:
        """A deleted profile stays ProfileNotFound."""
        def gone() -> None:
            """Make every profile read fail."""
            def read(_profile_id: object) -> Any:
                raise ProfileNotFound("profile was not found")
            self.profiles.read = read  # type: ignore[method-assign]
        self.inject("between", gone)
        self.check(ProfileNotFound, "NOT_FOUND")

    def test_mission_path_error(self) -> None:
        """A mission path refusal stays MissionPathError."""
        self.armed("_context", MissionPathError("mission target crosses a reparse point"))
        self.check(MissionPathError, "NOT_FOUND")

    def test_configuration_file_error(self) -> None:
        """A layout error stays ConfigurationFileError."""
        self.armed("_state", ConfigurationFileError("layout"))
        self.check(ConfigurationFileError, "RECOVERY_REQUIRED")

    def test_runtime_error(self) -> None:
        """A plain fault stays a RuntimeError."""
        self.armed("_state", RuntimeError("fault"))
        self.check(RuntimeError, "STORAGE_FAILURE")

    def test_r17_refusal(self) -> None:
        """A record that no longer matches the files under the guard refuses with the R17 text."""
        def forge() -> None:
            """Drop the "files after" entry of one writable file from the applied record."""
            store = AssociationRecordStore(self.area, "main", self.key(), "applied")
            record, revision = store.read()
            fields = {name: value for name, value in record.items() if name != "manifest_sha256"}
            fields["files_after"] = [item for item in record["files_after"] if item["path"] != "mapgroupdirt.xml"]
            kept = {item["sha256"]: store.contents.read(item["sha256"]) for item in record["files_before"]
                    if item["existed"]}
            store.save(fields, kept, revision)
        self.inject("between", forge)
        error = self.refusal()
        self.assertIs(type(error), ZoneToggleRefused)
        self.assertEqual((error.reason, str(error)), ("files_differ", MISMATCH_TEXT))  # type: ignore[attr-defined]
        self.assertEqual(_error(error).code.value, "INVALID_REQUEST")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
