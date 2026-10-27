"""Cover the Windows ordinal comparison form of the target keys: the committed table, its digest and its drift."""

from __future__ import annotations

import ctypes
import hashlib
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dayz_serverman.application.mission_map_plans import _root_spelling
from dayz_serverman.repositories.mission_map_layout import (
    ORDINAL_TABLE_FILE, ORDINAL_TABLE_SHA256, ORDINAL_TABLE_VERSION, SURROGATE_UNITS, OrdinalTableError, TargetClass,
    load_ordinal_table, ordinal_spelling, target_key,
)


# CompareStringOrdinal results for "less than" and "equal"
CSTR_LESS_THAN = 1
CSTR_EQUAL = 2
# Most differing code units that a drift failure names
NAMED_LIMIT = 40


def mission_key(text: str) -> str:
    """Return the mission target key of a folder below C:/DayZ/mpmissions."""
    return target_key(TargetClass.MISSION, Path("C:/DayZ/mpmissions") / text)


class OrdinalSpellingTests(unittest.TestCase):
    """The comparison form follows the committed RtlUpcaseUnicodeChar table on every host (A34-F1)."""

    def test_names_that_ntfs_keeps_apart_stay_apart(self) -> None:
        """Dotless i, long s, micro sign and sharp s have no ordinal upcase partner."""
        for first, second in (("mission", "m\u0131ssion"), ("case", "ca\u017fe"), ("\u00b5x", "\u039cx"),
                              ("Stra\u00dfe", "strasse")):
            with self.subTest(first=first, second=second):
                self.assertNotEqual(ordinal_spelling(first), ordinal_spelling(second))
                self.assertNotEqual(mission_key(first), mission_key(second))

    def test_names_that_ntfs_merges_share_a_form(self) -> None:
        """Greek ypogegrammeni, ASCII and Latin-1 letters merge by the table."""
        for first, second in (("\u1fb3", "\u1fbc"), ("STRASSE", "strasse"), ("\u00c4", "\u00e4")):
            with self.subTest(first=first, second=second):
                self.assertEqual(ordinal_spelling(first), ordinal_spelling(second))
                self.assertEqual(mission_key(first), mission_key(second))
        self.assertEqual(ordinal_spelling("mpmissions\\dayzOffline.test"), "MPMISSIONS/DAYZOFFLINE.TEST")

    def test_no_normalization_and_no_supplementary_folding(self) -> None:
        """NFC and NFD differ; U+10428 and U+10400 differ, because surrogate code units never map."""
        self.assertNotEqual(ordinal_spelling("caf\u00e9"), ordinal_spelling("cafe\u0301"))
        self.assertNotEqual(ordinal_spelling("\U00010428"), ordinal_spelling("\U00010400"))
        self.assertEqual(ordinal_spelling("a\U00010428"), "A\U00010428")

    def test_a_lone_surrogate_is_refused(self) -> None:
        """A strictly resolved path never holds a lone surrogate; a lenient old root with one matches nothing."""
        for text in ("x\ud801", "\udc28x", "\udc28\ud801"):
            with self.subTest(text=ascii(text)):
                self.assertRaises(ValueError, ordinal_spelling, text)
        self.assertRaises(ValueError, mission_key, "x\ud801")
        self.assertIsNone(_root_spelling("mpmissions\\x\ud801"))
        self.assertEqual(_root_spelling("mpmissions\\x"), "MPMISSIONS/X")

    def test_the_table_version_is_part_of_the_key(self) -> None:
        """The key digest input is the class, the table version and the comparison form."""
        path = Path("C:/DayZ/mpmissions/dayzOffline.test")
        expected = f"mission|{ORDINAL_TABLE_VERSION}|{ordinal_spelling(str(path))}"
        self.assertEqual(target_key(TargetClass.MISSION, path),
                         hashlib.sha256(expected.encode("utf-8")).hexdigest()[:32])
        self.assertEqual(ORDINAL_TABLE_VERSION, "windows-ordinal-1")


class OrdinalTableFileTests(unittest.TestCase):
    """The committed table loads only with its pinned digest."""

    def setUp(self) -> None:
        """Create a temporary folder for table copies."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_ordinal_")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_the_committed_table_matches_its_pin_and_header(self) -> None:
        """The header names the version, generator, build and date; no pair maps a surrogate."""
        data = ORDINAL_TABLE_FILE.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(data).hexdigest(), ORDINAL_TABLE_SHA256)
        header = [line for line in data.decode("ascii").splitlines() if line.startswith("#")]
        self.assertEqual(header[0], f"# version: {ORDINAL_TABLE_VERSION}")
        self.assertTrue(header[1].startswith("# generator: tools/generate_ordinal_upcase.py"))
        self.assertRegex(header[2], r"^# windows build: \d+\.\d+\.\d+\.\d+$")
        self.assertRegex(header[3], r"^# date: \d{4}-\d{2}-\d{2}$")
        table = load_ordinal_table(ORDINAL_TABLE_FILE)
        self.assertEqual(header[4], f"# pairs: {len(table)}")
        self.assertFalse([unit for unit in (*table, *table.values()) if unit in SURROGATE_UNITS])
        self.assertTrue(all(unit != upper for unit, upper in table.items()))

    def test_a_crlf_copy_loads_and_a_changed_copy_fails(self) -> None:
        """A CRLF checkout keeps the digest; one changed pair or a changed header fails the digest check."""
        text = ORDINAL_TABLE_FILE.read_bytes().replace(b"\r\n", b"\n")
        crlf = self.folder / "crlf.txt"
        crlf.write_bytes(text.replace(b"\n", b"\r\n"))
        self.assertEqual(load_ordinal_table(crlf), load_ordinal_table(ORDINAL_TABLE_FILE))
        for name, changed in (("pair.txt", text.replace(b"\n0069 0049\n", b"\n0131 0049\n")),
                              ("header.txt", text.replace(b"windows-ordinal-1", b"windows-ordinal-2"))):
            with self.subTest(name=name):
                self.assertNotEqual(changed, text)
                (self.folder / name).write_bytes(changed)
                self.assertRaises(OrdinalTableError, load_ordinal_table, self.folder / name)

    def test_an_unchanged_copy_loads(self) -> None:
        """The digest check depends on the content, not on the folder."""
        copy = self.folder / "ordinal_upcase.txt"
        shutil.copyfile(ORDINAL_TABLE_FILE, copy)
        self.assertEqual(load_ordinal_table(copy)[0x61], 0x41)


@unittest.skipUnless(sys.platform == "win32", "live CompareStringOrdinal exists only on Windows")
class OrdinalTableDriftTests(unittest.TestCase):
    """The committed table still matches live CompareStringOrdinal ignore-case results for every code unit."""

    def test_the_committed_table_matches_live_compare_string_ordinal(self) -> None:
        """Table-equal units compare equal, and table classes in table order compare strictly less."""
        compare = ctypes.WinDLL("kernel32").CompareStringOrdinal
        compare.argtypes = (ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int)
        compare.restype = ctypes.c_int
        table = load_ordinal_table(ORDINAL_TABLE_FILE)
        units = [unit for unit in range(0x10000) if unit not in SURROGATE_UNITS]
        started = time.perf_counter()
        # Each unit equals its table upcase: Windows splits no class that the table merges
        split = [f"U+{unit:04X}~U+{table[unit]:04X}" for unit in table
                 if compare(chr(unit), 1, chr(table[unit]), 1, 1) != CSTR_EQUAL]
        # Ordered by table upcase, neighbouring classes compare strictly less: Windows merges no two classes
        ordered = sorted(units, key=lambda unit: (table.get(unit, unit), unit))
        merged = [f"U+{first:04X}<U+{second:04X}" for first, second in zip(ordered, ordered[1:])
                  if table.get(first, first) != table.get(second, second)
                  and compare(chr(first), 1, chr(second), 1, 1) != CSTR_LESS_THAN]
        elapsed = time.perf_counter() - started
        drift = split + merged
        self.assertEqual(drift[:NAMED_LIMIT], [], f"{len(drift)} code units drifted from {ORDINAL_TABLE_VERSION}")
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
