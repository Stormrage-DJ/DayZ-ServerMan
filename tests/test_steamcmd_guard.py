"""The SteamCMD run guard: try, poison, and the bounded, cancellable wait of a lane operation."""
from __future__ import annotations

import threading
import unittest

try:
    from tests import server_build_fixtures  # noqa: F401
except ModuleNotFoundError:
    import server_build_fixtures  # noqa: F401

from dayz_serverman.application.operations.models import OperationCancelled, OperationFailure
from dayz_serverman.application.steamcmd_guard import (
    BUSY_TEXT,
    WAIT_PHASE,
    LaneHold,
    SteamCmdRunGuard,
    hold_steamcmd,
)


class FakeContext:
    """Operation context stand-in: records checkpoints and stops at the safe point when cancelled."""

    def __init__(self, cancel_after: int | None = None) -> None:
        """Cancel once the given number of cancellation reads has passed."""
        self.checkpoints: list[tuple[str, int]] = []
        self.reads = 0
        self.cancel_after = cancel_after

    @property
    def cancellation_requested(self) -> bool:
        """Report the cancellation after the configured number of reads."""
        self.reads += 1
        return self.cancel_after is not None and self.reads > self.cancel_after

    def checkpoint(self, phase: str, percent: int) -> None:
        """Publish one checkpoint; the wait phase is a safe point."""
        self.checkpoints.append((phase, percent))
        if phase == WAIT_PHASE and self.cancel_after is not None and self.reads > self.cancel_after:
            raise OperationCancelled("cancelled at the safe point")


class FakeClock:
    """Monotonic clock that advances on every read."""

    def __init__(self, step: float) -> None:
        """Advance by the step at each read."""
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        """Return the next moment."""
        self.now += self.step
        return self.now


class GuardTests(unittest.TestCase):
    """Detailed design 14.5 and Architect decisions 1 and 2."""

    def test_try_and_release(self) -> None:
        """A held guard refuses a try; after release it is available again."""
        guard = SteamCmdRunGuard()
        self.assertTrue(guard.available() and guard.try_hold())
        self.assertFalse(guard.available() or guard.try_hold())
        guard.release()
        self.assertTrue(guard.try_hold())
        guard.release()

    def test_an_unproven_exit_poisons_the_guard(self) -> None:
        """After a poisoned release no try succeeds and a lane holder fails before its preflight."""
        guard = SteamCmdRunGuard()
        guard.try_hold()
        guard.release(unproven=True)
        self.assertTrue(guard.poisoned)
        self.assertFalse(guard.available() or guard.try_hold())
        context = FakeContext()
        with self.assertRaises(OperationFailure) as raised:
            guard.hold_for(context, "UPDATE_WORKSHOP_ITEMS", 5)
        self.assertEqual(raised.exception.code, "STEAMCMD_EXIT_UNPROVEN")
        self.assertFalse(raised.exception.recovery_required)
        self.assertEqual(context.checkpoints, [])

    def test_free_guard_publishes_no_wait_phase(self) -> None:
        """A free guard is taken at once, without a checkpoint."""
        guard, context = SteamCmdRunGuard(), FakeContext()
        with guard.hold_for(context, "UPDATE_WORKSHOP_ITEMS", 5) as hold:
            self.assertIsInstance(hold, LaneHold)
            self.assertFalse(guard.available())
        self.assertTrue(guard.available())
        self.assertEqual(context.checkpoints, [])

    def test_wait_publishes_once_and_acquires_when_free(self) -> None:
        """The wait phase is published once; later tries read cancellation without publishing."""
        guard = SteamCmdRunGuard(poll_seconds=0.01)
        guard.try_hold()
        context = FakeContext()
        holder: list[LaneHold] = []
        worker = threading.Thread(target=lambda: holder.append(guard.hold_for(context, "AUTHENTICATE_STEAMCMD", 5)))
        worker.start()
        while context.reads < 5:
            pass
        guard.release()
        worker.join(2.0)
        self.assertEqual(context.checkpoints, [(WAIT_PHASE, 5)])
        self.assertGreaterEqual(context.reads, 5)
        holder[0].release()
        self.assertTrue(guard.available())

    def test_cancellation_stops_at_the_safe_point(self) -> None:
        """A cancellation during the wait raises at the wait phase; the guard stays with its holder."""
        guard = SteamCmdRunGuard(poll_seconds=0.01)
        guard.try_hold()
        context = FakeContext(cancel_after=3)
        with self.assertRaises(OperationCancelled):
            guard.hold_for(context, "UPDATE_WORKSHOP_ITEMS", 5)
        self.assertEqual(context.checkpoints, [(WAIT_PHASE, 5), (WAIT_PHASE, 5)])
        self.assertFalse(guard.available())

    def test_the_wait_is_bounded(self) -> None:
        """After 120 s of waiting the operation fails with STEAMCMD_BUSY, not as a recovery."""
        guard = SteamCmdRunGuard(poll_seconds=0.001, monotonic=FakeClock(30.0))
        guard.try_hold()
        with self.assertRaises(OperationFailure) as raised:
            guard.hold_for(FakeContext(), "UPDATE_WORKSHOP_ITEMS", 5)
        self.assertEqual((raised.exception.code, raised.exception.safe_message), ("STEAMCMD_BUSY", BUSY_TEXT))
        self.assertFalse(raised.exception.recovery_required)

    def test_poisoning_during_the_wait_fails_the_waiter(self) -> None:
        """A guard poisoned by the run that a lane holder waited for fails that holder."""
        guard = SteamCmdRunGuard(poll_seconds=0.01)
        guard.try_hold()
        errors: list[str] = []

        def wait() -> None:
            """Wait for the guard and record the failure code."""
            try:
                guard.hold_for(FakeContext(), "UPDATE_WORKSHOP_ITEMS", 5)
            except OperationFailure as error:
                errors.append(error.code)

        worker = threading.Thread(target=wait)
        worker.start()
        guard.release(unproven=True)
        worker.join(2.0)
        self.assertEqual(errors, ["STEAMCMD_EXIT_UNPROVEN"])

    def test_no_guard_is_an_empty_hold(self) -> None:
        """Older wiring and fixtures without a guard run unchanged."""
        with hold_steamcmd(None, FakeContext(), "UPDATE_WORKSHOP_ITEMS", 5) as hold:
            hold.release(unproven=True)
            hold.release()


if __name__ == "__main__":
    unittest.main()
