"""Publication intent inventory tests over the managed workshop cache and keys."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.repositories.mod_publication_inventory import (
    PublicationInventoryError,
    build_publication_intent,
)
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier
from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity


def profile() -> ProfileRecord:
    """Build the workshop profile fixture shared by the inventory tests."""
    return ProfileRecord(7, ProfileInput.parse({
        "profile_id": "main", "display_name": "Main",
        "server_executable": "DayZServer_x64.exe", "server_config": "serverDZ.cfg",
        "runtime_profile": None, "mission_root": None, "game_port": 2302,
        "mods": [
            {"directory": "mods\\alpha", "launch_scope": "client",
             "source": {"kind": "workshop", "workshop_id": "111"}},
            {"directory": "mods\\local", "launch_scope": "client",
             "source": {"kind": "external"}},
            {"directory": "mods\\beta", "launch_scope": "server",
             "source": {"kind": "workshop", "workshop_id": "222"}},
        ], "extra_arguments": [],
    }))


class PublicationFixture(unittest.TestCase):
    """Shared fixture that builds a verified workshop cache and publication intents."""
    def setUp(self) -> None:
        """Create a temporary workshop cache with two verified items and a manifest."""
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.cache = self.base / "steamapps" / "workshop" / "content" / "221100"
        # Install both workshop items with identical shared-key bytes
        for item, content in (("111", b"alpha"), ("222", b"beta")):
            root = self.cache / item
            (root / "Addons").mkdir(parents=True)
            (root / "Addons" / f"{item}.pbo").write_bytes(content)
            (root / "keys").mkdir()
            (root / "keys" / "Shared.BIKEY").write_bytes(b"same-key")
        # Manifest marks both items installed so the cache verifies as current
        manifest = self.base / "steamapps" / "workshop" / "appworkshop_221100.acf"
        manifest.write_text(
            '"AppWorkshop" { "appid" "221100" "NeedsUpdate" "0" "NeedsDownload" "0" '
            '"WorkshopItemsInstalled" { '
            '"111" { "manifest" "1" "size" "5" "timeupdated" "1" } '
            '"222" { "manifest" "2" "size" "4" "timeupdated" "1" } } }',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        """Remove the temporary cache and manifest."""
        self.temporary.cleanup()

    def build(self, dayz_root: Path | None = None):
        """Build a publication intent from the fixture cache, honoring a root override."""
        # Verify both workshop items before building the intent
        verifier = WorkshopCacheVerifier(self.cache)
        proofs = {item: verifier.verify(item) for item in ("111", "222")}
        return build_publication_intent(
            publication_id="publication-1", profile=profile(), settings_revision=2,
            dayz_root_identity=(dayz_root_identity(dayz_root) if dayz_root else "d" * 64),
            cache_root=self.cache, proofs=proofs,
        )


class PublicationInventoryTests(PublicationFixture):
    """Contract of the publication inventory: ordering, coalescence, and fail-closed proofs."""
    def test_workshop_order_local_exclusion_and_identical_key_coalescence(self) -> None:
        """Keep workshop order from the profile and coalesce identical keys."""
        # Build the intent from the verified fixture cache
        intent = self.build()
        # Managed sources follow the profile order with the local mod excluded
        self.assertEqual([item.workshop_id for item in intent.managed_sources], ["111", "222"])
        self.assertEqual([item.target_relative for item in intent.managed_sources], [
            "mods\\alpha", "mods\\beta",
        ])
        self.assertEqual(len(intent.keys), 1)
        self.assertEqual(intent.keys[0].source_workshop_ids, ("111", "222"))
        self.assertNotIn("local", str(intent.body()).casefold())

    def test_differing_normalized_key_collision_fails_before_intent(self) -> None:
        """Fail closed on a case-insensitive key collision before any intent exists."""
        # Rename one key so both items normalize to the same name with different bytes
        first = self.cache / "111" / "keys" / "Shared.BIKEY"
        first.rename(first.with_name("SHARED.bikey"))
        first.with_name("SHARED.bikey").write_bytes(b"different")
        # Rebuild the proofs so the failure comes from the collision alone
        verifier = WorkshopCacheVerifier(self.cache)
        proofs = {item: verifier.verify(item) for item in ("111", "222")}
        # The collision must block intent construction
        with self.assertRaisesRegex(PublicationInventoryError, "key collision"):
            build_publication_intent(
                publication_id="publication-1", profile=profile(), settings_revision=2,
                dayz_root_identity="d" * 64, cache_root=self.cache, proofs=proofs,
            )

    def test_stale_cache_proof_fails_closed(self) -> None:
        """Fail closed when the manifest no longer matches a verified item."""
        # Verify both items, then change the recorded manifest behind the proofs
        verifier = WorkshopCacheVerifier(self.cache)
        proofs = {item: verifier.verify(item) for item in ("111", "222")}
        manifest = self.base / "steamapps/workshop/appworkshop_221100.acf"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace('"manifest" "1"', '"manifest" "3"'),
            encoding="utf-8",
        )
        # The stale proof must fail closed with a coded error
        with self.assertRaises(PublicationInventoryError) as raised:
            build_publication_intent(
                publication_id="publication-1", profile=profile(), settings_revision=2,
                dayz_root_identity="d" * 64, cache_root=self.cache, proofs=proofs,
            )
        self.assertEqual(raised.exception.code, "CACHE_VERIFICATION_FAILED")

    def test_safe_non_key_entries_are_ignored(self) -> None:
        """Ignore files and directories that are not key files."""
        # Add benign entries beside the real key file
        (self.cache / "111/keys/readme.txt").write_text("safe", encoding="utf-8")
        (self.cache / "111/keys/docs").mkdir()
        # Only the real key file appears in the inventory
        intent = self.build()
        self.assertEqual(len(intent.keys), 1)
        self.assertEqual(intent.keys[0].filename, "Shared.BIKEY")

    def test_mutation_between_output_inventory_and_final_cache_proof_fails(self) -> None:
        """Reject a cache mutation detected between output inventory and the final proof."""
        # Verify both items before arming the mutation checkpoint
        verifier = WorkshopCacheVerifier(self.cache)
        proofs = {item: verifier.verify(item) for item in ("111", "222")}

        def checkpoint(phase: str, index: int) -> None:
            """Rewrite the manifest when the final cache proof is rechecked."""
            if (phase, index) == ("CACHE_PROOF_RECHECK", 0):
                manifest = self.base / "steamapps/workshop/appworkshop_221100.acf"
                manifest.write_text(
                    manifest.read_text(encoding="utf-8").replace(
                        '"manifest" "1"', '"manifest" "3"'),
                    encoding="utf-8",
                )

        # The mutated cache must fail the publication closed
        with self.assertRaises(PublicationInventoryError) as raised:
            build_publication_intent(
                publication_id="publication-1", profile=profile(), settings_revision=2,
                dayz_root_identity="d" * 64, cache_root=self.cache, proofs=proofs,
                checkpoint=checkpoint,
            )
        self.assertEqual(raised.exception.code, "CACHE_VERIFICATION_FAILED")


if __name__ == "__main__":
    unittest.main()
