"""Profile domain tests for validation, launch command building, and path safety."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.arguments import build_launch_command  # noqa: E402
from dayz_serverman.domain.profiles import (  # noqa: E402
    ProfileInput, ProfileRecord, ProfileValidationError, semantic_profile_digest,
    validate_profile_id,
)
from tests.profile_fixtures import profile_payload  # noqa: E402


def create_launch_tree(root: Path) -> None:
    """Create the directories and files a valid launch profile references."""
    # Create every directory the profile payload references
    for relative in (
        r"Profiles\Máin Runtime", r"mpmissions\dayzOffline.enoch",
        "@Community Framework", "@Server Tools",
    ):
        root.joinpath(*relative.split("\\")).mkdir(parents=True)
    for relative in (r"Bin\DayZ Server_x64.exe", r"Config Files\serverDZ.cfg"):
        # Placeholder bytes are enough for the path resolution checks
        path = root.joinpath(*relative.split("\\"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic fixture")


class ProfileValidationTests(unittest.TestCase):
    """Contract: profile input validation is strict about ids, paths, mods, and tokens."""
    def test_profile_id_is_stable_and_windows_safe(self) -> None:
        """Accept stable lowercase ids and reject unsafe or reserved ones."""
        self.assertEqual(validate_profile_id("livonia-main"), "livonia-main")
        for invalid in ("Livonia", "-bad", "bad_underscore", "con", "COM1", "a" * 65):
            with self.subTest(invalid=invalid), self.assertRaises(ProfileValidationError):
                validate_profile_id(invalid)

    def test_paths_reject_absolute_traversal_empty_ads_wildcards_and_reserved_segments(self) -> None:
        """Reject absolute, traversal, empty, ADS, wildcard, and reserved path parts."""
        invalid = (
            r"C:\DayZ\server.exe", r"\rooted", r"..\server.exe", r"folder\.\server.exe",
            r"folder\\server.exe", r"folder\bad:name.exe", r"folder\*.exe",
            r"folder\NUL.txt", "folder\\trailing."
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ProfileValidationError):
                ProfileInput.parse(profile_payload(server_executable=value))

    def test_paths_reject_all_windows_controls_and_normalize_unicode(self) -> None:
        """Reject every raw control character and normalize unicode paths."""
        # Every raw control character must be rejected
        for codepoint in range(32):
            value = f"folder\\bad{chr(codepoint)}name"
            with self.subTest(codepoint=codepoint), self.assertRaises(ProfileValidationError):
                ProfileInput.parse(profile_payload(runtime_profile=value))
        # Composed and decomposed unicode spellings must normalize equal
        composed = ProfileInput.parse(profile_payload(runtime_profile="Profiles\\Máin"))
        decomposed = ProfileInput.parse(profile_payload(runtime_profile="Profiles\\Ma\u0301in"))
        self.assertEqual(composed.runtime_profile, decomposed.runtime_profile)

    def test_tagged_mod_sources_order_and_duplicates_are_strict(self) -> None:
        """Enforce tagged mod sources, ordering, and duplicate rules strictly."""
        # The parsed mods keep profile order with tagged scope and source kinds
        profile = ProfileInput.parse(profile_payload())
        self.assertEqual(
            [(item.launch_scope, item.source.kind) for item in profile.mods],
            [("client", "workshop"), ("server", "external")],
        )
        invalid_sets = [
            [
                {"directory": "@Mód", "launch_scope": "client",
                 "source": {"kind": "workshop", "workshop_id": "1"}},
                {"directory": "@MÓD", "launch_scope": "server", "source": {"kind": "external"}},
            ],
            [
                {"directory": "@One", "launch_scope": "client",
                 "source": {"kind": "workshop", "workshop_id": "99"}},
                {"directory": "@Two", "launch_scope": "server",
                 "source": {"kind": "workshop", "workshop_id": "99"}},
            ],
            [{"directory": "@One", "launch_scope": "client",
              "source": {"kind": "external", "workshop_id": "99"}}],
            [{"directory": "@One", "launch_scope": "other", "source": {"kind": "external"}}],
        ]
        # Duplicate, case-colliding, and malformed mod sets must all be rejected
        for mods in invalid_sets:
            with self.subTest(mods=mods), self.assertRaises(ProfileValidationError):
                ProfileInput.parse(profile_payload(mods=mods))

    def test_structured_switch_spellings_and_unsafe_tokens_are_blocked(self) -> None:
        """Block structured switch spellings and unsafe argument tokens."""
        # Every reserved spelling and unsafe token must be rejected
        for token in (
            "-PORT=2402", "-config", "-mod=@Override", "-SERVERMOD=@X", "-Profiles=data",
            "value&command", "line\nbreak", "nul\x00byte", '"pre-quoted"', "powershell.exe",
        ):
            with self.subTest(token=token), self.assertRaises(ProfileValidationError):
                ProfileInput.parse(profile_payload(extra_arguments=[token]))

    def test_semantic_digest_changes_only_for_authoritative_semantic_fields(self) -> None:
        """Change the semantic digest only for authoritative semantic fields."""
        # Renaming a non-semantic field must keep the digest
        base = ProfileInput.parse(profile_payload())
        renamed = ProfileInput.parse(profile_payload(display_name="Different"))
        changed = ProfileInput.parse(profile_payload(runtime_profile=None))
        self.assertEqual(semantic_profile_digest(base), semantic_profile_digest(renamed))
        self.assertNotEqual(semantic_profile_digest(base), semantic_profile_digest(changed))
        # Semantic changes and switch edits must change it, a rebuild must not
        executable = ProfileInput.parse(profile_payload(server_executable="Other.exe"))
        extras = ProfileInput.parse(profile_payload(extra_arguments=["-doLogs", "-newSafeFlag"]))
        self.assertNotEqual(semantic_profile_digest(base), semantic_profile_digest(executable))
        self.assertNotEqual(semantic_profile_digest(base), semantic_profile_digest(extras))
        self.assertEqual(semantic_profile_digest(base), semantic_profile_digest(ProfileInput.parse(profile_payload())))


class LaunchCommandTests(unittest.TestCase):
    """Contract: launch commands preserve exact argv order and refuse unsafe roots."""
    def test_exact_argv_order_preserves_scope_spaces_unicode_and_null_omission(self) -> None:
        """Preserve exact argv order, spaces, unicode, and null-profile omission."""
        # Build a launch command against a spaced unicode root
        with tempfile.TemporaryDirectory(prefix="serverman_dayz_") as temporary:
            root = Path(temporary) / "DáyZ Root With Spaces"
            create_launch_tree(root)
            record = ProfileRecord(7, ProfileInput.parse(profile_payload()))
            command = build_launch_command(record, str(root))
            executable = root / "Bin" / "DayZ Server_x64.exe"
            # The argv keeps exact order, scoping, and switch spellings
            self.assertEqual(command.argv, (
                str(executable.resolve()), r"-config=Config Files\serverDZ.cfg", "-port=2302",
                r"-profiles=Profiles\Máin Runtime", r"-mission=mpmissions\dayzOffline.enoch",
                "-mod=@Community Framework", "-serverMod=@Server Tools",
                "-doLogs", "-adminLog", "-name=Árvíztűrő Server",
            ))
            # Without a runtime profile the profiles switch is omitted
            without = ProfileRecord(8, ProfileInput.parse(profile_payload(runtime_profile=None)))
            self.assertFalse(any(item.startswith("-profiles=") for item in build_launch_command(without, str(root)).argv))

    def test_missing_directory_and_reparse_escape_fail_closed(self) -> None:
        """Fail closed on a missing mod directory and a reparse escape."""
        with tempfile.TemporaryDirectory(prefix="serverman_dayz_") as temporary:
            root = Path(temporary) / "root"
            create_launch_tree(root)
            # A removed mod directory must fail the launch closed
            (root / "@Server Tools").rmdir()
            with self.assertRaisesRegex(ProfileValidationError, "mod directory"):
                build_launch_command(ProfileRecord(0, ProfileInput.parse(profile_payload())), str(root))
            # A symlinked mod directory must be refused as a reparse point
            if hasattr(os, "symlink"):
                outside = Path(temporary) / "outside"
                outside.mkdir()
                link = root / "@Server Tools"
                try:
                    os.symlink(outside, link, target_is_directory=True)
                except OSError:
                    return
                with self.assertRaisesRegex(ProfileValidationError, "reparse point"):
                    build_launch_command(ProfileRecord(0, ProfileInput.parse(profile_payload())), str(root))

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_dayz_root_junction_is_rejected_before_resolution(self) -> None:
        """Reject a junction root before any path resolution."""
        # Create a junction that points at the real DayZ tree
        with tempfile.TemporaryDirectory(prefix="serverman_dayz_junction_") as temporary:
            actual = Path(temporary) / "actual"
            create_launch_tree(actual)
            junction = Path(temporary) / "junction"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(actual)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                self.skipTest("junction creation is unavailable")
            record = ProfileRecord(0, ProfileInput.parse(profile_payload()))
            # The real tree resolves while the junction root must be refused
            self.assertEqual(build_launch_command(record, str(actual)).working_directory, actual.resolve())
            with self.assertRaisesRegex(ProfileValidationError, "root contains a link or reparse"):
                build_launch_command(record, str(junction))


if __name__ == "__main__":
    unittest.main()
