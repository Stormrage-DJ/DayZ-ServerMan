"""Bridge calls of the update check: status, check request and composed wiring."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.mod_inventory import ModInventoryService  # noqa: E402
from dayz_serverman.application.target_proofs import TargetProofLookup  # noqa: E402
from dayz_serverman.application.update_check_coordinator import (  # noqa: E402
    UpdateCheckCoordinator,
)
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.domain.update_check import RemoteBatchFailure  # noqa: E402
from dayz_serverman.repositories.profiles import (  # noqa: E402
    ProfileNotFound,
    ProfileStorageError,
)
from dayz_serverman.update_check_composition import configured_workshop_ids  # noqa: E402
from tests.profile_fixtures import profile_payload  # noqa: E402
from tests.update_check_fixtures import REMOTE_TIME, Harness  # noqa: E402

# Number of Workshop mods in the measured profile
MOD_COUNT = 100


class _Profiles:
    """Profile reader with one known profile and prepared failures."""

    def __init__(self, profile: ProfileRecord) -> None:
        """Store the only readable profile."""
        self.profile = profile

    def read(self, profile_id):
        """Return the profile, or fail the way real storage does."""
        if profile_id == "broken":
            raise ProfileStorageError("profile record is unavailable: CORRUPT")
        if profile_id != self.profile.values.profile_id:
            raise ProfileNotFound("profile was not found")
        return self.profile


class UpdateCheckBridgeTests(unittest.TestCase):
    """Contracts of get_update_status and request_update_check."""

    def setUp(self) -> None:
        """Build a 100-mod profile, its Workshop cache and the bridge under test."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        root = base / "steamapps/workshop/content/221100"
        root.mkdir(parents=True)
        self.content_root = root
        ids = [str(1000 + number) for number in range(MOD_COUNT)]
        # A downloaded item has its content folder; without it the row reads "Not downloaded" (QF-054)
        for key in ids:
            (root / key).mkdir()
        # Every item is installed with the remote time; the first one is older
        installed = " ".join(
            f'"{key}" {{ "manifest" "8" "size" "1" "timeupdated" '
            f'"{REMOTE_TIME - (1 if key == ids[0] else 0)}" }}' for key in ids)
        (base / "steamapps/workshop/appworkshop_221100.acf").write_text(
            '"AppWorkshop" { "appid" "221100" "WorkshopItemsInstalled" { ' + installed
            + ' } "WorkshopItemDetails" { "1000" { "manifest" "8" } } }', encoding="utf-8")
        dayz = base / "DayZ Server"
        # Every mod but the second has a server-folder copy
        for key in ids[:1] + ids[2:]:
            (dayz / f"@Mod{key}").mkdir(parents=True)
        profile = ProfileRecord(1, ProfileInput.parse(profile_payload(profile_id="main", mods=[
            {"directory": f"@Mod{key}", "launch_scope": "client",
             "source": {"kind": "workshop", "workshop_id": key}} for key in ids])))
        settings = SimpleNamespace(load=lambda: SimpleNamespace(
            workshop_content_root=str(root), dayz_root=str(dayz)))
        self.harness = Harness(ids=ids, threaded=True)
        self.inventory = ModInventoryService(
            _Profiles(profile), settings, check_source=self.harness.service,
            target_proofs=TargetProofLookup([SimpleNamespace(target_records=lambda: set())]))
        coordinator = UpdateCheckCoordinator(self.harness.service, self.inventory)
        self.bridge = BridgeFacade(coordinator.handlers())

    def call(self, method: str, parameters: dict) -> dict:
        """Dispatch one request and return its result envelope."""
        return self.bridge.dispatch({
            "contract_version": 1, "request_id": "update-1",
            "method": method, "parameters": parameters,
        })

    def status(self) -> dict:
        """Return the update status value of the measured profile."""
        result = self.call("get_update_status", {"profile_id": "main"})
        self.assertTrue(result["success"], result)
        return result["value"]

    def test_status_before_any_check(self) -> None:
        """Without an attempt the status is NEVER with the exact field set."""
        value = self.status()
        self.assertEqual(set(value), {"mods", "server_build", "checking", "revision"})
        self.assertEqual(value["mods"], {
            "check_state": "NEVER", "checked_at": None, "last_success_at": None,
            "error_code": None, "update_count": 0, "pending_apply_count": MOD_COUNT,
            "not_downloaded_count": 0,
        })
        self.assertEqual((value["server_build"], value["checking"], value["revision"]),
                         (None, False, 0))

    def test_a_missing_content_folder_is_counted_as_not_downloaded(self) -> None:
        """QF-054: a manifest record without its content folder is counted, so the badge shows it."""
        (self.content_root / "1003").rmdir()
        value = self.status()
        self.assertEqual((value["mods"]["not_downloaded_count"], value["mods"]["pending_apply_count"]),
                         (1, MOD_COUNT - 1))

    def test_request_runs_and_counts_agree_with_the_rows(self) -> None:
        """A forced request starts a run; the counts equal the inventory rows."""
        accepted = self.call("request_update_check", {"scope": "mods", "force": True})
        self.assertEqual(accepted["value"]["accepted"], True)
        self.assertTrue(self.harness.wait_idle())
        value = self.status()
        states = [row["state"] for row in self.inventory.list("main")]
        self.assertEqual(value["mods"]["check_state"], "OK")
        self.assertEqual(value["mods"]["checked_at"], value["mods"]["last_success_at"])
        self.assertEqual(value["mods"]["update_count"], states.count("UPDATE_AVAILABLE"))
        self.assertEqual(value["mods"]["pending_apply_count"], states.count("PENDING_APPLY"))
        self.assertEqual(value["mods"]["not_downloaded_count"], states.count("NOT_DOWNLOADED"))
        self.assertEqual((value["mods"]["update_count"], value["revision"]), (1, 2))
        # A second forced request inside the debounce time starts nothing
        again = self.call("request_update_check", {"scope": "mods", "force": True})
        self.assertEqual(again["value"], {"accepted": False, "checking": False})

    def test_network_failure_is_a_status_never_a_bridge_error(self) -> None:
        """A failed check appears as check_state FAILED with its code."""
        self.harness.catalog.failures = [RemoteBatchFailure.NETWORK_UNREACHABLE]
        self.assertTrue(self.call(
            "request_update_check", {"scope": "mods", "force": False})["success"])
        self.assertTrue(self.harness.wait_idle())
        mods = self.status()["mods"]
        self.assertEqual((mods["check_state"], mods["error_code"], mods["last_success_at"]),
                         ("FAILED", "NETWORK_UNREACHABLE", None))
        self.assertIsNotNone(mods["checked_at"])

    def test_status_is_not_blocked_by_a_running_check(self) -> None:
        """While a 100-id run waits in the transport, the status returns at once."""
        release = self.harness.catalog.block()
        started = self.call("request_update_check", {"scope": "mods", "force": True})
        self.assertEqual(started["value"], {"accepted": True, "checking": True})
        self.assertTrue(self.harness.catalog.entered.wait(5))
        durations = []
        for _ in range(5):
            before = time.perf_counter()
            value = self.status()
            durations.append(time.perf_counter() - before)
            self.assertTrue(value["checking"])
        release.set()
        self.assertTrue(self.harness.wait_idle())
        self.assertEqual(len(self.harness.catalog.calls[0][0]), MOD_COUNT)
        # No call waits for the run; the best call meets the 100 ms target
        self.assertLess(max(durations), 2.0)
        self.assertLess(min(durations), 0.1)
        self.assertFalse(self.status()["checking"])

    def test_invalid_requests_and_profile_errors(self) -> None:
        """Wrong field sets, values and unknown or unreadable profiles are refused."""
        cases = (
            ("get_update_status", {}, "INVALID_REQUEST"),
            ("get_update_status", {"profile_id": "main", "force": True}, "INVALID_REQUEST"),
            ("get_update_status", {"profile_id": "../x"}, "INVALID_REQUEST"),
            ("get_update_status", {"profile_id": "missing"}, "NOT_FOUND"),
            ("get_update_status", {"profile_id": "broken"}, "STORAGE_FAILURE"),
            ("request_update_check", {"scope": "mods"}, "INVALID_REQUEST"),
            ("request_update_check", {"scope": "server", "force": True}, "INVALID_REQUEST"),
            ("request_update_check", {"scope": ["mods"], "force": True}, "INVALID_REQUEST"),
            ("request_update_check", {"scope": "mods", "force": 1}, "INVALID_REQUEST"),
            ("request_update_check",
             {"scope": "mods", "force": True, "profile_id": "main"}, "INVALID_REQUEST"),
        )
        for method, parameters, code in cases:
            result = self.call(method, parameters)
            self.assertFalse(result["success"], parameters)
            self.assertEqual(result["error"]["code"], code, parameters)
        self.assertEqual(self.harness.catalog.calls, [])


class ComposedUpdateCheckTests(unittest.TestCase):
    """The production wiring, proven without a network connection."""

    def setUp(self) -> None:
        """Build a composition in a temporary manager root and forbid connections."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_update_check_")
        self.composition = build_composition(Path(self.temporary.name) / "Manager")
        guard = patch("socket.create_connection", side_effect=AssertionError("no network"))
        guard.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.composition.operations.shutdown, 2)
        self.addCleanup(guard.stop)

    def dispatch(self, method: str, parameters: dict) -> dict:
        """Dispatch one request through the composed bridge."""
        return self.composition.bridge.dispatch({
            "contract_version": 1, "request_id": "update-1",
            "method": method, "parameters": parameters,
        })

    def wait_idle(self) -> None:
        """Wait until the composed service has no active run."""
        deadline = time.monotonic() + 5
        while self.composition.update_check.snapshot().checking:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)

    def test_forced_check_without_profiles_sends_nothing_and_writes_the_cache(self) -> None:
        """Zero configured ids give an OK check, a cache file and a readable status."""
        started = self.dispatch("request_update_check", {"scope": "mods", "force": True})
        self.assertTrue(started["value"]["accepted"])
        self.wait_idle()
        self.assertTrue(self.composition.paths.update_check_cache.is_file())
        self.composition.profiles.save(ProfileInput.parse(profile_payload(mods=[])), None)
        status = self.dispatch("get_update_status", {"profile_id": "livonia-main"})
        self.assertEqual(status["value"]["mods"]["check_state"], "OK")
        self.assertEqual(status["value"]["mods"]["update_count"], 0)
        missing = self.dispatch("get_update_status", {"profile_id": "missing"})
        self.assertEqual(missing["error"]["code"], "NOT_FOUND")

    def test_configured_ids_skip_an_unreadable_profile(self) -> None:
        """One corrupt profile does not hide the Workshop ids of the others."""
        self.composition.profiles.save(ProfileInput.parse(profile_payload()), None)
        read = configured_workshop_ids(
            self.composition.profiles, self.composition.profile_repository)
        self.assertEqual(read(), frozenset({"1559212036"}))
        (self.composition.paths.profiles / "damaged.json").write_text("{broken", "utf-8")
        self.assertEqual(read(), frozenset({"1559212036"}))


if __name__ == "__main__":
    unittest.main()
