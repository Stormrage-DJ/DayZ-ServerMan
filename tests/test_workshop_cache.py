"""Workshop cache verification determinism and fail-closed tests."""
from __future__ import annotations

import tempfile
import unittest
import sys
import os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.repositories.workshop_cache import (
    CacheVerificationError,
    WorkshopCacheVerifier,
)
from dayz_serverman.repositories import workshop_cache


class WorkshopCacheTests(unittest.TestCase):
    """Cache digest, manifest, and fail-closed verification contracts."""
    def setUp(self) -> None:
        """Create a temporary workshop item with its manifest record."""
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "steamapps" / "workshop" / "content" / "221100"
        self.item = self.root / "111"
        self.item.mkdir(parents=True)
        (self.item / "Addons").mkdir()
        (self.item / "Addons" / "mód.pbo").write_bytes(b"content")
        self.manifest = self.base / "steamapps" / "workshop" / "appworkshop_221100.acf"
        self.manifest.write_text(
            '"AppWorkshop" { "appid" "221100" "NeedsUpdate" "0" '
            '"NeedsDownload" "0" "WorkshopItemsInstalled" { "111" { '
            '"manifest" "9" "size" "7" "timeupdated" "1" } } }', encoding="utf-8")

    def tearDown(self) -> None:
        """Remove the temporary workshop tree."""
        self.temporary.cleanup()

    def test_verification_is_deterministic_and_detects_content_change(self) -> None:
        """Repeated verification is stable and content changes are detected."""
        # Compare two verifications of the unchanged item
        verifier = WorkshopCacheVerifier(self.root)
        first = verifier.verify("111")
        second = verifier.verify("111")
        self.assertEqual(first.manifest_record_digest, second.manifest_record_digest)
        self.assertEqual(first.content_inventory_digest, second.content_inventory_digest)
        self.assertEqual(first.regular_file_count, 1)
        (self.item / "Addons" / "mód.pbo").write_bytes(b"changed")
        # Confirm the content digest follows the mutated bytes
        changed = verifier.verify("111")
        self.assertNotEqual(first.content_inventory_digest, changed.content_inventory_digest)
        self.assertEqual(first.manifest_record_digest, changed.manifest_record_digest)

    def test_missing_and_ambiguous_manifest_records_fail_closed(self) -> None:
        """A missing manifest record fails closed."""
        # Empty the manifest so the item record is missing
        verifier = WorkshopCacheVerifier(self.root)
        self.manifest.write_text('"AppWorkshop" {}', encoding="utf-8")
        with self.assertRaises(CacheVerificationError):
            verifier.verify("111")

    def test_verification_inventories_once_and_manifest_drift_fails_closed(self) -> None:
        """Content is inventoried once and manifest drift fails closed."""
        verifier = WorkshopCacheVerifier(self.root)
        # Confirm a single verification inventories the content once
        with patch.object(verifier, "_inventory", wraps=verifier._inventory) as inventory:
            verifier.verify("111")
            inventory.assert_called_once()
        # Confirm manifest drift between reads fails closed
        with patch.object(verifier, "_manifest_record", side_effect=(b"a", b"b")):
            with self.assertRaises(CacheVerificationError):
                verifier.verify("111")
        # Confirm a duplicated item record also fails closed
        self.manifest.write_text('"111" { } "111" { }', encoding="utf-8")
        with self.assertRaises(CacheVerificationError):
            verifier.verify("111")

    def test_observe_reports_missing_and_manifest_versions_in_request_order(self) -> None:
        """Observe reports missing and installed items in the requested order."""
        self.manifest.write_text(
            '"AppWorkshop" { "appid" "221100" "NeedsUpdate" "0" '
            '"NeedsDownload" "0" "WorkshopItemsInstalled" { "111" { '
            '"manifest" "9" "size" "7" "timeupdated" "1" } } '
            '"WorkshopItemDetails" { "111" { "latest_manifest" "10" '
            '"latest_timeupdated" "2" } } }', encoding="utf-8")
        missing, installed = WorkshopCacheVerifier(self.root).observe(("222", "111"))
        self.assertFalse(missing.installed)
        self.assertEqual(installed.installed_manifest_id, "9")
        self.assertEqual(installed.latest_manifest_id, "10")
        self.assertEqual(installed.installed_time_updated, 1)

    def test_link_inside_item_is_rejected_when_supported(self) -> None:
        """A link inside an item is rejected when links are available."""
        # Plant a symbolic link that points outside the item
        link = self.item / "escape"
        try:
            link.symlink_to(self.base, target_is_directory=True)
        except OSError:
            self.skipTest("symbolic links are not available")
        with self.assertRaises(CacheVerificationError):
            WorkshopCacheVerifier(self.root).verify("111")

    def test_incomplete_manifest_and_empty_item_fail_closed(self) -> None:
        """An incomplete manifest and an empty item fail closed."""
        self.manifest.write_text(
            '"AppWorkshop" { "appid" "221100" "NeedsUpdate" "0" '
            '"NeedsDownload" "0" "WorkshopItemsInstalled" { '
            '"111" { "manifest" "9" } } }', encoding="utf-8")
        with self.assertRaises(CacheVerificationError):
            WorkshopCacheVerifier(self.root).verify("111")
        # Restore the complete manifest, then empty the item content
        self.setUp_manifest()
        (self.item / "Addons" / "mód.pbo").unlink()
        with self.assertRaises(CacheVerificationError):
            WorkshopCacheVerifier(self.root).verify("111")

    def test_ads_detection_is_enforced_and_real_ads_when_supported(self) -> None:
        """Alternate stream detection is enforced where the platform supports it."""
        target = self.item / "Addons" / "mód.pbo"
        original = workshop_cache._has_alternate_stream
        # Force stream detection on the content file and confirm rejection
        with patch.object(workshop_cache, "_has_alternate_stream",
                          side_effect=lambda path: path.name == target.name):
            with self.assertRaises(CacheVerificationError):
                WorkshopCacheVerifier(self.root).verify("111")
        # When NTFS is available, plant a real stream and confirm detection
        if os.name == "nt":
            try:
                Path(f"{target}:qa-stream").write_bytes(b"hidden")
            except OSError:
                self.skipTest("NTFS alternate streams are unavailable")
            self.assertTrue(original(target))
            with self.assertRaises(CacheVerificationError):
                WorkshopCacheVerifier(self.root).verify("111")

    def setUp_manifest(self) -> None:
        """Write the baseline workshop manifest for the fixture item."""
        self.manifest.write_text(
            '"AppWorkshop" { "appid" "221100" "NeedsUpdate" "0" '
            '"NeedsDownload" "0" "WorkshopItemsInstalled" { "111" { '
            '"manifest" "9" "size" "7" "timeupdated" "1" } } }', encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
