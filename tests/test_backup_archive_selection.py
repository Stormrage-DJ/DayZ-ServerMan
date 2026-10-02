"""Selected ZIP validation, external paths and preview-to-apply integrity."""
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.test_profile_restore_service as fixtures
from dayz_serverman.host.api import HostApi
from dayz_serverman.repositories.backup_verification import sha256_file
from types import SimpleNamespace


class ArchiveSelectionTests(unittest.TestCase):
    """Exercise selection against real archive and publication fixtures."""
    def setUp(self):
        """Use a renamed archive outside the configured backup folder."""
        self.fixture = fixtures.DirectRestoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.archive = self.fixture.root / "copied backup ő.zip"
        shutil.copyfile(self.fixture.backup_root / (self.fixture.backup_id + ".zip"), self.archive)

    def test_external_renamed_archive_restores_without_importing(self):
        """Restore a selected ZIP with its internal identity and preserve its bytes."""
        fixture = self.fixture
        before = sha256_file(self.archive)
        selected = fixture.service.selected.inspect(str(self.archive))
        fixture.request["backup_id"] = selected["backup_id"]
        parameters = fixture.parameters()
        result = fixture.service.apply(parameters, "external-selection", lambda *_: None)
        self.assertEqual(result["profile"]["profile_id"], "restored")
        self.assertEqual(sha256_file(self.archive), before)
        self.assertEqual(len(list(fixture.backup_root.glob("*.zip"))), 1)

    def test_changed_archive_and_unknown_selection_cannot_apply(self):
        """Recheck selected bytes before publication, even after a valid preview."""
        fixture = self.fixture
        selected = fixture.service.selected.inspect(str(self.archive))
        fixture.request["backup_id"] = selected["backup_id"]
        parameters = fixture.parameters()
        with self.archive.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            fixture.service.apply(parameters, "changed-selection", lambda *_: None)
        self.assertEqual(fixture.profiles.list(), ())
        with self.assertRaisesRegex(ValueError, "expired"):
            fixture.service.selected.path("selected-unknown")

    def test_invalid_paths_and_corrupt_zip_are_rejected(self):
        """Reject relative paths, non-ZIP files and invalid ZIP contents."""
        for value in (None, "relative.zip", str(self.fixture.root / "missing.zip")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.fixture.service.selected.inspect(value)
        self.archive.write_bytes(b"not a zip")
        with self.assertRaises(RuntimeError):
            self.fixture.service.selected.inspect(str(self.archive))

    def test_native_cancel_and_failure_do_not_dispatch(self):
        """Dismissal and unavailable picker never reach an archive bridge handler."""
        bridge = unittest.mock.Mock()
        api = HostApi(bridge)
        self.assertFalse(api.select_backup_archive()["success"])
        api._set_backup_archive_selector(lambda: None)
        self.assertTrue(api.select_backup_archive()["value"]["cancelled"])
        bridge.dispatch.assert_not_called()

    def test_older_archive_rejection_returns_no_session_reference(self):
        """Older metadata remains unsuitable for direct profile reconstruction."""
        with patch("dayz_serverman.repositories.selected_backup.read_archive_manifest", return_value=SimpleNamespace(schema_version=2)), \
             patch("dayz_serverman.repositories.selected_backup.verify_archive"):
            with self.assertRaisesRegex(ValueError, "older backup"):
                self.fixture.service.selected.inspect(str(self.archive))
        self.assertEqual(self.fixture.service.selected.selections, {})

    def test_native_selection_dispatches_only_selected_path(self):
        """The host forwards a native selection through the narrow inspection method."""
        bridge = unittest.mock.Mock()
        bridge.dispatch.return_value = {"success": True, "value": {"backup_id": "selected-fixture"}}
        api = HostApi(bridge)
        api._set_backup_archive_selector(lambda: str(self.archive))
        self.assertTrue(api.select_backup_archive()["success"])
        request = bridge.dispatch.call_args.args[0]
        self.assertEqual(request["method"], "inspect_backup_archive")
        self.assertEqual(request["parameters"], {"path": str(self.archive)})
