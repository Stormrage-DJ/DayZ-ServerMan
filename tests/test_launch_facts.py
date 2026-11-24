"""Launch facts (plan 1.1): stop intent, READY latch and one-time end claim per launch token."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.lifecycle_ownership import LaunchFacts, LaunchOwnership  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure, ServerReadiness, ServerState  # noqa: E402
from dayz_serverman.domain.models import ManagerSettings  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from tests.profile_fixtures import create_profile_paths, profile_payload  # noqa: E402
from tests.test_lifecycle_service import FakeInventory  # noqa: E402
from tests.test_server_ownership import ADOPT_NS, PROFILE, Session  # noqa: E402


class LaunchFactsTests(unittest.TestCase):
    """The facts of one token follow the rules of the leaf; no other token is touched."""

    def setUp(self) -> None:
        """Use an ownership holder without a repository."""
        self.ownership = LaunchOwnership()

    def test_unknown_token_has_no_facts(self) -> None:
        """A token never seen reads as all False."""
        self.assertEqual(self.ownership.launch_facts("t1"), LaunchFacts(False, False, False))

    def test_claim_end_before_ready_is_refused(self) -> None:
        """An end before READY is not a crash: no claim, no change."""
        self.assertFalse(self.ownership.claim_end("t1"))
        self.assertEqual(self.ownership.launch_facts("t1"), LaunchFacts(False, False, False))

    def test_claim_end_succeeds_once_after_ready(self) -> None:
        """A READY launch without stop intent is claimed once."""
        self.ownership.mark_ready("t1")
        self.assertTrue(self.ownership.claim_end("t1"))
        self.assertEqual(self.ownership.launch_facts("t1"), LaunchFacts(False, True, True))
        self.assertFalse(self.ownership.claim_end("t1"))

    def test_stop_intent_blocks_the_claim(self) -> None:
        """A deliberate stop means the end is not a crash."""
        self.ownership.mark_ready("t1")
        self.ownership.mark_stop_intent("t1")
        self.assertFalse(self.ownership.claim_end("t1"))
        self.assertEqual(self.ownership.launch_facts("t1"), LaunchFacts(True, True, False))

    def test_marks_are_idempotent_and_keep_the_other_flag(self) -> None:
        """Marks in any order never clear a flag; a repeat changes nothing."""
        self.ownership.mark_stop_intent("t1")
        self.ownership.mark_ready("t1")
        self.assertEqual(self.ownership.launch_facts("t1"), LaunchFacts(True, True, False))
        self.ownership.mark_stop_intent("t1")
        self.ownership.mark_ready("t1")
        self.assertEqual(self.ownership.launch_facts("t1"), LaunchFacts(True, True, False))

    def test_other_token_starts_clean(self) -> None:
        """Facts of one launch never leak to a new launch token."""
        self.ownership.mark_ready("t1")
        self.ownership.mark_stop_intent("t1")
        self.assertEqual(self.ownership.launch_facts("t2"), LaunchFacts(False, False, False))


class _SessionCase(unittest.TestCase):
    """Base for tests that run a manager session over fake ports."""

    def setUp(self) -> None:
        """Create a DayZ root with the profile, a manager data folder and a process table."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_launch_facts_")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.dayz, self.manager = base / "DayZ Root", base / "Manager"
        self.data = self.manager / "data"
        create_profile_paths(self.dayz)
        self.executable = self.dayz / "Bin" / "DayZ Server_x64.exe"
        self.settings = ManagerSettings(4, str(self.dayz.resolve()), str(self.executable.resolve()),
                                        None, None, None, None, MappingProxyType({}))
        self.profile = ProfileRecord(7, ProfileInput.parse(profile_payload()))
        self.inventory = FakeInventory()

    def launched(self) -> Session:
        """Start the server in a session and return it."""
        first = Session(self, "a")
        self.assertEqual(first.readiness.start(PROFILE, 7, 4).state, ServerState.RUNNING_MANAGED)
        return first


class StopIntentTests(_SessionCase):
    """Every deliberate stop marks the stop intent of the launch it stops."""

    def test_operator_stop_marks_the_stop_intent_of_that_launch(self) -> None:
        """A stop sets the intent of the stopped launch and nothing else."""
        first = self.launched()
        token = first.ownership.current().launch_token
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(False, False, False))
        self.assertEqual(first.lifecycle.stop(4).state, ServerState.STOPPED)
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(True, False, False))

    def test_stop_through_the_readiness_service_marks_the_intent(self) -> None:
        """The stop of an operator, a schedule, a backup chain or a mod restart reaches the same mark."""
        first = self.launched()
        token = first.ownership.current().launch_token
        first.readiness.stop(4)
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(True, False, False))

    def test_restart_marks_the_old_launch_and_leaves_the_new_launch_clean(self) -> None:
        """A restart is a deliberate stop of the old launch; the new launch starts clean."""
        first = self.launched()
        old = first.ownership.current().launch_token
        first.readiness.restart(PROFILE, 7, 4)
        new = first.ownership.current().launch_token
        self.assertNotEqual(old, new)
        self.assertEqual(first.ownership.launch_facts(old), LaunchFacts(True, False, False))
        self.assertEqual(first.ownership.launch_facts(new), LaunchFacts(False, False, False))

    def test_lifecycle_restart_marks_the_old_launch(self) -> None:
        """The restart of the lifecycle service itself marks the old launch."""
        first = self.launched()
        old = first.ownership.current().launch_token
        first.lifecycle.restart(PROFILE, 7, 4)
        new = first.ownership.current().launch_token
        self.assertNotEqual(old, new)
        self.assertEqual(first.ownership.launch_facts(old), LaunchFacts(True, False, False))
        self.assertEqual(first.ownership.launch_facts(new), LaunchFacts(False, False, False))

    def test_an_adopted_launch_marks_the_intent_under_its_own_token(self) -> None:
        """A second session adopts the launch and stops it; the intent sits under the token of the record."""
        first = self.launched()
        token = first.ownership.current().launch_token
        second = Session(self, "b", wall_ns=ADOPT_NS)
        second.readiness.status()
        self.assertEqual(second.ownership.current().launch_token, token)
        second.readiness.stop(4)
        self.assertEqual(second.ownership.launch_facts(token), LaunchFacts(True, False, False))

    def test_a_stop_request_that_fails_still_marks_the_intent(self) -> None:
        """A failed request may still end the process: the intent stays and no restart follows."""
        first = self.launched()
        token = first.ownership.current().launch_token
        first.stopper.failure = LifecycleFailure("STOP_FAILED", "synthetic stop failure")
        with self.assertRaises(LifecycleFailure):
            first.lifecycle.stop(4)
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(True, False, False))


class ReadyLatchTests(_SessionCase):
    """A status read that computes READY latches READY for the launch it describes."""

    def test_ready_is_latched_only_when_ready_is_computed(self) -> None:
        """No latch before READY; a query answer alone is not READY; both probes latch it."""
        first = self.launched()
        token = first.ownership.current().launch_token
        first.readiness.status()
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(False, False, False))
        first.probe.ready = True
        self.assertNotEqual(first.readiness.status().readiness, ServerReadiness.READY)
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(False, False, False))
        first.mission.ready = True
        self.assertEqual(first.readiness.status().readiness, ServerReadiness.READY)
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(False, True, False))

    def test_ready_of_an_adopted_launch_is_latched_under_its_token(self) -> None:
        """A second session that adopts the launch latches READY under the token of the record."""
        first = self.launched()
        token = first.ownership.current().launch_token
        second = Session(self, "b", wall_ns=ADOPT_NS)
        second.probe.ready = True
        second.mission.ready = True
        status = second.readiness.status()
        self.assertEqual(status.readiness, ServerReadiness.READY)
        self.assertEqual(second.ownership.launch_facts(token), LaunchFacts(False, True, False))

    def test_a_new_launch_starts_clean_after_ready(self) -> None:
        """READY of the old launch stays with it; the restarted launch has no fact yet."""
        first = self.launched()
        old = first.ownership.current().launch_token
        first.probe.ready = True
        first.mission.ready = True
        first.readiness.status()
        first.probe.ready = False
        first.mission.ready = False
        first.readiness.restart(PROFILE, 7, 4)
        new = first.ownership.current().launch_token
        self.assertEqual(first.ownership.launch_facts(new), LaunchFacts(False, False, False))
        self.assertEqual(first.ownership.launch_facts(old), LaunchFacts(True, True, False))

    def test_an_end_without_a_stop_is_claimed_once(self) -> None:
        """A READY launch that ends without a manager stop gives one claim."""
        first = self.launched()
        token = first.ownership.current().launch_token
        first.probe.ready = True
        first.mission.ready = True
        first.readiness.status()
        self.assertTrue(first.ownership.claim_end(token))
        self.assertFalse(first.ownership.claim_end(token))
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(False, True, True))

    def test_an_end_after_a_manager_stop_is_never_claimed(self) -> None:
        """A deliberate stop is not a crash, even for a READY launch."""
        first = self.launched()
        token = first.ownership.current().launch_token
        first.probe.ready = True
        first.mission.ready = True
        first.readiness.status()
        first.readiness.stop(4)
        self.assertFalse(first.ownership.claim_end(token))
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(True, True, False))

    def test_an_end_before_ready_is_never_claimed(self) -> None:
        """A launch that never became READY is not a crash."""
        first = self.launched()
        token = first.ownership.current().launch_token
        first.readiness.status()
        self.assertFalse(first.ownership.claim_end(token))
        self.assertEqual(first.ownership.launch_facts(token), LaunchFacts(False, False, False))

    def test_an_adopted_end_is_claimed_under_the_adopted_token(self) -> None:
        """A second session that adopted a READY launch claims its end under the recorded token."""
        first = self.launched()
        token = first.ownership.current().launch_token
        second = Session(self, "b", wall_ns=ADOPT_NS)
        second.probe.ready = True
        second.mission.ready = True
        second.readiness.status()
        self.assertTrue(second.ownership.claim_end(token))


if __name__ == "__main__":
    unittest.main()
