"""Update operation: the `download` checkpoint, the item phases and the observer around the run."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import build_cache  # noqa: E402
from test_content_proof_reuse import _SteamCmd, profile_record  # noqa: E402
from test_workshop_decision_update import _Check  # noqa: E402
from dayz_serverman.adapters.windows.steamcmd import (  # noqa: E402
    SteamCmdPaths, SteamCmdRunResult,
)
from dayz_serverman.application.workshop_coordinator import WorkshopCoordinator  # noqa: E402
from dayz_serverman.application.workshop_updates import (  # noqa: E402
    UpdateRequest, WorkshopUpdateService,
)
from dayz_serverman.domain.workshop import AuthenticationMode  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402


class _Context:
    """Operation context stand-in that records checkpoints and every offered detail value."""

    cancellation_requested = False

    def __init__(self) -> None:
        """Start with empty records."""
        self.phases: list[str] = []
        self.details: list[list[dict[str, object]]] = []

    def checkpoint(self, phase: str, _percent: int) -> None:
        """Record the phase."""
        self.phases.append(phase)

    def publish_detail(self, items) -> None:
        """Record a copy of the offered item list."""
        self.details.append([dict(item) for item in items])

    def record_evidence(self, _evidence) -> None:
        """Accept the child evidence."""


class UpdateItemPhaseTests(unittest.TestCase):
    """Design 13.6: each sent item is queued, every item is verified, the end is done or failed."""

    def setUp(self) -> None:
        """Create a cache with three items and an update service with a recording observer."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.cache = build_cache(self.base, {"333": b"charlie", "111": b"alpha", "222": b"bravo"})
        self.record = profile_record(
            "main", 2302, {"@Charlie": "333", "@Alpha": "111", "@Bravo": "222"})
        self.settings = SimpleNamespace(
            revision=1, steam_authentication_mode="ANONYMOUS", steam_account_name=None,
            dayz_root=None, workshop_content_root=str(self.cache))
        self.steamcmd = _SteamCmd()
        self.check = _Check()
        self.events: list[object] = []
        self.context = _Context()
        self.service = self.build(self.observer)

    def build(self, observer) -> WorkshopUpdateService:
        """Return the update service with the given observer factory."""
        return WorkshopUpdateService(
            SimpleNamespace(read=lambda _profile_id: self.record),
            SimpleNamespace(load=lambda: self.settings),
            SimpleNamespace(
                inspect=lambda _settings: SteamCmdPaths(self.base, self.base / "steamcmd.exe", self.cache),
                revalidate=lambda _paths: None),
            self.steamcmd, WorkshopCacheVerifier, check_source=self.check,
            download_observer=observer,
        )

    def observer(self, root, progress, ids):
        """Build an observer stand-in that records its arguments and its lifetime."""
        self.events.append(("observer", root, tuple(ids)))
        test = self

        class _Observer:
            """Context manager that samples one folder size when the run starts."""

            def __enter__(self) -> None:
                """Record the start and apply one sample."""
                test.events.append("enter")
                progress.sample(ids[0], 3)

            def __exit__(self, *_error) -> None:
                """Record the stop."""
                test.events.append("exit")

        return _Observer()

    def update(self) -> dict[str, object]:
        """Run one update with the recording context."""
        self.steamcmd.during_run = lambda: self.events.append("run")
        return self.service.update(UpdateRequest(
            "main", self.record.revision, self.record.semantic_digest, 1,
            AuthenticationMode.ANONYMOUS, None), self.context)

    def phases_of(self, workshop_id: str) -> list[str]:
        """Return the distinct phases of one item in publication order."""
        seen: list[str] = []
        for items in self.context.details:
            for item in items:
                if item["workshop_id"] == workshop_id and (not seen or seen[-1] != item["phase"]):
                    seen.append(item["phase"])
        return seen

    def test_mixed_run_queues_only_sent_items_with_their_sizes(self) -> None:
        """Changed items wait, download and verify; the unchanged item appears at its proof step."""
        self.check.times = {"333": 999, "111": 100, "222": 999}
        result = self.update()
        self.assertEqual(result["download_state"], "VERIFIED")
        self.assertEqual(self.context.details[0], [
            {"workshop_id": "333", "phase": "queued", "done_bytes": None, "total_bytes": 5},
            {"workshop_id": "222", "phase": "queued", "done_bytes": None, "total_bytes": 5}])
        self.assertEqual(self.phases_of("333"), ["queued", "downloading", "verifying", "done"])
        self.assertEqual(self.phases_of("222"), ["queued", "verifying", "done"])
        self.assertEqual(self.phases_of("111"), ["verifying", "done"])
        self.assertEqual({item["phase"] for item in self.context.details[-1]}, {"done"})
        # The observer is built for the sent items and lives exactly around the SteamCMD run
        self.assertEqual(self.events, [("observer", self.cache, ("333", "222")), "enter", "run", "exit"])
        phases = self.context.phases
        self.assertLess(phases.index("check_remote"), phases.index("download"))
        self.assertLess(phases.index("download"), phases.index("verify_items"))

    def test_failed_check_queues_every_item_without_a_size(self) -> None:
        """Without facts every item is sent and no total is known."""
        self.update()
        self.assertEqual([(item["workshop_id"], item["phase"], item["total_bytes"])
                          for item in self.context.details[0]],
                         [("333", "queued", None), ("111", "queued", None), ("222", "queued", None)])

    def test_unchanged_run_has_no_download_phase_and_no_observer(self) -> None:
        """Nothing sent: no `download` checkpoint, no observer, entries only from the proof step."""
        self.check.times = {"333": 100, "111": 100, "222": 100}
        self.update()
        self.assertEqual((self.events, self.steamcmd.runs), ([], 0))
        self.assertNotIn("download", self.context.phases)
        self.assertEqual(self.context.details[0], [])
        self.assertEqual(self.phases_of("333"), ["verifying", "done"])

    def test_update_runs_without_an_observer(self) -> None:
        """The fallback wiring without the observer keeps the item phases."""
        self.service = self.build(None)
        self.assertEqual(self.update()["download_state"], "VERIFIED")
        self.assertEqual(self.phases_of("333"), ["queued", "verifying", "done"])

    def test_failed_and_cancelled_runs_end_every_entry_as_failed(self) -> None:
        """A run without a success leaves no entry in a working phase."""
        for name, run in (("failed", SteamCmdRunResult(1, (), False, True, 77)),
                          ("cancelled", SteamCmdRunResult(1, (), True, True, 77))):
            with self.subTest(name=name):
                self.context = _Context()
                self.steamcmd.run_update = lambda *_arguments, value=run: value
                result = self.update()
                self.assertNotEqual(result["download_state"], "VERIFIED")
                self.assertEqual({item["phase"] for item in self.context.details[-1]}, {"failed"})
                self.assertEqual(len(self.context.details[-1]), 3)

    def test_download_is_a_safe_point_of_the_operation(self) -> None:
        """The coordinator declares `download` as a point where a cancellation stops the update."""
        declared: dict[str, object] = {}

        def submit(kind, _work, **options):
            """Record the submission options."""
            declared.update(kind=kind, **options)
            return SimpleNamespace(operation_id="operation-1", state=SimpleNamespace(value="QUEUED"))

        settings = SimpleNamespace(load=lambda: self.settings)
        coordinator = WorkshopCoordinator(self.service, settings, SimpleNamespace(submit=submit))
        coordinator.update_workshop_items({
            "profile_id": "main", "expected_profile_revision": 1,
            "expected_semantic_profile_digest": "a" * 64, "expected_settings_revision": 1,
            "authentication_mode": "ANONYMOUS", "account_name": None,
            "update_all_and_start": False})
        self.assertEqual(declared["kind"], "UPDATE_WORKSHOP_ITEMS")
        self.assertIn("download", declared["safe_points"])
        self.assertNotIn("VERIFY_BEFORE_START", declared["safe_points"])


if __name__ == "__main__":
    unittest.main()
