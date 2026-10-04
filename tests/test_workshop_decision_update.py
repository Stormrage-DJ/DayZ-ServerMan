"""Update operation with the decide rule: skipped runs, changed-only requests, the gate rule."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from content_proof_fixtures import build_cache, build_target, write_manifest  # noqa: E402
from test_content_proof_reuse import (  # noqa: E402
    FULL_HASH, _Context, _SteamCmd, profile_record,
)
from update_check_fixtures import Harness  # noqa: E402
from dayz_serverman.adapters.windows.steamcmd import (  # noqa: E402
    SteamCmdPaths, SteamCmdRunResult,
)
from dayz_serverman.application.content_proofs import ContentProofResolver  # noqa: E402
from dayz_serverman.application.mod_publication_gate import (  # noqa: E402
    PublicationGateError, parse_publication_gate,
)
from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.application.workshop_updates import (  # noqa: E402
    UpdateRequest, WorkshopUpdateService,
)
from dayz_serverman.domain.update_check import (  # noqa: E402
    RemoteBatchFailure, RemoteFact, RemoteItemResult, UpdateCheckRecord,
)
from dayz_serverman.domain.workshop import AuthenticationMode  # noqa: E402
from dayz_serverman.repositories.applied_mod_state import AppliedModStateRepository  # noqa: E402
from dayz_serverman.repositories.content_proofs import ContentProofStore  # noqa: E402
from dayz_serverman.repositories.workshop_cache import WorkshopCacheVerifier  # noqa: E402

# Fixed fields of the 14-field result
RESULT_FIELDS = {
    "profile_id", "profile_revision", "semantic_profile_digest", "settings_revision",
    "authentication_identity_digest", "download_state", "items", "publication_state",
    "start_requested", "start_authorized", "start_error", "process_id",
    "steamcmd_exit_code", "steamcmd_summary",
}


class _Check:
    """Fresh-check stand-in: the remote update time per item, or a failure."""

    def __init__(self) -> None:
        """Start with a failing check, which sends every item."""
        self.times: dict[str, int] | None = None
        self.calls = 0

    def check_now(self, ids):
        """Return an OK fact for every id with a configured time."""
        self.calls += 1
        if self.times is None:
            return {}
        return {key: RemoteFact(RemoteItemResult.OK, self.times[key], 5,
                                "2026-10-03T12:00:00.000+00:00")
                for key in ids if key in self.times}


class DecideBeforeSteamCmdTests(unittest.TestCase):
    """Criterion 20: no SteamCMD process and no full hash when nothing changed."""

    def setUp(self) -> None:
        """Create a cache with three items and the update service with a check source."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.cache = build_cache(self.base, {"333": b"charlie", "111": b"alpha", "222": b"bravo"})
        self.dayz = self.base / "DayZ Server"
        build_target(self.dayz, "@Alpha", b"alpha")
        self.record = profile_record(
            "main", 2302, {"@Charlie": "333", "@Alpha": "111", "@Bravo": "222"})
        self.settings = SimpleNamespace(
            revision=1, steam_authentication_mode="ANONYMOUS", steam_account_name=None,
            dayz_root=str(self.dayz), workshop_content_root=str(self.cache),
        )
        self.store = ContentProofStore(self.base / "data" / "content-proofs.json")
        self.steamcmd = _SteamCmd()
        self.argv: list[tuple[str, ...]] = []
        original = self.steamcmd.run_update

        def recording(paths, argv, *rest):
            """Record the command before the stand-in runs it."""
            self.argv.append(argv)
            return original(paths, argv, *rest)

        self.steamcmd.run_update = recording
        self.check = _Check()
        self.revalidations = 0
        executable = self.base / "steamcmd.exe"
        self.resolver = ContentProofResolver(
            self.store, AppliedModStateRepository(self.base / "data" / "legacy.json"))
        self.service = WorkshopUpdateService(
            SimpleNamespace(read=lambda _profile_id: self.record),
            SimpleNamespace(load=lambda: self.settings),
            SimpleNamespace(
                inspect=lambda _settings: SteamCmdPaths(self.base, executable, self.cache),
                revalidate=self._revalidate,
            ),
            self.steamcmd, WorkshopCacheVerifier,
            content_proofs=self.resolver, check_source=self.check,
        )

    def _revalidate(self, _paths) -> None:
        """Count the path revalidations before a launch."""
        self.revalidations += 1

    def update(self) -> dict[str, object]:
        """Run one update and return the whole result."""
        return self.service.update(UpdateRequest(
            "main", self.record.revision, self.record.semantic_digest, 1,
            AuthenticationMode.ANONYMOUS, None,
        ), _Context())

    def sent_ids(self) -> list[str]:
        """Return the item ids of the last SteamCMD command."""
        argv = self.argv[-1]
        return [argv[index + 2] for index, value in enumerate(argv)
                if value == "+workshop_download_item"]

    def gate(self, result: dict[str, object]):
        """Parse the result as the publication gate would."""
        return parse_publication_gate(
            SimpleNamespace(kind="UPDATE_WORKSHOP_ITEMS", state=OperationState.SUCCEEDED,
                            result=result),
            SimpleNamespace(
                profile_id="main", profile_revision=self.record.revision,
                semantic_profile_digest=self.record.semantic_digest, settings_revision=1),
            self.record, self.settings,
        )

    def test_check_failure_sends_all_items(self) -> None:
        """Without a fresh fact every item is sent, in profile order."""
        result = self.update()
        self.assertEqual(self.sent_ids(), ["333", "111", "222"])
        self.assertEqual(result["process_id"], 77)
        self.assertEqual(self.check.calls, 1)
        self.gate(result)

    def test_all_unchanged_starts_no_process_and_hashes_nothing(self) -> None:
        """A skipped run has null process fields, stored proofs, and passes the gate."""
        self.update()
        self.check.times = {"333": 100, "111": 100, "222": 100}
        runs, revalidations = self.steamcmd.runs, self.revalidations
        with patch(FULL_HASH, side_effect=AssertionError("full hash must not run")):
            result = self.update()
        # No process started and no path revalidation was needed
        self.assertEqual((self.steamcmd.runs, self.revalidations), (runs, revalidations))
        self.assertEqual(set(result), RESULT_FIELDS)
        self.assertEqual(
            (result["process_id"], result["steamcmd_exit_code"], result["steamcmd_summary"]),
            (None, None, None))
        self.assertEqual(result["download_state"], "VERIFIED")
        self.assertEqual(
            [(item["item"]["workshop_id"], item["outcome"],
              item["cache_proof"]["verification_kind"]) for item in result["items"]],
            [(key, "VERIFIED_CURRENT", "STORED_SOURCE") for key in ("333", "111", "222")])
        proofs, start = self.gate(result)
        self.assertEqual((list(proofs), start), (["333", "111", "222"], False))

    def test_null_process_fields_with_another_outcome_are_refused(self) -> None:
        """Null process evidence is valid only when every item is VERIFIED_CURRENT."""
        self.update()
        self.check.times = {"333": 100, "111": 100, "222": 100}
        result = self.update()
        for outcome in ("DOWNLOADED_VERIFIED", "UPDATED_VERIFIED"):
            with self.subTest(outcome=outcome):
                forged = copy.deepcopy(result)
                forged["items"][1]["outcome"] = outcome
                with self.assertRaises(PublicationGateError):
                    self.gate(forged)
        # Partly null process evidence is refused as well
        for field, value in (("steamcmd_exit_code", 0), ("steamcmd_summary", "text")):
            with self.subTest(field=field):
                forged = copy.deepcopy(result)
                forged[field] = value
                with self.assertRaises(PublicationGateError):
                    self.gate(forged)

    def test_mixed_set_sends_only_changed_ids_in_profile_order(self) -> None:
        """Only items with a newer remote time reach SteamCMD; the others stay current."""
        self.update()
        self.check.times = {"333": 200, "111": 100, "222": 300}
        # SteamCMD installs the new builds of the two sent items
        self.steamcmd.during_run = lambda: write_manifest(
            self.base, {"333": ("10", 200), "111": ("9", 100), "222": ("11", 300)})
        result = self.update()
        self.assertEqual(self.sent_ids(), ["333", "222"])
        self.assertEqual(
            [(item["item"]["workshop_id"], item["outcome"]) for item in result["items"]],
            [("333", "UPDATED_VERIFIED"), ("111", "VERIFIED_CURRENT"),
             ("222", "UPDATED_VERIFIED")])
        self.assertEqual((result["process_id"], result["steamcmd_exit_code"]), (77, 0))
        self.gate(result)

    def test_local_conditions_send_the_item(self) -> None:
        """Pending work in the manifest sends all; a missing directory sends that item."""
        self.update()
        self.check.times = {"333": 100, "111": 100, "222": 100}
        manifest = self.base / "steamapps" / "workshop" / "appworkshop_221100.acf"
        complete = manifest.read_text(encoding="utf-8")
        manifest.write_text(complete.replace('"NeedsDownload" "0"', '"NeedsDownload" "1"'),
                            encoding="utf-8")
        # SteamCMD finishes the pending work during the run
        self.steamcmd.during_run = lambda: manifest.write_text(complete, encoding="utf-8")
        self.update()
        self.assertEqual(self.sent_ids(), ["333", "111", "222"])
        self.steamcmd.during_run = None
        (self.cache / "222" / "Addons" / "mod.pbo").unlink()
        (self.cache / "222" / "Addons").rmdir()
        (self.cache / "222").rmdir()
        result = self.update()
        self.assertEqual(self.sent_ids(), ["222"])
        self.assertEqual(result["items"][2]["error_code"], "CACHE_VERIFICATION_FAILED")

    def test_failed_local_hash_on_a_skipped_run_is_a_failed_verification(self) -> None:
        """A read error during the local hash gives UNKNOWN_FAILED and a refused gate."""
        self.check.times = {"333": 100, "111": 100, "222": 100}
        with patch(FULL_HASH, side_effect=PermissionError("synthetic read error")):
            result = self.update()
        self.assertEqual(self.steamcmd.runs, 0)
        self.assertIsNone(result["process_id"])
        self.assertEqual(result["download_state"], "UNKNOWN")
        for item in result["items"]:
            self.assertEqual((item["outcome"], item["error_code"], item["cache_proof"]),
                             ("UNKNOWN_FAILED", "CACHE_VERIFICATION_FAILED", None))
        with self.assertRaises(PublicationGateError):
            self.gate(result)

    def test_skipped_run_without_a_stored_proof_hashes_locally(self) -> None:
        """An unchanged item without a stored proof is hashed once, without SteamCMD."""
        self.check.times = {"333": 100, "111": 100, "222": 100}
        result = self.update()
        self.assertEqual(self.steamcmd.runs, 0)
        self.assertEqual({item["cache_proof"]["verification_kind"] for item in result["items"]},
                         {"FULL_CONTENT"})
        self.assertEqual(set(self.store.load().sources), {"333", "111", "222"})
        self.gate(result)

    def test_cancelled_run_starts_no_full_hash_for_unsent_items(self) -> None:
        """After a cancel an unsent item keeps its outcome only through a stored proof."""
        self.steamcmd.run_update = lambda *_arguments: SteamCmdRunResult(1, (), True, True, 77)
        self.check.times = {"333": 200, "111": 100, "222": 300}
        # Without a stored proof the unsent item is cancelled like the sent ones
        with patch(FULL_HASH, side_effect=AssertionError("full hash must not run")):
            result = self.update()
        self.assertEqual([item["outcome"] for item in result["items"]], ["CANCELLED"] * 3)
        self.assertEqual(self.store.load().sources, {})
        # With a stored proof the unsent item stays verified, still without a hash
        self.resolver.record_full_hash(
            WorkshopCacheVerifier(self.cache), WorkshopCacheVerifier(self.cache).verify("111"))
        with patch(FULL_HASH, side_effect=AssertionError("full hash must not run")):
            result = self.update()
        self.assertEqual(
            [(item["outcome"], (item["cache_proof"] or {}).get("verification_kind"))
             for item in result["items"]],
            [("CANCELLED", None), ("VERIFIED_CURRENT", "STORED_SOURCE"), ("CANCELLED", None)])
        with self.assertRaises(PublicationGateError):
            self.gate(result)

    def test_clock_set_back_and_failed_check_sends_all_items(self) -> None:
        """Facts of an earlier check never skip SteamCMD after the clock moved backwards."""
        checks = Harness(ids=("333", "111", "222"))
        checks.catalog.times = {"333": 100, "111": 100, "222": 100}
        self.service._check_source = checks.service
        # The first check succeeds: nothing changed, so no process starts
        self.update()
        self.assertEqual(self.steamcmd.runs, 0)
        # The clock is corrected backwards and Steam is unreachable
        checks.clock.advance(-3600)
        checks.catalog.failures = [RemoteBatchFailure.NETWORK_UNREACHABLE]
        result = self.update()
        self.assertEqual(self.sent_ids(), ["333", "111", "222"])
        self.assertEqual((self.steamcmd.runs, result["process_id"]), (1, 77))

    def test_future_dated_cached_facts_and_failed_check_send_all_items(self) -> None:
        """Cached facts with a future time never skip SteamCMD while Steam is unreachable."""
        record = UpdateCheckRecord(None, None, {
            key: RemoteFact(RemoteItemResult.OK, 100, 5, "2099-01-01T00:00:00.000+00:00")
            for key in ("333", "111", "222")})
        checks = Harness(ids=("333", "111", "222"), record=record)
        checks.catalog.failures = [RemoteBatchFailure.NETWORK_UNREACHABLE] * 5
        self.service._check_source = checks.service
        result = self.update()
        self.assertEqual(self.sent_ids(), ["333", "111", "222"])
        self.assertEqual((self.steamcmd.runs, result["process_id"]), (1, 77))
        self.gate(result)


if __name__ == "__main__":
    unittest.main()
