"""Task 5.2: `mods verify` with one result per Workshop mod (10.3; criteria 8, 10, 12, 13, 18, 27).

Real compositions over the fixture's Workshop cache (`steam_cli_fixtures.SteamRoot.workshop_cache`):
the verification hashes the download and the server folder copy on the lane. No SteamCMD, no network.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import saved_state  # noqa: E402
from steam_cli_fixtures import WORKSHOP_ID, SteamRoot  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import Table  # noqa: E402
from dayz_serverman.cli.commands.mods_verify import mod_directories  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

PROFILE = ("--profile", "livonia-main")


class ModsVerifyTests(SteamRoot):
    """`mods verify` on the fixture profile's Workshop mod."""

    def server_copy(self) -> Path:
        """Return the server folder copy of the fixture's Workshop mod."""
        return self.dayz / "@Community Framework" / "Addons" / "mod.pbo"

    def test_matching_copy_in_text_and_json(self) -> None:
        """One line per mod with its folder, its Workshop item and "Verified"; the page's summary sentence."""
        self.workshop_cache()
        code, stdout, stderr = self.cli("mods", "verify", *PROFILE)
        self.assertEqual(code, 0, stderr)
        lines = stdout.splitlines()
        self.assertEqual(lines[1].split(), ["Mod", "Workshop", "item", "Result"])
        self.assertIn("@Community Framework", lines[2])
        self.assertIn(WORKSHOP_ID, lines[2])
        self.assertTrue(lines[2].endswith("Verified"))
        self.assertEqual(lines[-1], "The mod is verified; the server copy matches")
        self.assertIn("Verifying mod files", stderr)
        code, stdout, _stderr = self.cli("mods", "verify", *PROFILE, "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["operations"][0]["kind"], value["review"]), (0, "VERIFY_WORKSHOP_FILES", None))
        item = value["result"]["items"][0]
        self.assertEqual((item["workshop_id"], item["source_state"], item["target_state"]),
                         (WORKSHOP_ID, "VERIFIED", "MATCHES_SOURCE"))

    def test_a_differing_copy_is_a_result_not_a_failure(self) -> None:
        """The problem is listed per mod and counted; the operation succeeded, so the exit code is 0."""
        self.workshop_cache(matching=False)
        before = self.server_copy().read_bytes()
        code, stdout, stderr = self.cli("mods", "verify", *PROFILE)
        self.assertEqual(code, 0, stderr)
        self.assertIn("Server copy differs from the download", stdout)
        self.assertEqual(stdout.splitlines()[-1], "1 problem found")
        # Verification changes no server file
        self.assertEqual(self.server_copy().read_bytes(), before)

    def test_a_missing_download_fails_the_operation(self) -> None:
        """Without a downloaded copy the operation ends failed: exit 1 with the record in the JSON details."""
        code, stdout, _stderr = self.cli("mods", "verify", *PROFILE, "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["details"]["operation"]["state"]), (1, "FAILED"))
        self.assertTrue(error["message"].startswith("The mod files could not be verified."))

    def test_refusals_before_the_submit_change_nothing(self) -> None:
        """Unknown profile (2), differing pin (3), another instance (3), Ctrl+C before the submit (5)."""
        self.workshop_cache()
        before = saved_state(self.manager, self.dayz)
        proofs = self.manager / "data" / "content-proofs.json"
        cases = ((("--profile", "Nobody"), 2, "USAGE"),
                 ((*PROFILE, "--expect-profile-revision", "9"), 3, "REVISION_CONFLICT"))
        for arguments, expected, json_code in cases:
            with self.subTest(arguments=arguments):
                code, stdout, _stderr = self.cli("mods", "verify", *arguments, "--json")
                self.assertEqual((code, json.loads(stdout)["error"]["code"]), (expected, json_code))
        interrupts = Interrupts()
        interrupts.handle(2, None)
        code, stdout, _stderr = self.cli("mods", "verify", *PROFILE, "--json", interrupts=interrupts)
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (5, "CANCELLED"))
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            code, stdout, _stderr = self.cli("mods", "verify", *PROFILE, "--json")
            self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
        finally:
            holder.close(drain_seconds=5)
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        self.assertFalse(proofs.exists())

    def test_output_follows_the_input_name_rules(self) -> None:
        """Folders and Workshop items are stored data; no identifier in prose (criterion 18)."""
        self.workshop_cache(matching=False)
        RecordingOutput.made = []
        with patch.object(runner, "Output", RecordingOutput):
            self.cli("mods", "verify", *PROFILE)
        written = [block for output in RecordingOutput.made for block in output.written]
        self.assertTrue(any(isinstance(block, Table) for block in written))
        InputNameChecker(self, flags() | {"livonia-main"}, ["livonia-main"]).blocks(written)


class DirectoryTests(unittest.TestCase):
    """The mod folder of each Workshop item comes from the profile record."""

    def test_mod_directories(self) -> None:
        """Workshop mods map to their folders; local mods and odd entries are skipped."""
        record = {"mods": [{"directory": "@A", "source": {"kind": "workshop", "workshop_id": "1"}},
                           {"directory": "@B", "source": {"kind": "external"}}, "odd"]}
        self.assertEqual(mod_directories(record), {"1": "@A"})


if __name__ == "__main__":
    unittest.main()
