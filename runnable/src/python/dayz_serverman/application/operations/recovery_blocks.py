"""The reasons that block the operation lane until a recovery is resolved."""

from __future__ import annotations

import threading

from .logging import OperationLog


class RecoveryBlocks:
    """Every active block in the order it was set, each with the recovery that owns it.

    The owner is the kind of operation whose recovery lifts the block, for example
    RESTORE_BACKUP; a block without an owner is lifted only by a manager restart.
    A failed operation that requires recovery blocks under its own kind, so its
    recovery lifts that block too, while blocks of other recoveries stay (QF-039, QF-040).
    """

    def __init__(self, condition: threading.Condition, log: OperationLog) -> None:
        """Start without a block; share the lane lock and the lane log."""
        self._condition = condition
        self._log = log
        self._entries: list[tuple[str, str | None]] = []

    def latest(self) -> tuple[str | None, str | None]:
        """Return the reason and owner of the block set last, the one that is shown, read together.

        One lock acquisition, so a block set or lifted meanwhile never pairs one reason
        with another block's owner (QF-046); (None, None) when nothing blocks.
        """
        with self._condition:
            return self._entries[-1] if self._entries else (None, None)

    def add(self, reason: str, owner: str | None) -> None:
        """Set a block and log its reason; a block that is set again moves to the end."""
        with self._condition:
            if (reason, owner) in self._entries:
                self._entries.remove((reason, owner))
            self._entries.append((reason, owner))
        # Record every block reason, so Manager diagnostics explains why changes are refused
        fields = {"reason": reason, **({"owner": owner} if owner else {})}
        self._log.emit("operation_lane.recovery_block", fields=fields, level="ERROR")

    def clear(self, owner: str | None) -> None:
        """Remove the blocks of one owner, or every block when owner is None."""
        with self._condition:
            self._entries = [] if owner is None else [entry for entry in self._entries if entry[1] != owner]


class RecoveryBlockAccess:
    """The recovery-block part of the operation manager's interface, over its RecoveryBlocks."""

    _recovery_blocks: RecoveryBlocks

    def block_for_recovery(self, message: str, owner: str | None = None) -> None:
        """Block the lane until the recovery of owner (an operation kind) or a restart lifts it."""
        self._recovery_blocks.add(message, owner)

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
