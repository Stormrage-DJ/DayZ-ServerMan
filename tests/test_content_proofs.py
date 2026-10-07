"""Content proof store: round trip, key normalization, corruption, pruning and failed writes."""
from __future__ import annotations

import json
import sys
import tempfile
import unicodedata
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import source_proof, target_proof  # noqa: E402
from dayz_serverman.domain.content_proofs import (  # noqa: E402
    MAX_SOURCE_RECORDS,
    MAX_TARGET_RECORDS,
    target_directory_key,
)
from dayz_serverman.repositories.content_proofs import (  # noqa: E402
    MAX_FILE_BYTES, ContentProofStore,
)
from dayz_serverman.repositories.paths import PortablePaths  # noqa: E402

# Root identity used as the target section key
ROOT = "d" * 64


def minutes_later(count: int) -> str:
    """Return the fixed verification time plus a number of minutes."""
    moment = datetime(2026, 10, 3, 12, 0, tzinfo=UTC) + timedelta(minutes=count)
    return moment.isoformat(timespec="milliseconds")


class _Logger:
    """Collect emitted events."""

    def __init__(self) -> None:
        """Start with no events."""
        self.events: list[tuple[str, dict]] = []

    def emit(self, event: str, *, level: str = "INFO", fields=None) -> None:
        """Record one event with its fields."""
        self.events.append((event, dict(fields or {})))


class ContentProofStoreTests(unittest.TestCase):
    """Persistence rules of `data/content-proofs.json`."""

    def setUp(self) -> None:
        """Create a store in a temporary data directory."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "data" / "content-proofs.json"
        self.logger = _Logger()
        self.store = ContentProofStore(self.path, self.logger)

    def raw(self) -> dict:
        """Return the persisted document."""
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_path_entry_points_at_the_data_directory(self) -> None:
        """The portable layout names the store file under data."""
        paths = PortablePaths.from_root(Path(self.temporary.name))
        self.assertEqual(paths.content_proofs, paths.data / "content-proofs.json")

    def test_round_trip_keeps_both_record_kinds_and_the_document_shape(self) -> None:
        """Records survive a write and a read; the file has the schema 1 shape."""
        self.assertEqual(self.store.load().sources, {})
        self.assertTrue(self.store.record(
            sources={"111": source_proof()}, targets={(ROOT, "@Alpha"): target_proof()},
        ))
        document = self.store.load()
        self.assertEqual(document.sources, {"111": source_proof()})
        self.assertEqual(document.targets, {(ROOT, "@alpha"): target_proof()})
        raw = self.raw()
        self.assertEqual(set(raw), {"schema_version", "sources", "targets"})
        self.assertEqual(raw["schema_version"], 1)
        self.assertEqual(raw["targets"][ROOT]["@alpha"]["workshop_id"], "111")
        self.assertEqual(self.store.target_records(), {(ROOT, "@alpha", "111", "9")})
        # A later write merges and leaves no temporary sibling behind
        self.store.record(sources={"222": source_proof(installed_manifest_id="4")})
        self.assertEqual(set(self.store.load().sources), {"111", "222"})
        self.assertEqual([path.name for path in self.path.parent.iterdir()], [self.path.name])

    def test_target_key_ignores_letter_case_and_nothing_else(self) -> None:
        """Only names that NTFS treats as one directory share one record."""
        composed = "@Mód"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(composed, decomposed)
        # Letter case is ignored
        self.assertEqual(target_directory_key("@CF"), target_directory_key("@cf"))
        # Distinct NTFS names keep distinct keys
        self.assertNotEqual(target_directory_key("@Strasse"), target_directory_key("@Straße"))
        self.assertNotEqual(target_directory_key(composed), target_directory_key(decomposed))
        self.store.record(targets={
            (ROOT, "@Strasse"): target_proof(),
            (ROOT, "@Straße"): target_proof(workshop_id="222"),
            (ROOT, composed): target_proof(workshop_id="333"),
            (ROOT, decomposed): target_proof(workshop_id="444"),
            (ROOT, "@CF"): target_proof(workshop_id="555"),
        })
        # The other letter case replaces the same record instead of adding one
        self.store.record(targets={(ROOT, "@cf"): target_proof(workshop_id="666")})
        self.assertEqual(self.store.target_records(), {
            (ROOT, "@strasse", "111", "9"), (ROOT, "@straße", "222", "9"),
            (ROOT, composed.lower(), "333", "9"), (ROOT, decomposed.lower(), "444", "9"),
            (ROOT, "@cf", "666", "9"),
        })

    def test_file_above_the_read_cap_is_an_empty_store(self) -> None:
        """A well-formed document one byte above the cap is treated as unreadable."""
        self.store.record(sources={"111": source_proof()})
        document = self.path.read_bytes()
        # Leading white space keeps the JSON valid; at the cap the document still loads
        self.path.write_bytes(b" " * (MAX_FILE_BYTES - len(document)) + document)
        self.assertEqual(set(self.store.load().sources), {"111"})
        self.path.write_bytes(b" " * (MAX_FILE_BYTES + 1 - len(document)) + document)
        self.assertEqual(self.store.load().sources, {})
        self.assertEqual(self.store.target_records(), frozenset())

    def test_unusable_document_is_an_empty_store_and_the_next_write_replaces_it(self) -> None:
        """Unreadable text, an unknown version or a wrong root shape is an empty store."""
        self.path.parent.mkdir(parents=True)
        valid = {"schema_version": 1, "sources": {}, "targets": {}}
        for name, text in (
            ("not-json", "{broken"),
            ("unknown-version", json.dumps({**valid, "schema_version": 2})),
            ("boolean-version", json.dumps({**valid, "schema_version": True})),
            ("extra-root-field", json.dumps({**valid, "profiles": {}})),
            ("list-root", "[]"),
            ("sources-not-a-map", json.dumps({**valid, "sources": []})),
        ):
            with self.subTest(name=name):
                self.path.write_text(text, encoding="utf-8")
                self.assertEqual(self.store.load().sources, {})
                self.assertEqual(self.store.target_records(), frozenset())
                self.assertTrue(self.store.record(sources={"111": source_proof()}))
                self.assertEqual(self.raw()["sources"]["111"], source_proof().to_dict())

    def test_one_malformed_record_is_ignored_and_dropped_at_the_next_write(self) -> None:
        """Each malformed record is skipped alone; well-formed neighbours stay."""
        good_source, good_target = source_proof().to_dict(), target_proof().to_dict()
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"schema_version": 1, "sources": {
            "111": good_source,
            "222": {**good_source, "content_inventory_digest": "A" * 64},
            "333": {**good_source, "regular_file_count": 0},
            "444": {**good_source, "regular_file_count": True},
            "555": {**good_source, "installed_manifest_id": 9},
            "666": {**good_source, "verified_at": "2026-10-03T12:00:00"},
            "777": {**good_source, "extra": 1},
            "not-an-id": good_source,
            "888": "text",
        }, "targets": {
            ROOT: {"@alpha": good_target, "@Upper": good_target,
                   "@beta": {**good_target, "target_metadata_digest": None}},
            "short-root": {"@alpha": good_target},
            "e" * 64: "not-a-section",
        }}), encoding="utf-8")
        document = self.store.load()
        self.assertEqual(set(document.sources), {"111"})
        self.assertEqual(set(document.targets), {(ROOT, "@alpha")})
        # The next write keeps only the well-formed records
        self.store.record(sources={"999": source_proof()})
        raw = self.raw()
        self.assertEqual(set(raw["sources"]), {"111", "999"})
        self.assertEqual(raw["targets"], {ROOT: {"@alpha": good_target}})

    def test_malformed_new_record_is_skipped_without_losing_the_others(self) -> None:
        """A malformed record handed to a write never reaches the file."""
        self.assertTrue(self.store.record(
            sources={"111": source_proof(), "222": source_proof(total_regular_bytes=0)},
            targets={(ROOT, "@a"): target_proof(), ("bad", "@b"): target_proof()},
        ))
        document = self.store.load()
        self.assertEqual(set(document.sources), {"111"})
        self.assertEqual(set(document.targets), {(ROOT, "@a")})

    def test_pruning_drops_the_oldest_records_beyond_the_limits(self) -> None:
        """At most 512 source and 1,024 target records stay; the oldest go first."""
        sources = {str(1000 + index): source_proof(verified_at=minutes_later(index))
                   for index in range(MAX_SOURCE_RECORDS + 2)}
        targets = {(ROOT, f"@mod{index}"): target_proof(verified_at=minutes_later(index))
                   for index in range(MAX_TARGET_RECORDS + 3)}
        self.assertTrue(self.store.record(sources=sources, targets=targets))
        document = self.store.load()
        self.assertEqual(len(document.sources), MAX_SOURCE_RECORDS)
        self.assertEqual(len(document.targets), MAX_TARGET_RECORDS)
        self.assertFalse({"1000", "1001"} & set(document.sources))
        self.assertFalse({(ROOT, f"@mod{index}") for index in range(3)} & set(document.targets))
        # One newer record pushes out the then-oldest one
        self.store.record(sources={"5": source_proof(verified_at=minutes_later(9999))})
        kept = self.store.load().sources
        self.assertEqual(len(kept), MAX_SOURCE_RECORDS)
        self.assertIn("5", kept)
        self.assertNotIn("1002", kept)

    def test_failed_write_is_logged_keeps_the_old_file_and_never_raises(self) -> None:
        """A failing replace returns False, logs one warning and leaves the document."""
        self.store.record(sources={"111": source_proof()})
        before = self.path.read_bytes()
        with patch("dayz_serverman.repositories.content_proofs.replace_file",
                   side_effect=OSError("synthetic")):
            self.assertFalse(self.store.record(sources={"222": source_proof()}))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.logger.events,
                         [("content_proofs.write_failed", {"error": "OSError"})])
        self.assertEqual([path.name for path in self.path.parent.iterdir()], [self.path.name])


if __name__ == "__main__":
    unittest.main()
