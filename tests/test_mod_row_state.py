"""Row-state rule and target-proof lookup: one case per table row."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.adapters.windows.publication_paths import dayz_root_identity  # noqa: E402
from dayz_serverman.application.target_proofs import TargetProofLookup  # noqa: E402
from dayz_serverman.domain.mod_row_state import (  # noqa: E402
    RowInputs,
    RowState,
    TargetProof,
    row_state,
)
from dayz_serverman.domain.update_check import RemoteFact, RemoteItemResult  # noqa: E402
from dayz_serverman.domain.update_check_rules import CheckState  # noqa: E402
from dayz_serverman.repositories.applied_mod_state import (  # noqa: E402
    AppliedModStateRepository,
)

CHECKED = "2026-10-03T12:00:00.000+00:00"
LOCAL_TIME = 500


def fact(time_updated: int = LOCAL_TIME, result=RemoteItemResult.OK) -> RemoteFact:
    """Return a remote fact; only an OK fact carries a time."""
    if result is not RemoteItemResult.OK:
        return RemoteFact(result, None, None, CHECKED)
    return RemoteFact(result, time_updated, 4096, CHECKED)


def derive(**changes: object) -> RowState:
    """Derive a row from defaults that describe a current, proven Workshop mod."""
    values = {
        "source_kind": "workshop", "cache_readable": True, "installed_manifest_id": "8",
        "latest_manifest_id": None, "local_time_updated": LOCAL_TIME, "fact": fact(),
        "check_state": CheckState.OK, "target": TargetProof.PROVEN,
    }
    return row_state(RowInputs(**{**values, **changes}))


class RowStateTests(unittest.TestCase):
    """The state table, first match wins, with its additive fields."""

    def test_rows_one_to_three_have_no_remote_comparison(self) -> None:
        """Local, unavailable and not-downloaded rows report NOT_APPLICABLE."""
        cases = (
            ("LOCAL", {"source_kind": "external"}),
            ("UNAVAILABLE", {"cache_readable": False}),
            ("NOT_DOWNLOADED", {"installed_manifest_id": None}),
            # QF-054: a manifest record whose content folder is missing is not downloaded
            ("NOT_DOWNLOADED", {"content_present": False}),
        )
        for state, changes in cases:
            # A newer fact and a missing target must not outrank these rows
            row = derive(fact=fact(LOCAL_TIME + 1), target=TargetProof.MISSING, **changes)
            self.assertEqual(row, RowState(state, None, "NOT_APPLICABLE", None), state)

    def test_update_available_from_manifest_or_newer_remote_time(self) -> None:
        """A differing latest manifest or a newer remote time reports an update."""
        self.assertEqual(derive(latest_manifest_id="9").state, "UPDATE_AVAILABLE")
        newer = derive(fact=fact(LOCAL_TIME + 1), target=TargetProof.MISSING)
        self.assertEqual(newer, RowState("UPDATE_AVAILABLE", LOCAL_TIME + 1, "OK", None))

    def test_stale_or_failed_fact_still_gives_update_available(self) -> None:
        """A newer remote time counts even when the check is stale or failed."""
        for state in (CheckState.STALE, CheckState.FAILED):
            row = derive(fact=fact(LOCAL_TIME + 1), check_state=state)
            self.assertEqual((row.state, row.remote_check), ("UPDATE_AVAILABLE", state.value))

    def test_unproven_or_missing_target_is_pending_apply(self) -> None:
        """Downloaded content without a proven copy is not applied, with its reason."""
        unproven = derive(target=TargetProof.UNPROVEN)
        missing = derive(target=TargetProof.MISSING, check_state=CheckState.FAILED)
        self.assertEqual(unproven, RowState("PENDING_APPLY", LOCAL_TIME, "OK", "TARGET_UNPROVEN"))
        self.assertEqual((missing.state, missing.pending_reason),
                         ("PENDING_APPLY", "TARGET_MISSING"))

    def test_current_needs_a_fresh_equal_remote_answer(self) -> None:
        """Check OK, fact OK and equal times report CURRENT, proven or unknown target."""
        for target in (TargetProof.PROVEN, TargetProof.UNKNOWN):
            self.assertEqual(derive(target=target), RowState("CURRENT", LOCAL_TIME, "OK", None))

    def test_without_a_check_source_the_manifest_comparison_decides(self) -> None:
        """No check source wired: today's rule, and the remote fields stay absent."""
        current = derive(check_state=None, fact=None, latest_manifest_id="8")
        installed = derive(check_state=None, fact=None)
        local = derive(check_state=None, fact=None, source_kind="external")
        self.assertEqual(current, RowState("CURRENT"))
        self.assertEqual(installed, RowState("INSTALLED"))
        self.assertEqual(local, RowState("LOCAL"))

    def test_unverified_item_is_installed_never_current(self) -> None:
        """Every unverified combination reads INSTALLED with its remote-check value."""
        cases = (
            ("NEVER", {"fact": None, "check_state": CheckState.NEVER}),
            ("NEVER", {"fact": None}),
            ("UNKNOWN_ITEM", {"fact": fact(result=RemoteItemResult.NOT_FOUND)}),
            ("UNKNOWN_ITEM", {"fact": fact(result=RemoteItemResult.WRONG_APP)}),
            ("FAILED", {"check_state": CheckState.FAILED}),
            ("STALE", {"check_state": CheckState.STALE}),
            ("OK", {"fact": fact(LOCAL_TIME - 1)}),
            ("OK", {"local_time_updated": None}),
        )
        for remote_check, changes in cases:
            # An equal latest manifest must not make an unverified row current
            row = derive(latest_manifest_id="8", **changes)
            self.assertEqual((row.state, row.remote_check), ("INSTALLED", remote_check), changes)
        self.assertIsNone(derive(fact=fact(result=RemoteItemResult.INVALID)).remote_time_updated)


class RecordSource:
    """Target record source with a fixed record set, or a failure."""

    def __init__(self, records=(), error: Exception | None = None) -> None:
        """Store the records to return or the error to raise."""
        self.records, self.error = set(records), error

    def target_records(self):
        """Return the records, or raise the prepared error."""
        if self.error is not None:
            raise self.error
        return self.records


class TargetProofTests(unittest.TestCase):
    """The T table: unknown, proven, unproven and missing."""

    def setUp(self) -> None:
        """Create a server folder with one applied mod directory."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "DayZ Server"
        (self.root / "@Applied").mkdir(parents=True)
        self.identity = dayz_root_identity(self.root)
        self.items = [("@Applied", "111", "8"), ("@Absent", "222", "9")]

    def resolve(self, sources, root: object = "default"):
        """Resolve both items against the given sources."""
        chosen = str(self.root) if root == "default" else root
        return TargetProofLookup(sources).resolve(chosen, self.items)

    def test_unknown_without_a_source_or_a_usable_root(self) -> None:
        """No wired source, no root or an unusable root gives UNKNOWN for every item."""
        unknown = {"@Applied": TargetProof.UNKNOWN, "@Absent": TargetProof.UNKNOWN}
        self.assertEqual(self.resolve(()), unknown)
        self.assertEqual(self.resolve([RecordSource()], None), unknown)
        self.assertEqual(self.resolve([RecordSource()], str(self.root / "nowhere")), unknown)
        self.assertEqual(self.resolve([RecordSource()], "relative"), unknown)

    def test_matching_record_proves_and_the_directory_decides_otherwise(self) -> None:
        """A record proves the copy; else an existing directory is unproven, an absent one missing."""
        record = (self.identity, "@applied", "111", "8")
        self.assertEqual(self.resolve([RecordSource([record])]),
                         {"@Applied": TargetProof.PROVEN, "@Absent": TargetProof.MISSING})
        # Another manifest id, Workshop id or root identity proves nothing
        for other in ((self.identity, "@applied", "111", "7"),
                      (self.identity, "@applied", "999", "8"), ("0" * 64, "@applied", "111", "8")):
            self.assertEqual(self.resolve([RecordSource([other])])["@Applied"],
                             TargetProof.UNPROVEN)

    def test_first_source_may_fail_and_a_later_source_still_proves(self) -> None:
        """A failing source contributes nothing; the next source is still read."""
        record = (self.identity, "@absent", "222", "9")
        result = self.resolve([RecordSource(error=OSError()), RecordSource([record])])
        self.assertEqual(result["@Absent"], TargetProof.PROVEN)

    def test_legacy_record_of_another_profile_gives_proven(self) -> None:
        """The legacy lookup ignores the profile id and the profile digest."""
        path = Path(self.temporary.name) / "applied-mod-state.json"
        path.write_text(json.dumps({"schema_version": 1, "profiles": {
            "other-profile": {"mods": {"111": {
                "semantic_profile_digest": "f" * 64, "dayz_root_identity": self.identity,
                "target_relative": "@APPLIED", "installed_manifest_id": "8",
            }, "333": "malformed"}},
            "broken": "not-a-section",
        }}), encoding="utf-8")
        legacy = AppliedModStateRepository(path)
        self.assertEqual(legacy.target_records(), {(self.identity, "@applied", "111", "8")})
        self.assertEqual(self.resolve([legacy])["@Applied"], TargetProof.PROVEN)
        # A corrupt or missing legacy file is "no proof", never an error
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.resolve([legacy])["@Applied"], TargetProof.UNPROVEN)
        path.unlink()
        self.assertEqual(legacy.target_records(), frozenset())


if __name__ == "__main__":
    unittest.main()
