"""The reasons that block the operation lane until a recovery is resolved."""

from __future__ import annotations

import threading

from .logging import OperationLog


# Cause of a block that a startup recovery set only because no DayZ server folder is configured (QF-069, D19)
MISSING_DAYZ_ROOT = "MISSING_DAYZ_ROOT"
# The one kind that may pass such blocks: a Settings save that sets only the DayZ server folder
REPAIR_KIND = "SAVE_SETTINGS"


class RecoveryBlocks:
    """Every active block in the order it was set, each with the recovery that owns it and its cause.

    The owner is the kind of operation whose recovery lifts the block, for example
    RESTORE_BACKUP; a block without an owner is lifted only by a manager restart.
    A failed operation that requires recovery blocks under its own kind, so its
    recovery lifts that block too, while blocks of other recoveries stay (QF-039, QF-040).
    The cause is MISSING_DAYZ_ROOT only for the three startup paths that block
    because no DayZ server folder is set; every other block has no cause.
    """

    def __init__(self, condition: threading.Condition, log: OperationLog) -> None:
        """Start without a block; share the lane lock and the lane log."""
        self._condition = condition
        self._log = log
        self._entries: list[tuple[str, str | None, str | None]] = []

    def latest(self) -> tuple[str | None, str | None]:
        """Return the reason and owner of the block set last, the one that is shown, read together.

        One lock acquisition, so a block set or lifted meanwhile never pairs one reason
        with another block's owner (QF-046); (None, None) when nothing blocks.
        """
        with self._condition:
            return self._entries[-1][:2] if self._entries else (None, None)

    def add(self, reason: str, owner: str | None, cause: str | None = None) -> None:
        """Set a block and log its reason; a block that is set again moves to the end with the new cause."""
        with self._condition:
            self._entries = [entry for entry in self._entries if entry[:2] != (reason, owner)]
            self._entries.append((reason, owner, cause))
        # Record every block reason, so Manager diagnostics explains why changes are refused
        fields = {"reason": reason, **({"owner": owner} if owner else {})}
        self._log.emit("operation_lane.recovery_block", fields=fields, level="ERROR")

    def clear(self, owner: str | None) -> None:
        """Remove the blocks of one owner, or every block when owner is None."""
        with self._condition:
            self._entries = [] if owner is None else [entry for entry in self._entries if entry[1] != owner]

    def only_cause(self, cause: str) -> bool:
        """Report whether at least one block exists and every block has this cause."""
        with self._condition:
            return bool(self._entries) and all(entry[2] == cause for entry in self._entries)

    def admission(self, kind: str, repairs_missing_dayz_root: bool) -> tuple[str | None, str | None, bool]:
        """Decide whether a submission passes the blocks; call it under the lane lock.

        Returns the reason and owner of the refusing block, (None, None) to admit,
        and True when the submission is admitted through blocks. Only a repair save
        passes, and only while every block is a "no DayZ server folder" block.
        """
        if repairs_missing_dayz_root and kind != REPAIR_KIND:
            raise ValueError("only a settings save may repair a missing DayZ server folder")
        with self._condition:
            if not self._entries:
                return None, None, False
            if repairs_missing_dayz_root and self.only_cause(MISSING_DAYZ_ROOT):
                return None, None, True
            reason, owner, _cause = self._entries[-1]
            return reason, owner, False

    def log_bypass(self, kind: str) -> None:
        """Log once that a submission was admitted through the active blocks, with their reasons."""
        with self._condition:
            reasons = [entry[0] for entry in self._entries]
        self._log.emit("operation_lane.recovery_block_bypass", fields={"kind": kind, "reasons": reasons},
                       level="WARNING")


class RecoveryBlockAccess:
    """The recovery-block part of the operation manager's interface, over its RecoveryBlocks."""

    _recovery_blocks: RecoveryBlocks

    def block_for_recovery(self, message: str, owner: str | None = None) -> None:
        """Block the lane until the recovery of owner (an operation kind) or a restart lifts it."""
        self._recovery_blocks.add(message, owner)

    def block_for_missing_dayz_root(self, message: str, owner: str | None = None) -> None:
        """Block the lane because a startup recovery has no DayZ server folder; a repair save may pass it."""
        self._recovery_blocks.add(message, owner, MISSING_DAYZ_ROOT)

    def clear_recovery_block(self, owner: str | None = None) -> None:
        """Lift the blocks of owner after its recovery; without owner, every block."""
        self._recovery_blocks.clear(owner)

    @property
    def recovery_block(self) -> str | None:
        """Return the current recovery block message, if any."""
        return self._recovery_blocks.latest()[0]

    def recovery_block_pair(self) -> tuple[str | None, str | None]:
        """Return the shown block's reason and owner together, so its wording names the right way out."""
        return self._recovery_blocks.latest()
