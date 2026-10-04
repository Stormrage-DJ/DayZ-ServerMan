"""Decide rule before SteamCMD: one case per "unchanged" condition, and the sent-set order."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.workshop_decision import (  # noqa: E402
    LocalCacheState,
    decide_sent_items,
    is_unchanged,
    items_to_send,
)
from dayz_serverman.domain.update_check import RemoteFact, RemoteItemResult  # noqa: E402
from dayz_serverman.domain.workshop import (  # noqa: E402
    RequiredWorkshopItem,
    WorkshopObservation,
)

# Time text of a fact; the decide rule never reads it
CHECKED_AT = "2026-10-03T12:00:00.000+00:00"


def fact(result: RemoteItemResult = RemoteItemResult.OK, time: int | None = 100) -> RemoteFact:
    """Return a remote fact with the given answer class and update time."""
    return RemoteFact(result, time, 5 if time is not None else None, CHECKED_AT)


def observation(
    workshop_id: str = "111", installed: str | None = "9", latest: str | None = "9",
    time: int | None = 100,
) -> WorkshopObservation:
    """Return a local observation; the defaults describe an installed, current item."""
    return WorkshopObservation(workshop_id, installed, latest, time, time)


def required(*workshop_ids: str) -> tuple[RequiredWorkshopItem, ...]:
    """Return the required items in the given profile order."""
    return tuple(RequiredWorkshopItem(workshop_id, index, "client")
                 for index, workshop_id in enumerate(workshop_ids))


class _Verifier:
    """Local cache stand-in with a configurable Steam state and directory set."""

    def __init__(self, complete: bool = True, directories: tuple[str, ...] = ()) -> None:
        """Store what the cache reports."""
        self.complete = complete
        self.directories = directories

    def steam_reports_complete(self) -> bool:
        """Return the configured app-level state."""
        return self.complete

    def source_directory_exists(self, workshop_id: str) -> bool:
        """Return whether the item directory is configured as present."""
        return workshop_id in self.directories


class _CheckSource:
    """Check source stand-in that returns fixed facts or raises."""

    def __init__(self, facts=None, error: Exception | None = None) -> None:
        """Store the facts to return and the optional error to raise."""
        self.facts = facts or {}
        self.error = error
        self.calls: list[tuple[str, ...]] = []

    def check_now(self, ids):
        """Record the requested ids and answer as configured."""
        self.calls.append(tuple(ids))
        if self.error is not None:
            raise self.error
        return self.facts


class UnchangedRuleTests(unittest.TestCase):
    """An item is unchanged only when all six conditions hold."""

    def unchanged(self, **changes: object) -> bool:
        """Evaluate the rule for a fully unchanged item with the given changes."""
        values = {"fact": fact(), "observation": observation(),
                  "steam_reports_complete": True, "source_directory_exists": True}
        values.update(changes)
        return is_unchanged(
            values["fact"], values["observation"],
            steam_reports_complete=values["steam_reports_complete"],
            source_directory_exists=values["source_directory_exists"],
        )

    def test_all_conditions_true_is_unchanged(self) -> None:
        """The baseline item is unchanged, also when no latest id is known."""
        self.assertTrue(self.unchanged())
        self.assertTrue(self.unchanged(observation=observation(latest=None)))

    def test_each_negated_condition_sends_the_item(self) -> None:
        """Negating any single condition makes the item changed."""
        cases = {
            "no fact": {"fact": None},
            "fact not found": {"fact": fact(RemoteItemResult.NOT_FOUND, None)},
            "fact wrong app": {"fact": fact(RemoteItemResult.WRONG_APP, None)},
            "fact invalid": {"fact": fact(RemoteItemResult.INVALID, None)},
            "remote time newer": {"fact": fact(time=101)},
            "remote time older": {"fact": fact(time=99)},
            "no observation": {"observation": None},
            "not installed": {"observation": observation(installed=None)},
            # No other condition fails here: only the missing installed record sends the item
            "no installed record at all": {
                "observation": observation(installed=None, latest=None)},
            "no local time": {"observation": observation(time=None)},
            "latest differs": {"observation": observation(latest="10")},
            "steam reports pending work": {"steam_reports_complete": False},
            "source directory absent": {"source_directory_exists": False},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                self.assertFalse(self.unchanged(**changes))


class SentSetTests(unittest.TestCase):
    """The sent set holds every changed item in profile order."""

    def setUp(self) -> None:
        """Describe three installed items with all directories present."""
        self.items = required("333", "111", "222")
        self.observations = {key: observation(key) for key in ("111", "222", "333")}
        self.verifier = _Verifier(True, ("111", "222", "333"))

    def sent(self, source) -> list[str]:
        """Return the ids of the sent set for the given check source."""
        return [item.workshop_id for item in decide_sent_items(
            source, self.items, self.observations, self.verifier)]

    def test_mixed_set_keeps_profile_order(self) -> None:
        """Only the changed items are sent, in the order of the profile."""
        facts = {"333": fact(time=200), "111": fact(), "222": fact(time=300)}
        self.assertEqual(self.sent(_CheckSource(facts)), ["333", "222"])
        local = LocalCacheState(self.observations, True, frozenset(("111", "222", "333")))
        self.assertEqual(items_to_send(self.items, facts, local), (self.items[0], self.items[2]))

    def test_all_unchanged_gives_an_empty_set(self) -> None:
        """With a fresh equal fact for every item nothing is sent."""
        source = _CheckSource({key: fact() for key in ("111", "222", "333")})
        self.assertEqual(self.sent(source), [])
        self.assertEqual(source.calls, [("333", "111", "222")])

    def test_missing_fact_sends_only_that_item(self) -> None:
        """A fact that the check did not return puts that item in the sent set."""
        self.assertEqual(self.sent(_CheckSource({"333": fact(), "222": fact()})), ["111"])

    def test_no_source_failure_or_timeout_sends_all(self) -> None:
        """No check source, a raising check, and a check without facts send every item."""
        everything = ["333", "111", "222"]
        self.assertEqual(self.sent(None), everything)
        self.assertEqual(self.sent(_CheckSource(error=RuntimeError("synthetic"))), everything)
        self.assertEqual(self.sent(_CheckSource({})), everything)
        # A source without the check call counts as a failed check
        self.assertEqual(self.sent(object()), everything)

    def test_unreadable_local_state_sends_all(self) -> None:
        """A cache that cannot report its state proves nothing."""
        facts = {key: fact() for key in ("111", "222", "333")}
        self.verifier = object()
        self.assertEqual(self.sent(_CheckSource(facts)), ["333", "111", "222"])

    def test_app_level_pending_work_sends_all(self) -> None:
        """Pending work reported by Steam sends every item, whatever the facts say."""
        facts = {key: fact() for key in ("111", "222", "333")}
        self.verifier = _Verifier(False, ("111", "222", "333"))
        self.assertEqual(self.sent(_CheckSource(facts)), ["333", "111", "222"])


if __name__ == "__main__":
    unittest.main()
