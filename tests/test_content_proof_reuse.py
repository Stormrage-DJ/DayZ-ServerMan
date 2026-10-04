"""Update operation with the proof store: reuse across profiles and exactly one hash on change."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import build_cache, build_target, write_manifest  # noqa: E402
from dayz_serverman.adapters.windows.steamcmd import (  # noqa: E402
    SteamCmdPaths, SteamCmdRunResult,
)
from dayz_serverman.application.content_proofs import ContentProofResolver  # noqa: E402
from dayz_serverman.application.workshop_updates import (  # noqa: E402
    UpdateRequest, WorkshopUpdateService,
)
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.domain.workshop import AuthenticationMode  # noqa: E402
from dayz_serverman.repositories.applied_mod_state import AppliedModStateRepository  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402

# Patch target of the full content hash of one Workshop item
FULL_HASH = "dayz_serverman.repositories.workshop_cache.WorkshopCacheVerifier._inventory"


def profile_record(profile_id: str, port: int, mods: dict[str, str]) -> ProfileRecord:
    """Return a profile whose Workshop mods map the directory to the item id."""
    return ProfileRecord(1, ProfileInput.parse({
        "profile_id": profile_id, "display_name": profile_id.title(),
        "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
        "runtime_profile": None, "mission_root": None, "game_port": port,
        "mods": [{"directory": directory, "launch_scope": "client",
                  "source": {"kind": "workshop", "workshop_id": workshop_id}}
                 for directory, workshop_id in mods.items()],
        "extra_arguments": [],
    }))


class _SteamCmd:
    """SteamCMD stand-in: every requested item succeeds; an optional hook changes the cache."""

    def __init__(self) -> None:
        """Start without a cache-changing hook."""
        self.runs = 0
        self.during_run = None

    def run_update(self, _paths, argv, _cancellation_requested, _on_launched, before_launch):
        """Report one success line per requested item."""
        before_launch()
        self.runs += 1
        if self.during_run is not None:
            self.during_run()
        ids = [argv[index + 2] for index, value in enumerate(argv)
               if value == "+workshop_download_item"]
        lines = tuple(f"Success. Downloaded item {workshop_id}" for workshop_id in ids)
        return SteamCmdRunResult(0, lines, False, True, 77)


class _Context:
    """Operation context stand-in that never cancels."""

    cancellation_requested = False

    def publish_detail(self, _items) -> None:
        """Accept and drop the advisory item progress."""

    def checkpoint(self, _phase: str, _percent: int) -> None:
        """Accept every checkpoint."""

    def record_evidence(self, _evidence) -> None:
        """Accept the child evidence."""


class ContentProofReuseTests(unittest.TestCase):
    """Criterion 21: a full hash runs only for a tree whose manifest id or fingerprint changed."""

    def setUp(self) -> None:
        """Create a cache with two items, a server folder and the wired update service."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.cache = build_cache(self.base, {"111": b"alpha", "222": b"bravo"})
        self.dayz = self.base / "DayZ Server"
        build_target(self.dayz, "@Alpha", b"alpha")
        self.profiles = {
            "main": profile_record("main", 2302, {"@Alpha": "111", "@Bravo": "222"}),
            "second": profile_record("second", 2402, {"@Alpha": "111", "@Bravo": "222"}),
        }
        self.settings = SimpleNamespace(
            revision=1, steam_authentication_mode="ANONYMOUS", steam_account_name=None,
            dayz_root=str(self.dayz), workshop_content_root=str(self.cache),
        )
        self.store = ContentProofStore(self.base / "data" / "content-proofs.json")
        self.legacy_path = self.base / "data" / "applied-mod-state.json"
        self.steamcmd = _SteamCmd()
        executable = self.base / "steamcmd.exe"
        self.service = WorkshopUpdateService(
            SimpleNamespace(read=lambda profile_id: self.profiles[profile_id]),
            SimpleNamespace(load=lambda: self.settings),
            SimpleNamespace(
                inspect=lambda _settings: SteamCmdPaths(self.base, executable, self.cache),
                revalidate=lambda _paths: None,
            ),
            self.steamcmd, WorkshopCacheVerifier,
            content_proofs=ContentProofResolver(
                self.store, AppliedModStateRepository(self.legacy_path)),
        )

    def update(self, profile_id: str = "main") -> dict[str, dict]:
        """Run one update for the profile and return the result items by Workshop id."""
        record = self.profiles[profile_id]
        result = self.service.update(UpdateRequest(
            profile_id, record.revision, record.semantic_digest, 1,
            AuthenticationMode.ANONYMOUS, None,
        ), _Context())
        self.assertEqual(result["download_state"], "VERIFIED")
        return {item["item"]["workshop_id"]: item for item in result["items"]}

    def kinds(self, items: dict[str, dict]) -> dict[str, str]:
        """Return the proof kind per Workshop id."""
        return {key: value["cache_proof"]["verification_kind"] for key, value in items.items()}

    def hashed(self, profile_id: str = "main") -> tuple[list[str], dict[str, dict]]:
        """Run one update and return the ids that were fully hashed, with the result items."""
        original = WorkshopCacheVerifier._inventory
        seen: list[str] = []

        def counting(item: Path, root: Path, probe=None):
            """Record the hashed item and run the real hash."""
            seen.append(item.name)
            return original(item, root, probe)

        with patch(FULL_HASH, side_effect=counting):
            return seen, self.update(profile_id)

    def test_tree_without_a_stored_proof_is_hashed_once_to_create_it(self) -> None:
        """The first update hashes every item; the second one hashes nothing."""
        seen, first = self.hashed()
        self.assertEqual(seen, ["111", "222"])
        self.assertEqual(self.kinds(first), {"111": "FULL_CONTENT", "222": "FULL_CONTENT"})
        self.assertEqual(set(self.store.load().sources), {"111", "222"})
        with patch(FULL_HASH, side_effect=AssertionError("full hash must not run")):
            second = self.update()
        self.assertEqual(self.kinds(second), {"111": "STORED_SOURCE", "222": "STORED_SOURCE"})
        for workshop_id in ("111", "222"):
            self.assertEqual(second[workshop_id]["outcome"], "VERIFIED_CURRENT")
            self.assertIsNone(second[workshop_id]["cache_proof"]["target_metadata_digest"])
            self.assertEqual(second[workshop_id]["cache_proof"]["content_inventory_digest"],
                             first[workshop_id]["cache_proof"]["content_inventory_digest"])

    def test_second_profile_and_edited_profile_reuse_the_proof_without_a_hash(self) -> None:
        """A proof made for one profile serves another profile and an edited one."""
        first = self.update("main")
        with patch(FULL_HASH, side_effect=AssertionError("full hash must not run")):
            second = self.update("second")
            # A semantic edit (the port) changes the profile digest, not the proof
            old_digest = self.profiles["main"].semantic_digest
            self.profiles["main"] = profile_record(
                "main", 2500, {"@Alpha": "111", "@Bravo": "222"})
            self.assertNotEqual(self.profiles["main"].semantic_digest, old_digest)
            edited = self.update("main")
        for items in (second, edited):
            self.assertEqual(self.kinds(items), {"111": "STORED_SOURCE", "222": "STORED_SOURCE"})
            self.assertEqual(items["111"]["cache_proof"]["content_inventory_digest"],
                             first["111"]["cache_proof"]["content_inventory_digest"])

    def test_changed_fingerprint_forces_exactly_one_hash(self) -> None:
        """Only the item whose tree changed is hashed again, and only once."""
        self.update()
        (self.cache / "222" / "Addons" / "mod.pbo").write_bytes(b"bravo-changed")
        seen, items = self.hashed()
        self.assertEqual(seen, ["222"])
        self.assertEqual(self.kinds(items), {"111": "STORED_SOURCE", "222": "FULL_CONTENT"})
        # The new proof replaced the record, so the next run hashes nothing
        seen, items = self.hashed("second")
        self.assertEqual(seen, [])
        self.assertEqual(self.kinds(items), {"111": "STORED_SOURCE", "222": "STORED_SOURCE"})

    def test_changed_manifest_forces_exactly_one_hash(self) -> None:
        """A new installed manifest id invalidates the record of that item only."""
        self.update()
        # SteamCMD installs a new manifest for item 111 during the run
        self.steamcmd.during_run = lambda: write_manifest(
            self.base, {"111": ("10", 200), "222": ("9", 100)})
        seen, items = self.hashed()
        self.assertEqual(seen, ["111"])
        self.assertEqual(items["111"]["outcome"], "UPDATED_VERIFIED")
        self.assertEqual(self.kinds(items), {"111": "FULL_CONTENT", "222": "STORED_SOURCE"})
        record = self.store.load().sources["111"]
        self.assertEqual((record.installed_manifest_id, record.time_updated), ("10", 200))
        self.steamcmd.during_run = None
        self.assertEqual(self.hashed()[0], [])

    def test_update_never_writes_the_legacy_file(self) -> None:
        """The update operation records proofs in the store only."""
        self.update()
        self.assertTrue(self.store._path.is_file())
        self.assertFalse(self.legacy_path.exists())


if __name__ == "__main__":
    unittest.main()
