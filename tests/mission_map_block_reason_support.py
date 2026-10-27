"""The shown block reason after a recovery-required editor operation on the real lane (8.11 finding F1).

Architect 2026-10-17 14:48:26 in evidence 8.11, decision 4: the lane holds one editor block with the reason
RECOVERY_UNRESOLVED. Its catalogue sentence is the one shown. A new submission is refused with that reason
and owner. The operation record keeps the commit-step message for Manager diagnostics.
"""

from __future__ import annotations

import unittest
from typing import Any

from dayz_serverman.application.host_sentence_wording import BLOCK_REASONS, block_reason_text
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND, RECOVERY_UNRESOLVED
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import RECOVERY_BLOCK, OperationState, QueueUnavailable

__all__ = ["COMMIT_MESSAGE", "UNRESOLVED_SENTENCE", "assert_one_unresolved_block"]

# The commit-step message of a record publication that fails at or after the commit point
COMMIT_MESSAGE = "Apply reached its commit point; review recovery status."
# The catalogue sentence of the unresolved mission map recovery block
UNRESOLVED_SENTENCE = dict(BLOCK_REASONS)["unresolved mission map recovery"]


def assert_one_unresolved_block(test: unittest.TestCase, lane: OperationManager, record: Any, kind: str,
                                message: str = COMMIT_MESSAGE) -> None:
    """Check the record, the one editor block, its shown sentence and the refusal of a new submission of kind."""
    test.assertEqual(record.state, OperationState.RECOVERY_REQUIRED)
    # The record keeps the commit-step message, so diagnostics show where the failure was
    test.assertEqual((record.terminal_error.code, record.terminal_error.message), ("RECOVERY_REQUIRED", message))
    # One block, set by the lane, with the catalogue reason under the editor kind
    test.assertEqual([entry[:2] for entry in lane._recovery_blocks._entries], [(RECOVERY_UNRESOLVED, EDITOR_KIND)])
    pair = lane.recovery_block_pair()
    test.assertEqual(pair, (RECOVERY_UNRESOLVED, EDITOR_KIND))
    test.assertEqual(block_reason_text(*pair), UNRESOLVED_SENTENCE)
    with test.assertRaises(QueueUnavailable) as refused:
        lane.submit(kind, lambda _context: {})
    test.assertEqual((str(refused.exception), refused.exception.reason, refused.exception.owner),
                     (RECOVERY_UNRESOLVED, RECOVERY_BLOCK, EDITOR_KIND))
