"""Streamed Workshop hashing: pinned digests, bounded memory and cancellation between files."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.repositories.mod_publication_inventory import inventory_tree  # noqa: E402
from dayz_serverman.repositories.tree_metadata import tree_metadata_digest  # noqa: E402
from dayz_serverman.repositories.workshop_cache import (  # noqa: E402
    HashingCancelled,
    WorkshopCacheVerifier,
)

# One hashing chunk; the fixture sizes sit on both sides of this boundary
MIB = 1024 * 1024
# Fixed modification time (100 ns steps survive NTFS) so the fingerprint is constant too
FIXED_MTIME_NS = 1_700_000_000_000_000_000
# Digests recorded with the whole-file hashing code on 2026-10-03, before streaming
PINNED_CONTENT_DIGEST = "b771a495c80711d87e8d8c1bf1ade9f3d685ee27629c584e63cedcf9a21b8f44"
PINNED_METADATA_DIGEST = "e6eec99a5c3cfd5e04ced1acabc4d6a7f04b3c7b10e87f4bfd17d595d6a5f48e"
PINNED_MANIFEST_RECORD_DIGEST = "de06f04fc479caf95b27cfc892bf068cae9a5eb276f1eea04064b18118caa5cd"
PINNED_FILE_COUNT = 4
PINNED_TOTAL_BYTES = 3 * MIB


def pattern(size: int, offset: int) -> bytes:
    """Return deterministic bytes: a rotating byte ramp that starts at the offset."""
    ramp = bytes((value + offset) % 256 for value in range(256))
    return (ramp * (size // 256 + 1))[:size]


def build_cache(base: Path, files: dict[str, bytes], directories: tuple[str, ...] = ()) -> Path:
    """Create a Workshop cache with item 4242 and its manifest; return the content root."""
    root = base / "steamapps" / "workshop" / "content" / "221100"
    item = root / "4242"
    item.mkdir(parents=True)
    for relative in directories:
        (item / relative).mkdir(parents=True)
    # Write every file with the fixed modification time
    for relative, data in files.items():
        path = item / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))
    (base / "steamapps" / "workshop" / "appworkshop_221100.acf").write_text(
        '"AppWorkshop" { "appid" "221100" "NeedsUpdate" "0" "NeedsDownload" "0" '
        '"WorkshopItemsInstalled" { "4242" { "manifest" "77" "size" "3145728" '
        '"timeupdated" "1700000000" } } }', encoding="utf-8",
    )
    return root


def pinned_files() -> dict[str, bytes]:
    """Return the fixed fixture: sizes 0, 1 MiB - 1, 1 MiB and 1 MiB + 1, one non-ASCII name."""
    return {
        "empty.txt": b"",
        "Addons/short.pbo": pattern(MIB - 1, 1),
        "Addons/exact.pbo": pattern(MIB, 2),
        "Addons/mód.pbo": pattern(MIB + 1, 3),
    }


class PinnedDigestTests(unittest.TestCase):
    """The digests of a fixed tree equal the constants recorded before streaming."""

    def setUp(self) -> None:
        """Build the fixed fixture tree with an empty directory."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = build_cache(Path(self.temporary.name), pinned_files(), ("EmptyDir",))

    def test_fixed_tree_keeps_the_digests_recorded_before_streaming(self) -> None:
        """Content digest, fingerprint, counts and manifest digest stay byte-identical."""
        proof = WorkshopCacheVerifier(self.root).verify("4242")
        self.assertEqual(proof.content_inventory_digest, PINNED_CONTENT_DIGEST)
        self.assertEqual(proof.metadata_inventory_digest, PINNED_METADATA_DIGEST)
        self.assertEqual(proof.manifest_record_digest, PINNED_MANIFEST_RECORD_DIGEST)
        self.assertEqual(proof.regular_file_count, PINNED_FILE_COUNT)
        self.assertEqual(proof.total_regular_bytes, PINNED_TOTAL_BYTES)
        self.assertEqual(proof.installed_manifest_id, "77")

    def test_target_digest_and_fingerprint_functions_agree_with_the_source_proof(self) -> None:
        """The target digest and the fingerprint function reproduce the source values."""
        item = self.root / "4242"
        self.assertEqual(inventory_tree(item), PINNED_CONTENT_DIGEST)
        self.assertEqual(tree_metadata_digest(item), PINNED_METADATA_DIGEST)


class StreamedHashingTests(unittest.TestCase):
    """Chunk boundaries, bounded memory and the cancellation probe."""

    def setUp(self) -> None:
        """Create the temporary base directory of one cache."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_each_boundary_size_matches_a_whole_file_hash(self) -> None:
        """Sizes 0, 1 MiB - 1, 1 MiB and 1 MiB + 1 hash like the bytes read as a whole."""
        for size in (0, MIB - 1, MIB, MIB + 1):
            with self.subTest(size=size):
                # A one-byte companion keeps the item non-empty for the size 0 case
                files = {"a.bin": pattern(size, 5), "b.bin": b"x"}
                root = build_cache(self.base / str(size), files)
                expected = json.dumps([
                    ["F", name, len(data), hashlib.sha256(data).hexdigest()]
                    for name, data in sorted(files.items())
                ], ensure_ascii=False, separators=(",", ":"))
                proof = WorkshopCacheVerifier(root).verify("4242")
                self.assertEqual(proof.content_inventory_digest,
                                 hashlib.sha256(expected.encode("utf-8")).hexdigest())
                self.assertEqual(proof.total_regular_bytes, size + 1)

    def test_peak_memory_stays_bounded_for_a_large_file(self) -> None:
        """Hashing a 64 MiB file allocates far less than the file size."""
        root = build_cache(self.base, {"seed.bin": b"x"})
        # Write the large file chunk by chunk so the test itself stays small
        block = pattern(MIB, 9)
        with (root / "4242" / "large.bin").open("wb") as stream:
            for _ in range(64):
                stream.write(block)
        verifier = WorkshopCacheVerifier(root)
        tracemalloc.start()
        try:
            proof = verifier.verify("4242")
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(proof.total_regular_bytes, 64 * MIB + 1)
        # One chunk is 1 MiB; 8 MiB leaves a generous margin for shared runners
        self.assertLess(peak, 8 * MIB)

    def test_cancellation_probe_runs_before_each_file_and_never_inside_one(self) -> None:
        """The probe is asked once per file; a firing probe abandons the hash."""
        files = {"a.bin": pattern(3 * MIB, 1), "b.bin": b"b", "c.bin": b"c"}
        root = build_cache(self.base, files)
        verifier = WorkshopCacheVerifier(root)
        calls: list[int] = []

        def never() -> bool:
            """Count the call and let the hash continue."""
            calls.append(len(calls))
            return False

        # A silent probe is asked once per file, also for the three-chunk file
        proof = verifier.verify("4242", never)
        self.assertEqual(len(calls), len(files))
        self.assertEqual(proof.content_inventory_digest,
                         verifier.verify("4242").content_inventory_digest)
        calls.clear()

        def before_second_file() -> bool:
            """Fire when the second file is about to be read."""
            calls.append(len(calls))
            return len(calls) == 2

        with self.assertRaises(HashingCancelled):
            verifier.verify("4242", before_second_file)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
