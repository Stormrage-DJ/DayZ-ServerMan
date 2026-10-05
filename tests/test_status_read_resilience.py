"""QF-028: status and inventory reads degrade, and store writes never fail, under concurrency."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import VERIFIED_AT  # noqa: E402
from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity  # noqa: E402
from dayz_serverman.application.mod_inventory import ModInventoryService  # noqa: E402
from dayz_serverman.application.mod_inventory_coordinator import (  # noqa: E402
    ModInventoryCoordinator,
)
from dayz_serverman.application.mod_publication_prestart import PrestartCheck  # noqa: E402
from dayz_serverman.application.target_proofs import TargetProofLookup  # noqa: E402
from dayz_serverman.application.update_check_coordinator import (  # noqa: E402
    UpdateCheckCoordinator,
)
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.domain.content_proofs import TargetProofRecord  # noqa: E402
from dayz_serverman.domain.models import RecordInspection, RecordState, RecordUnavailable  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.repositories import content_proofs as proof_module  # noqa: E402
from dayz_serverman.repositories.applied_mod_state import AppliedModStateRepository  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.tree_metadata import has_alternate_stream  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402
from profile_fixtures import profile_payload  # noqa: E402
from update_check_fixtures import REMOTE_TIME, Harness  # noqa: E402

# Number of Workshop mods in the profile under test
MOD_COUNT = 6
# Duration of each concurrency run in seconds
RUN_SECONDS = 1.5


class _Logger:
    """Collect emitted events."""

    def __init__(self) -> None:
        """Start with no events."""
        self.events: list[str] = []

    def emit(self, event: str, *, level: str = "INFO", fields=None) -> None:
        """Record one event name."""
        self.events.append(event)


class StatusReadResilienceTests(unittest.TestCase):
    """A reader error means "no proof" for that read; a failed store write is only logged."""

    def setUp(self) -> None:
        """Build a profile, its Workshop cache, the real stores and the bridge under test."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.cache_root = base / "steamapps/workshop/content/221100"
        self.cache_root.mkdir(parents=True)
        ids = [str(1000 + number) for number in range(MOD_COUNT)]
        # A downloaded item has its content folder (QF-054)
        for key in ids:
            (self.cache_root / key).mkdir()
        installed = " ".join(
            f'"{key}" {{ "manifest" "8" "size" "1" "timeupdated" "{REMOTE_TIME}" }}'
            for key in ids)
        self.manifest = base / "steamapps/workshop/appworkshop_221100.acf"
        self.manifest.write_text(
            '"AppWorkshop" { "appid" "221100" "WorkshopItemsInstalled" { ' + installed
            + ' } "WorkshopItemDetails" { } }', encoding="utf-8")
        dayz = base / "DayZ Server"
        for key in ids:
            (dayz / f"@Mod{key}").mkdir(parents=True)
        profile = ProfileRecord(1, ProfileInput.parse(profile_payload(profile_id="main", mods=[
            {"directory": f"@Mod{key}", "launch_scope": "client",
             "source": {"kind": "workshop", "workshop_id": key}} for key in ids])))
        self.settings = SimpleNamespace(load=lambda: SimpleNamespace(
            workshop_content_root=str(self.cache_root), dayz_root=str(dayz)))
        # The real stores, as the production wiring reads them
        self.logger = _Logger()
        self.store = ContentProofStore(base / "data" / "content-proofs.json", self.logger)
        self.legacy = AppliedModStateRepository(base / "data" / "applied-mod-state.json")
        identity = dayz_root_identity(dayz)
        self.targets = {
            (identity, f"@Mod{key}"): TargetProofRecord(
                key, "8", "a" * 64, "b" * 64, VERIFIED_AT, "PRESTART") for key in ids}
        self.assertTrue(self.store.record(targets=self.targets))
        harness = Harness(ids=ids)
        inventory = ModInventoryService(
            SimpleNamespace(read=lambda _profile_id: profile), self.settings,
            check_source=harness.service,
            target_proofs=TargetProofLookup([self.store, self.legacy]))
        self.bridge = BridgeFacade({
            **UpdateCheckCoordinator(harness.service, inventory).handlers(),
            **ModInventoryCoordinator(inventory).handlers(),
        })

    def call(self, method: str) -> dict:
        """Dispatch one read for the profile and return its result envelope."""
        return self.bridge.dispatch({
            "contract_version": 1, "request_id": "read-1",
            "method": method, "parameters": {"profile_id": "main"},
        })

    def pending(self) -> int:
        """Return the "downloaded - not applied" count of a successful status read."""
        status = self.call("get_update_status")
        self.assertIsNone(status.get("error"), status)
        return status["value"]["mods"]["pending_apply_count"]

    def read_until(self, deadline: float, failures: list) -> None:
        """Read status and inventory until the deadline; collect every failed envelope."""
        while time.monotonic() < deadline:
            for method in ("get_update_status", "list_mod_inventory"):
                result = self.call(method)
                if result.get("error") is not None:
                    failures.append((method, result["error"]["code"]))

    def test_concurrent_stream_checks_never_raise(self) -> None:
        """Root cause: the stream check is safe when several threads run it at once."""
        errors: list[BaseException] = []
        deadline = time.monotonic() + RUN_SECONDS

        def work() -> None:
            """Check the manifest for alternate streams until the deadline."""
            try:
                while time.monotonic() < deadline:
                    self.assertFalse(has_alternate_stream(self.manifest))
            except BaseException as error:  # noqa: BLE001 - the test reports any error type
                errors.append(error)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_reads_survive_a_store_that_is_replaced_in_a_tight_loop(self) -> None:
        """Three readers and one writer of the proof store: no bridge call fails."""
        stop = threading.Event()
        failures: list = []

        def write() -> None:
            """Rewrite the store file until the readers are done."""
            while not stop.is_set():
                self.store.record(targets=self.targets)

        writer = threading.Thread(target=write)
        deadline = time.monotonic() + RUN_SECONDS
        readers = [threading.Thread(target=self.read_until, args=(deadline, failures))
                   for _ in range(3)]
        writer.start()
        for reader in readers:
            reader.start()
        for reader in readers:
            reader.join()
        stop.set()
        writer.join()
        self.assertEqual(failures, [])
        # After the run the store is intact and every target is proven again
        self.assertEqual(self.pending(), 0)

    def test_a_failing_store_read_means_no_proof(self) -> None:
        """A sharing violation, a missing file and broken JSON give "not applied", never an error."""
        for error in (PermissionError(13, "sharing violation"), FileNotFoundError(2, "gone")):
            with self.subTest(error=type(error).__name__):
                with patch.object(ContentProofStore, "_read", side_effect=error):
                    self.assertEqual(self.pending(), MOD_COUNT)
                    self.assertIsNone(self.call("list_mod_inventory").get("error"))
        with patch.object(proof_module.json, "loads",
                          side_effect=json.JSONDecodeError("broken", "{", 0)):
            self.assertEqual(self.pending(), MOD_COUNT)
        self.assertEqual(self.pending(), 0)

    def test_a_sharing_violation_on_read_is_retried_once(self) -> None:
        """One short retry hides a replace that is in progress."""
        real = ContentProofStore._read
        calls: list[int] = []

        def flaky(store: ContentProofStore) -> bytes:
            """Fail the first read with a sharing violation, then read normally."""
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(13, "sharing violation")
            return real(store)

        with patch.object(ContentProofStore, "_read", flaky):
            self.assertEqual(len(self.store.load().targets), MOD_COUNT)
        self.assertEqual(len(calls), 2)

    def test_a_failing_legacy_read_means_no_record(self) -> None:
        """The legacy applied-state file degrades the same way."""
        self.store._path.unlink()
        for error in (PermissionError(13, "locked"), FileNotFoundError(2, "gone"),
                      json.JSONDecodeError("broken", "{", 0)):
            with self.subTest(error=type(error).__name__):
                with patch.object(AppliedModStateRepository, "_load", side_effect=error):
                    self.assertEqual(self.pending(), MOD_COUNT)

    def test_an_unreadable_manifest_makes_the_rows_unavailable(self) -> None:
        """An operating-system error in the cache observation is not a failed call."""
        for error in (PermissionError(13, "locked"), FileNotFoundError(2, "gone")):
            with self.subTest(error=type(error).__name__):
                with patch.object(WorkshopCacheVerifier, "observe", side_effect=error):
                    rows = self.call("list_mod_inventory")
                    self.assertIsNone(rows.get("error"), rows)
                    self.assertEqual({row["state"] for row in rows["value"]}, {"UNAVAILABLE"})
                    self.assertEqual(self.pending(), 0)

    def test_unreadable_settings_are_a_storage_failure_not_an_internal_one(self) -> None:
        """A settings record that cannot be read maps to the designed error code."""
        unavailable = RecordUnavailable(RecordInspection(RecordState.CORRUPT, Path("manager.json")))
        self.settings.load = lambda: (_ for _ in ()).throw(unavailable)
        for method in ("get_update_status", "list_mod_inventory"):
            with self.subTest(method=method):
                self.assertEqual(self.call(method)["error"]["code"], "STORAGE_FAILURE")

    def test_a_write_that_meets_a_reader_is_retried_and_never_raises(self) -> None:
        """A replace refused by an open reader handle is retried, then logged; nothing is raised."""
        real = proof_module.os.replace
        attempts: list[int] = []

        def refused_twice(source, target) -> None:
            """Refuse the first two replaces the way an open reader handle does."""
            attempts.append(1)
            if len(attempts) <= 2:
                raise PermissionError(13, "Access is denied")
            real(source, target)

        with patch.object(proof_module.os, "replace", refused_twice):
            self.assertTrue(self.store.record(targets=self.targets))
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.logger.events, [])
        # A replace that never succeeds costs the proof only
        with patch.object(proof_module.os, "replace",
                          side_effect=PermissionError(13, "Access is denied")):
            self.assertFalse(self.store.record(targets=self.targets))
            # The pre-start records of a publication take the same path and stay silent
            check = PrestartCheck(self.store, None)
            check._proven = dict(self.targets)
            check.record()
        self.assertEqual(self.logger.events, ["content_proofs.write_failed"] * 2)
        self.assertEqual(list(self.store._path.parent.glob(".*.tmp")), [])
        self.assertEqual(self.pending(), 0)

    def test_a_real_open_reader_does_not_fail_the_write(self) -> None:
        """With the file held open by a reader, the write returns without raising."""
        with self.store._path.open("rb"):
            result = self.store.record(targets=self.targets)
        self.assertIn(result, (True, False))
        self.assertTrue(self.store.record(targets=self.targets))
        self.assertEqual(self.pending(), 0)


if __name__ == "__main__":
    unittest.main()
