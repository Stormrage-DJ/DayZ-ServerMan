"""Tests for legacy argument tokenization, conversion, and copy-only migration records."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path, PureWindowsPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# The prototypes this module exercised are not part of this repository.
if importlib.util.find_spec("reference") is None:
    raise unittest.SkipTest("reference prototypes are not available")
from reference.prototypes.legacy_conversion import (  # noqa: E402
    CopyIntent,
    IssueCode,
    MigrationResult,
    build_migration_preview,
    convert_legacy_arguments,
    tokenize_windows_arguments,
)


class WindowsTokenizationTests(unittest.TestCase):
    """Tokenization rules for legacy Windows command line arguments."""
    def test_representative_legacy_arguments(self) -> None:
        """Representative legacy arguments split into the expected tokens."""
        tokens = tokenize_windows_arguments(
            r'-config="server profile\serverDZ.cfg" -port=2302 "-mod=@Core;@Map" -doLogs'
        )
        self.assertEqual(
            tokens,
            (
                r"-config=server profile\serverDZ.cfg",
                "-port=2302",
                "-mod=@Core;@Map",
                "-doLogs",
            ),
        )

    def test_empty_and_escaped_quotes_follow_windows_rules(self) -> None:
        """Empty input and escaped quotes follow Windows parsing rules."""
        self.assertEqual(tokenize_windows_arguments(''), ())
        self.assertEqual(
            tokenize_windows_arguments(r'-name="The \"Safe\" Server"'),
            ('-name=The "Safe" Server',),
        )

    def test_unmatched_quote_is_blocked(self) -> None:
        """An unmatched quote blocks tokenization with a clear error."""
        with self.assertRaisesRegex(ValueError, "unmatched quote"):
            tokenize_windows_arguments('-config="broken')

    def test_unquoted_shell_metacharacter_is_blocked(self) -> None:
        """An unquoted shell metacharacter blocks tokenization."""
        with self.assertRaisesRegex(ValueError, "shell metacharacter"):
            tokenize_windows_arguments('-port=2302 & calc.exe')


class ConversionTests(unittest.TestCase):
    """Conversion rules for legacy arguments against structured fields."""
    def test_known_fields_are_converted_and_unknown_arguments_keep_order(self) -> None:
        """Known fields convert while unknown arguments keep their original order."""
        conversion = convert_legacy_arguments(
            '-port=2302 -doLogs -adminLog -config=serverDZ.cfg',
            {},
        )
        self.assertTrue(conversion.can_import)
        self.assertEqual(conversion.structured_fields["game_port"], "2302")
        self.assertEqual(conversion.structured_fields["config_path"], "serverDZ.cfg")
        self.assertEqual(conversion.extra_arguments, ("-doLogs", "-adminLog"))

    def test_repeated_known_field_is_blocking(self) -> None:
        """A repeated known field blocks the import."""
        conversion = convert_legacy_arguments('-port=2302 -port=2402', {})
        self.assertFalse(conversion.can_import)
        self.assertEqual(
            conversion.blocking_issues[0].code,
            IssueCode.DUPLICATE_KNOWN_ARGUMENT,
        )

    def test_conflicting_structured_field_is_blocking(self) -> None:
        """An argument that disagrees with a structured field blocks the import."""
        conversion = convert_legacy_arguments('-port=2402', {"game_port": 2302})
        self.assertEqual(
            conversion.blocking_issues[0].code,
            IssueCode.STRUCTURED_CONFLICT,
        )
        self.assertEqual(conversion.structured_fields["game_port"], "2302")

    def test_equal_structured_field_is_removed_with_warning(self) -> None:
        """An argument equal to the structured value is dropped with a warning."""
        conversion = convert_legacy_arguments('-port=2302', {"game_port": 2302})
        self.assertTrue(conversion.can_import)
        self.assertEqual(len(conversion.warnings), 1)
        self.assertEqual(conversion.extra_arguments, ())

    def test_malformed_input_preserves_existing_structured_fields(self) -> None:
        """Malformed input blocks the import and keeps existing structured fields."""
        conversion = convert_legacy_arguments('-config="broken', {"game_port": 2302})
        self.assertFalse(conversion.can_import)
        self.assertEqual(conversion.structured_fields["game_port"], "2302")


class MigrationStructureTests(unittest.TestCase):
    """Copy-only preview and result record contracts."""
    def test_preview_and_result_are_copy_only_records(self) -> None:
        """Preview and result records stay copy only and publishable when permitted."""
        intent = CopyIntent("legacy-profile-a", PureWindowsPath("data/profiles/a.json"))
        preview = build_migration_preview('-port=2302', {}, [intent])
        result = MigrationResult(
            preview,
            (PureWindowsPath("data/profiles/a.json"),),
            True,
            "migration-001",
        )
        self.assertTrue(preview.can_import)
        self.assertTrue(result.published)

    def test_destination_escape_is_rejected(self) -> None:
        """A copy destination that escapes its root is rejected."""
        with self.assertRaises(ValueError):
            CopyIntent("legacy-profile-a", PureWindowsPath("../legacy/profile.json"))

    def test_blocked_preview_cannot_be_published(self) -> None:
        """A blocked preview cannot be turned into a published result."""
        preview = build_migration_preview('-port=2302 -port=2402', {}, [])
        with self.assertRaises(ValueError):
            MigrationResult(preview, (), True, "migration-002")


if __name__ == "__main__":
    unittest.main()
