"""Cover sensitive argument detection, alias canonicalization, and redaction."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.domain.migrations import convert_legacy_profile  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileValidationError  # noqa: E402
from dayz_serverman.observability.structured_log import StructuredLogger  # noqa: E402
from dayz_serverman.security.sensitive import (  # noqa: E402
    canonical_option_name, contains_sensitive_arguments,
)


class MigrationSecurityReworkTests(unittest.TestCase):
    """Verify migration security rejects sensitive options and redacts their values."""

    def setUp(self) -> None:
        """Create a temporary root for conversion and logging fixtures."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_migration_security_")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        """Remove the temporary root."""
        self.temporary.cleanup()

    def test_recognized_aliases_are_canonical_and_duplicates_block(self) -> None:
        """Verify alias spellings canonicalize while duplicates block selection."""
        aliases = (
            ("Name", "n-a-m-e", "Server"),
            ("CONFIG", "con_fig", "serverDZ.cfg"),
            ("Port", "p-o-r-t", "2402"),
            ("Profiles", "pro_files", "profile"),
            ("Mod", "MODS", "@One"),
            ("serverMod", "SERVER_MODS", "@Server"),
            ("Mission", "mis_sion", r"mpmissions\test"),
            ("Args", "a-r-g-s", "-doLogs"),
            ("Tweaks", "t_w_e_a_k_s", {}),
        )
        # Duplicate recognized fields block selection for every alias pair
        for first, second, value in aliases:
            with self.subTest(first=first, second=second):
                converted = convert_legacy_profile(
                    "main.json", {first: value, second: value}, self.root,
                )
                self.assertFalse(converted.selectable)
                self.assertIn(
                    "DUPLICATE_RECOGNIZED_FIELD",
                    {item.code for item in converted.conflicts},
                )
        # Mixed-case server aliases stay selectable as server-scope mods
        server_mod = convert_legacy_profile(
            "main.json", {"SeRvEr-MoD": "@Server"}, self.root,
        )
        self.assertTrue(server_mod.selectable)
        self.assertEqual(server_mod.profile.mods[0].launch_scope, "server")  # type: ignore[union-attr]

    def test_sensitive_option_spellings_and_forms_are_rejected_without_value_leak(self) -> None:
        """Verify every sensitive option form is rejected without leaking values."""
        secrets = ("SUPERSECRET", "qa-secret-token", "qa secret phrase")
        probes = (
            ("--apiKey=SUPERSECRET",), ("--api-key:SUPERSECRET",),
            ("--api_key", "SUPERSECRET"), ("/apikey", "SUPERSECRET"),
            ("Authorization:", "Bearer", "qa-secret-token"),
            ("--auth=Bearer qa-secret-token",), ("Bearer", "qa-secret-token"),
            ('password="qa secret phrase"',), ("--passwd", "SUPERSECRET"),
            ("--secret:SUPERSECRET",), ("--client-secret", "SUPERSECRET"),
            ("--access-token=SUPERSECRET",), ("--token", "SUPERSECRET"),
            ("--AuthToken", "SUPERSECRET"),
        )
        for tokens in probes:
            with self.subTest(tokens=tokens):
                self.assertTrue(contains_sensitive_arguments(tokens))
        self.assertEqual(canonical_option_name("--API_key=x"), "apikey")

        # Log every probe as structured arguments and as joined message text
        log = self.root / "manager.jsonl"
        logger = StructuredLogger(log)
        for index, tokens in enumerate(probes):
            logger.emit(f"probe.{index}", fields={"arguments": list(tokens)})
            logger.emit(f"probe.text.{index}", fields={"message": " ".join(tokens)})
        # Require that no secret value survives anywhere in the emitted log
        payload = log.read_text(encoding="utf-8")
        for secret in secrets:
            self.assertNotIn(secret, payload)

        # Confirm conversion flags the argument without echoing its value
        converted = convert_legacy_profile(
            "main.json", {"args": "--apiKey=SUPERSECRET"}, self.root,
        )
        public = json.dumps(converted.to_dict())
        self.assertIn("SENSITIVE_ARGUMENT", public)
        self.assertNotIn("SUPERSECRET", public)

    def test_sensitive_extra_arguments_cannot_reach_launch_profile(self) -> None:
        """Verify sensitive extra arguments never reach a launch profile."""
        base = {
            "profile_id": "main", "display_name": "Main",
            "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
            "runtime_profile": None, "mission_root": None, "game_port": 2302,
            "mods": [],
        }
        # Reject both inline and separate sensitive extra arguments
        for extras in (
            ["--apiKey=SUPERSECRET"], ["--authorization", "Bearer", "qa-secret-token"],
        ):
            with self.subTest(extras=extras), self.assertRaises(ProfileValidationError):
                ProfileInput.parse({**base, "extra_arguments": extras})

    def test_free_text_and_nested_redaction_covers_all_option_prefixes(self) -> None:
        """Verify free-text and nested redaction covers every option prefix."""
        probes = (
            "safe-before -apiKey ALPHA5302SECRET safe-after",
            "safe-before --API_key=BRAVO5302SECRET safe-after",
            "safe-before /api-key:CHARLIE5302SECRET safe-after",
            "safe-before //Client_Secret DELTA5302SECRET safe-after",
            "safe-before Authorization: Bearer ECHO5302SECRET safe-after",
            "safe-before Bearer FOXTROT5302SECRET safe-after",
            'safe-before -password="GOLF5302 SECRET" safe-after',
        )
        log = self.root / "matrix.jsonl"
        StructuredLogger(log).emit("redaction.matrix", fields={
            "message": " | ".join(probes),
            "nested": {"values": list(probes), "safe": "safe-surrounding-text"},
        })
        # Require each secret fragment to vanish while surrounding text survives
        emitted = log.read_text(encoding="utf-8")
        for fragment in (
            "ALPHA5302", "BRAVO5302", "CHARLIE5302", "DELTA5302",
            "ECHO5302", "FOXTROT5302", "GOLF5302",
        ):
            self.assertNotIn(fragment, emitted)
        self.assertIn("safe-before", emitted)
        self.assertIn("safe-after", emitted)
        self.assertIn("safe-surrounding-text", emitted)

    def test_empty_separator_tokens_redact_following_credentials_and_truncation(self) -> None:
        """Verify empty separators still redact following credentials."""
        sequences = [
            ["-apiKey:", "HOTEL5302SECRET"],
            ["--auth=", "INDIA5302SECRET"],
            ["Password:", "JULIET5302SECRET"],
            ["Authorization:", "Basic", "KILO5302SECRET"],
            ["Authorization:", "Bearer", "LIMA5302SECRET"],
            ["-apiKey:"], ["--auth="], ["Password:"],
            ["Authorization:", "Basic"], ["Authorization:", "Bearer"],
        ]
        log = self.root / "empty-separator.jsonl"
        StructuredLogger(log).emit("redaction.empty_separator", fields={
            "nested": {"sequences": sequences},
            "message": (
                "safe-before -apiKey: MIKE5302SECRET; --auth= NOVEMBER5302SECRET; "
                "Authorization: Basic OSCAR5302SECRET; safe-after Password:"
            ),
        })
        # Require follow-on credentials to be redacted and plain keywords to survive
        emitted = log.read_text(encoding="utf-8")
        for fragment in (
            "HOTEL5302", "INDIA5302", "JULIET5302", "KILO5302", "LIMA5302",
            "MIKE5302", "NOVEMBER5302", "OSCAR5302",
        ):
            self.assertNotIn(fragment, emitted)
        self.assertIn("safe-before", emitted)
        self.assertIn("safe-after", emitted)
        self.assertIn("Basic", emitted)
        self.assertIn("Bearer", emitted)


if __name__ == "__main__":
    unittest.main()
