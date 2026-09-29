"""Foundation tests for legacy inventory inspection and argument conversion."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.domain.legacy_arguments import (  # noqa: E402
    convert_legacy_arguments,
    tokenize_windows_arguments,
)
from dayz_serverman.repositories.legacy_source import (  # noqa: E402
    LegacySourceError,
    inspect_legacy_root,
)


class MigrationFoundationTests(unittest.TestCase):
    """Determinism, sanitization, and fail-closed contracts of legacy inventory."""
    def setUp(self) -> None:
        """Create the manager data tree and the legacy profile folder."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_migration_foundation_")
        self.root = Path(self.temporary.name)
        self.manager = self.root / "DayZ-ServerMan"
        self.manager_data = self.manager / "data"
        self.manager_data.mkdir(parents=True)
        self.legacy = self.root / "Légacy DayZ Root"
        (self.legacy / "dayz_server_manager").mkdir(parents=True)
        self.profiles = self.legacy / "dayz_server_manager-profiles"
        self.profiles.mkdir()

    def tearDown(self) -> None:
        """Remove the temporary migration layout."""
        self.temporary.cleanup()

    def write_profile(self, text: str, name: str = "main.json") -> Path:
        """Write a legacy profile document and return its path."""
        path = self.profiles / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_inventory_is_deterministic_sanitized_and_backup_policy_pending(self) -> None:
        """Inspection is deterministic, sanitized, and leaves legacy backups pending."""
        profile = self.write_profile(
            '{"name":"Main","config":"serverDZ.cfg","port":"2302","args":"-doLogs"}'
        )
        state = self.legacy / "dayz_server_manager-state.json"
        state.write_text('{"last_profile":"Main"}', encoding="utf-8")
        backups = self.legacy / "dayz_server_manager-backups" / "Main"
        backups.mkdir(parents=True)
        (backups / "one.zip").write_bytes(b"one")
        (backups / "two.zip").write_bytes(b"two-two")
        (self.legacy / "ignored.txt").write_text("ignored", encoding="utf-8")
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (profile, state)}

        # Two inspections must agree and leave the source untouched
        first = inspect_legacy_root(str(self.legacy), self.manager, self.manager_data)
        second = inspect_legacy_root(str(self.legacy), self.manager, self.manager_data)

        self.assertEqual(first.source_digest, second.source_digest)
        self.assertEqual(
            [item.role for item in first.files],
            ["PROFILE", "MANAGER_STATE", "LEGACY_BACKUP", "LEGACY_BACKUP"],
        )
        self.assertTrue(all(not Path(item.relative_path).is_absolute() for item in first.files))
        self.assertNotIn(str(self.legacy), str([item.to_dict() for item in first.files]))
        self.assertEqual((first.backup_count, first.backup_size), (2, 10))
        self.assertEqual(first.ignored, ("ignored.txt",))
        for path, evidence in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), evidence)

    def test_inventory_digest_changes_for_source_or_backup_inventory_change(self) -> None:
        """The inventory digest changes whenever source or backup content changes."""
        profile = self.write_profile('{"name":"Main"}')
        first = inspect_legacy_root(str(self.legacy), self.manager, self.manager_data)
        profile.write_text('{"name":"Changed"}', encoding="utf-8")
        second = inspect_legacy_root(str(self.legacy), self.manager, self.manager_data)
        self.assertNotEqual(first.source_digest, second.source_digest)
        # A new backup archive must change the digest as well
        backups = self.legacy / "dayz_server_manager-backups"
        backups.mkdir()
        (backups / "later.zip").write_bytes(b"later")
        third = inspect_legacy_root(str(self.legacy), self.manager, self.manager_data)
        self.assertNotEqual(second.source_digest, third.source_digest)

    def test_invalid_roots_duplicate_json_and_links_fail_closed(self) -> None:
        """Invalid roots, duplicate JSON keys, and links all fail closed."""
        # Duplicate keys and invalid roots must be rejected
        self.write_profile('{"name":"Main","name":"Duplicate"}')
        with self.assertRaises(LegacySourceError):
            inspect_legacy_root(str(self.legacy), self.manager, self.manager_data)
        with self.assertRaises(LegacySourceError):
            inspect_legacy_root(str(self.manager), self.manager, self.manager_data)
        with self.assertRaises(LegacySourceError):
            inspect_legacy_root(r"\\server\share", self.manager, self.manager_data)

        # A symlinked profile must be rejected as an unsafe link
        self.profiles.joinpath("main.json").unlink()
        external = self.root / "external.json"
        external.write_text('{"name":"External"}', encoding="utf-8")
        link = self.profiles / "linked.json"
        try:
            link.symlink_to(external)
        except OSError:
            self.skipTest("file symlink creation is unavailable")
        with self.assertRaises(LegacySourceError):
            inspect_legacy_root(str(self.legacy), self.manager, self.manager_data)

    def test_windows_argument_conversion_preserves_safe_unknown_order(self) -> None:
        """Conversion keeps unknown argument order and reports equal-field warnings."""
        converted = convert_legacy_arguments(
            '-port=2302 -doLogs -adminLog -config="server path\\serverDZ.cfg"',
            {"game_port": 2302, "config_path": r"server path\serverDZ.cfg"},
        )
        self.assertEqual(converted.extra_arguments, ("-doLogs", "-adminLog"))
        self.assertEqual(converted.conflicts, ())
        self.assertEqual(len(converted.warnings), 2)

    def test_unsafe_duplicate_and_conflicting_arguments_are_sanitized(self) -> None:
        """Unsafe, duplicate, and conflicting arguments are sanitized in conflicts."""
        unsafe = convert_legacy_arguments('-port=2302 & password=SECRET', {})
        self.assertEqual(unsafe.conflicts[0]["code"], "UNSAFE_ARGUMENTS")
        self.assertNotIn("SECRET", str(unsafe.conflicts))
        duplicate = convert_legacy_arguments('-port=2302 -port=2402', {})
        self.assertEqual(duplicate.conflicts[0]["code"], "DUPLICATE_KNOWN_ARGUMENT")
        conflict = convert_legacy_arguments('-port=2402', {"game_port": 2302})
        self.assertEqual(conflict.conflicts[0]["code"], "STRUCTURED_ARGUMENT_CONFLICT")

    def test_tokenizer_rejects_malformed_quoting_and_control_characters(self) -> None:
        """Malformed quoting, control characters, and shell operators are rejected."""
        for raw in ('-config="broken', "-doLogs\n-password=secret", "-port=2302 | calc"):
            with self.subTest(raw=repr(raw)), self.assertRaises(ValueError):
                tokenize_windows_arguments(raw)


if __name__ == "__main__":
    unittest.main()
