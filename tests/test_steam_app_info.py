"""Newest build: token-level extraction of the app block from noisy SteamCMD output (14.4)."""
from __future__ import annotations

import unittest

try:
    from tests.server_build_fixtures import app_info_lines, vdf
except ModuleNotFoundError:
    from server_build_fixtures import app_info_lines, vdf

from dayz_serverman.domain.server_build import BranchFact
from dayz_serverman.repositories.steam_app_info import (
    AppInfoUnreadable,
    connected,
    extract_app_block,
    read_branches,
)


def block(branches: dict, app_id: str = "223350", extra: dict | None = None) -> str:
    """Return an app block with the given branch entries."""
    return vdf({app_id: {"common": {"name": "DayZ Server"}, **(extra or {}),
                         "depots": {"preloadonly": "1", "branches": branches}}})


PUBLIC = {"public": {"buildid": "24570360", "timeupdated": "1786528820", "timebuildupdated": "1"}}


class AppInfoTests(unittest.TestCase):
    """Extraction, branch reading and the connected-line rule."""

    def read(self, text: str) -> dict[str, BranchFact]:
        """Read the branches of the server app from one text."""
        return read_branches(text.splitlines(keepends=True), "223350")

    def test_recorded_output_of_the_spike(self) -> None:
        """Evidence 6.1: public 24570360 at 1786528820 and the old experimental branch."""
        lines = app_info_lines()
        self.assertTrue(connected(lines))
        self.assertEqual(read_branches(lines, "223350"), {
            "public": BranchFact(24570360, 1786528820),
            "experimental_public": BranchFact(3889697, 1559730582),
        })

    def test_noise_around_the_block_is_ignored(self) -> None:
        """Header, unloading, redirect and bootstrap lines before and after the block."""
        text = ("AppID : 223350, change number : 39096141/39096141, last change : Fri\n" + block(PUBLIC)
                + "Unloading Steam API...OK\n[  0%] Checking for available updates...\n[----] Verifying installation...\n")
        self.assertEqual(self.read(text), {"public": BranchFact(24570360, 1786528820)})

    def test_a_brace_inside_a_string_is_text(self) -> None:
        """Depth counts markers only."""
        text = block(PUBLIC, extra={"config": {"launch": "a } b { c", "escaped": 'quote " and } brace'}})
        self.assertEqual(self.read(text)["public"].build_id, 24570360)

    def test_unusable_outputs(self) -> None:
        """No block, two blocks, an unterminated string, garbage in the block, a missing public branch."""
        good = block(PUBLIC)
        cases = {
            "none": "Connecting anonymously to Steam Public...OK\n",
            "two": good + good,
            "unterminated": good.replace('"1786528820"', '"1786528820', 1),
            "unclosed": good.rstrip().rstrip("}"),
            "garbage": good.replace('"depots"', 'depots', 1),
            "no public": block({"beta": {"buildid": "5"}}),
            "public invalid": block({"public": {"buildid": "0"}}),
            "duplicate": good.replace('"common"', '"DEPOTS"\t"1"\n"common"', 1),
            "other app": block(PUBLIC, app_id="223351"),
            "second token": good.replace("{", '"x"', 1),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(AppInfoUnreadable):
                    self.read(text)

    def test_invalid_branches_are_skipped_and_the_list_is_bounded(self) -> None:
        """Bad names, ids and times are skipped; at most 64 entries are read."""
        branches = {**PUBLIC, "bad name": {"buildid": "7"}, "zero": {"buildid": "0"}, "text": {"buildid": "x1"},
                    "big": {"buildid": "4294967296"}, "no_time": {"buildid": "9", "timeupdated": "soon"}}
        facts = self.read(block(branches))
        self.assertEqual(set(facts), {"public", "no_time"})
        self.assertIsNone(facts["no_time"].time_updated)
        many = {**PUBLIC, **{f"b{index}": {"buildid": str(index + 1)} for index in range(64)}}
        facts = self.read(block(many))
        self.assertEqual(len(facts), 64)
        self.assertNotIn("b63", facts)

    def test_connected_line_rule(self) -> None:
        """Only an anonymous connection that ends with OK proves a fresh answer."""
        self.assertTrue(connected(["Connecting anonymously to Steam Public...OK\r\n"]))
        for line in ("Connecting anonymously to Steam Public...FAILED (No Connection)\n",
                     "Logging in user 'operator' to Steam Public...OK\n", ""):
            with self.subTest(line=line):
                self.assertFalse(connected([line]))

    def test_the_block_alone_is_returned(self) -> None:
        """The extracted text starts with the quoted id and ends with its brace."""
        text = "noise\n" + block(PUBLIC) + "tail\n"
        found = extract_app_block(text, "223350")
        self.assertTrue(found.startswith('"223350"') and found.rstrip().endswith("}"))
        self.assertNotIn("tail", found)


if __name__ == "__main__":
    unittest.main()
