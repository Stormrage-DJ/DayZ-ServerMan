"""Shared fixture of the "Verify files" tests: cache, server folder, service and context."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import build_cache, build_target  # noqa: E402
from test_content_proof_reuse import profile_record  # noqa: E402
from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity  # noqa: E402
from dayz_serverman.application.operations.models import OperationCancelled  # noqa: E402
from dayz_serverman.application.workshop_verification import (  # noqa: E402
    VerifyRequest, WorkshopVerificationService,
)
from dayz_serverman.domain.content_proofs import target_directory_key  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402

# Patch targets of the full source hash and of the target hash
SOURCE_HASH = "dayz_serverman.repositories.workshop_cache.WorkshopCacheVerifier._inventory"
TARGET_HASH = "dayz_serverman.application.workshop_verification.inventory_tree"


class FakeContext:
    """Operation context stand-in with scripted cancellation."""

    def __init__(self, cancel_phase: str | None = None, cancel_index: int = 0,
                 cancel_after_probes: int | None = None) -> None:
        """Cancel at the n-th checkpoint of a phase, or after a number of file probes."""
        self.cancel_phase, self.cancel_index = cancel_phase, cancel_index
        self.cancel_after_probes = cancel_after_probes
        self.checkpoints: list[tuple[str, int]] = []
        self.details: list[list[dict]] = []
        self.probes = 0

    @property
    def cancellation_requested(self) -> bool:
        """Count each probe; report cancellation once the scripted count is passed."""
        self.probes += 1
        return self.cancel_after_probes is not None and self.probes > self.cancel_after_probes

    def checkpoint(self, phase: str, percent: int) -> None:
        """Record the checkpoint and cancel at the scripted safe point."""
        seen = sum(1 for name, _percent in self.checkpoints if name == phase)
        self.checkpoints.append((phase, percent))
        if phase == self.cancel_phase and seen == self.cancel_index:
            raise OperationCancelled("synthetic cancellation")

    def publish_detail(self, items) -> None:
        """Keep a copy of every offered detail value."""
        self.details.append(copy.deepcopy(list(items)))


class VerifyFilesFixture(unittest.TestCase):
    """Cache with items 111 and 222, a server folder with both copies, and the service."""

    MODS = {"@Alpha": "111", "@Bravo": "222"}

    def setUp(self) -> None:
        """Create the trees, the settings, the profile and the service."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.cache = build_cache(self.base, {"111": b"alpha", "222": b"bravo"})
        self.dayz = self.base / "DayZ Server"
        build_target(self.dayz, "@Alpha", b"alpha")
        build_target(self.dayz, "@Bravo", b"bravo")
        self.record = profile_record("main", 2302, dict(self.MODS))
        self.settings = SimpleNamespace(
            revision=1, dayz_root=str(self.dayz), workshop_content_root=str(self.cache),
        )
        self.store = ContentProofStore(self.base / "data" / "content-proofs.json")
        self.service = WorkshopVerificationService(
            SimpleNamespace(read=lambda _profile_id: self.record),
            SimpleNamespace(load=lambda: self.settings), self.store,
        )

    def verify(self, context: FakeContext | None = None, **changes: int) -> dict[str, dict]:
        """Run one verification and return the result items by Workshop id."""
        values = {"expected_profile_revision": self.record.revision,
                  "expected_settings_revision": 1, **changes}
        result = self.service.verify(VerifyRequest("main", **values), context or FakeContext())
        self.assertEqual(set(result), {"items"})
        return {item["workshop_id"]: item for item in result["items"]}

    def states(self, items: dict[str, dict], workshop_id: str) -> tuple[str, str, str | None]:
        """Return (source_state, target_state, error_code) of one item."""
        item = items[workshop_id]
        return item["source_state"], item["target_state"], item["error_code"]

    def target_key(self, directory: str) -> tuple[str, str]:
        """Return the store key of a server-folder copy."""
        return dayz_root_identity(self.dayz), target_directory_key(directory)

    def tree_state(self) -> dict[str, tuple[int, int, bytes]]:
        """Return size, modification time and bytes of every file in both folders."""
        return {
            str(path): (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
            for root in (self.base / "steamapps", self.dayz)
            for path in sorted(root.rglob("*")) if path.is_file()
        }
