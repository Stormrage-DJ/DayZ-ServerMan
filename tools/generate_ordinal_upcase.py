"""Generate the committed Windows ordinal upcase table of the mission map target keys (Windows only, not shipped).

The table lists every UTF-16 code unit, surrogates excepted, whose RtlUpcaseUnicodeChar result differs from
itself. Each pair is cross-checked with CompareStringOrdinal(..., TRUE). A new table needs a new version name and
a key-migration leaf with Architect review; see evidence 2.8 (Architect 16:35:31).

Usage: py tools\\generate_ordinal_upcase.py [output path]
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
from ctypes import wintypes
from datetime import date
from pathlib import Path


# Version name of the table that this generator writes; the key helper puts it into every key digest
TABLE_VERSION = "windows-ordinal-1"
# Default output: the data file beside the package modules
DEFAULT_OUTPUT = (Path(__file__).resolve().parents[1] / "runnable" / "src" / "python" / "dayz_serverman"
                  / "domain" / "ordinal_upcase.txt")
# CompareStringOrdinal result for equal strings
CSTR_EQUAL = 2
# UTF-16 surrogate code units, which the table never maps
SURROGATES = range(0xD800, 0xE000)


def _bindings() -> tuple[object, object]:
    """Return typed ntdll.RtlUpcaseUnicodeChar and kernel32.CompareStringOrdinal functions."""
    upcase = ctypes.WinDLL("ntdll").RtlUpcaseUnicodeChar
    upcase.argtypes = (wintypes.WCHAR,)
    upcase.restype = wintypes.WCHAR
    compare = ctypes.WinDLL("kernel32", use_last_error=True).CompareStringOrdinal
    compare.argtypes = (wintypes.LPCWSTR, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int, wintypes.BOOL)
    compare.restype = ctypes.c_int
    return upcase, compare


def windows_build() -> str:
    """Return the full Windows build, for example 10.0.26300.9457, with the update build revision."""
    import winreg

    version = sys.getwindowsversion()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
        revision = winreg.QueryValueEx(key, "UBR")[0]
    return f"{version.major}.{version.minor}.{version.build}.{revision}"


def upcase_pairs() -> list[tuple[int, int]]:
    """Return every (code unit, upcase) pair that differs, after the CompareStringOrdinal cross-check."""
    upcase, compare = _bindings()
    pairs = []
    for unit in range(0x10000):
        if unit in SURROGATES:
            continue
        upper = ord(upcase(chr(unit)))
        # Ignore-case ordinal comparison must call the pair equal, also for unchanged units
        if compare(chr(unit), 1, chr(upper), 1, True) != CSTR_EQUAL:
            raise SystemExit(f"CompareStringOrdinal disagrees with RtlUpcaseUnicodeChar at U+{unit:04X}")
        if upper != unit:
            pairs.append((unit, upper))
    return pairs


def table_text(pairs: list[tuple[int, int]], build: str, day: date) -> str:
    """Return the table file: the header lines, then one "unit upcase" hex pair per line, LF line ends."""
    header = [
        f"# version: {TABLE_VERSION}",
        "# generator: tools/generate_ordinal_upcase.py (ntdll.RtlUpcaseUnicodeChar, checked by CompareStringOrdinal)",
        f"# windows build: {build}",
        f"# date: {day.isoformat()}",
        f"# pairs: {len(pairs)}",
    ]
    return "\n".join(header + [f"{unit:04X} {upper:04X}" for unit, upper in pairs]) + "\n"


def main(arguments: list[str]) -> int:
    """Write the table and print the pair count, the build and the LF-normalized SHA-256 to pin."""
    if sys.platform != "win32":
        print("generate_ordinal_upcase.py runs only on Windows", file=sys.stderr)
        return 2
    output = Path(arguments[0]) if arguments else DEFAULT_OUTPUT
    pairs = upcase_pairs()
    build = windows_build()
    text = table_text(pairs, build, date.today())
    # Write LF line ends; the helper hashes the LF-normalized bytes, so a CRLF checkout keeps the digest
    output.write_bytes(text.encode("ascii"))
    digest = hashlib.sha256(text.encode("ascii")).hexdigest()
    print(f"{output}: {len(pairs)} pairs, Windows build {build}, SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
