"""Removal of the recovery copy of a restore once the restore has committed."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .backup_verification import is_reparse

# An operation identifier: letters, digits, hyphens or underscores, never a path step such as '..'
OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def remove_recovery_copy(recovery_root: Path, operation_id: str) -> bool:
    """Delete `<recovery root>/<operation id>` after a committed restore; return whether no copy is left.

    The restore is committed, so the copy of the replaced files is no longer needed. A copy that cannot be
    deleted, for example because another program has a file open, stays; the restore stays committed.
    """
    # The operation identifier names one folder directly below the recovery root
    if not isinstance(operation_id, str) or OPERATION_ID.fullmatch(operation_id) is None:
        return False
    root = recovery_root.resolve(strict=False)
    copy = root / operation_id
    if not copy.exists():
        return True
    if is_reparse(copy):
        return False
    shutil.rmtree(copy, ignore_errors=True)
    return not copy.exists()
