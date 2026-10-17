"""Recovery of an interrupted backup restore, a part of the restore service (moved from `restores.py` in 10.3)."""

from __future__ import annotations

from pathlib import Path

from ..domain.lifecycle import LifecycleFailure
from ..domain.models import RecordUnavailable
from ..repositories.restore_storage import RestoreStorageError
from .folder_writer_scope import OWNER_WRITER_WAIT_SECONDS
from .installation_guard import InstallationGuard

# Block reason of a restore recovery that the installation guard refused
RECOVERY_NOT_STOPPED = "Mutations are blocked by an interrupted backup restore while the server is not proven stopped."
# Block reason of a restore recovery without a configured DayZ server folder; a repair save may pass it (QF-069)
RECOVERY_NO_DAYZ_ROOT = "Backup restore recovery requires a configured DayZ root."


class RestoreRecovery:
    """The recovery methods of `RestoreService`.

    The service sets the attributes that these methods read: `_journals`, `_settings`,
    `_lifecycle`, `_mutex`, `_folder_writer`, `_storage`, `_recovery_root` and `_editor`.
    """

    def recovery_pending(self) -> bool:
        """Report whether a restore journal exists, without reading settings or taking the mutex."""
        return bool(self._journals.records())

    def inspect_recovery(self, wait_seconds: float = OWNER_WRITER_WAIT_SECONDS) -> dict[str, object]:
        """Finish or undo an interrupted restore and report whether recovery still blocks mutations.

        Recovery copies and renames files in the DayZ root, so it runs only under the
        installation mutex with a proven stopped server (QF-027, QF-039). A refusal
        writes nothing, keeps the journal for a later inspection, and names the reason.
        """
        # Without journal records nothing can be blocking
        if not self._journals.records():
            return {"blocked": False, "diagnostics": []}
        try:
            settings = self._settings.load()
            if settings.dayz_root is None:
                # Named before the guard, so the block can be passed by a save that sets the folder (QF-069)
                return {
                    "blocked": True,
                    "reason": RECOVERY_NO_DAYZ_ROOT,
                    "diagnostics": [{
                        "code": "RECOVERY_REQUIRED",
                        "message": "Restore recovery needs a configured DayZ server folder.",
                        "usable": False,
                    }],
                }
            root = Path(settings.dayz_root)
            # The guard refuses before the block runs; the state is read under the mutex; A13 writer side inside
            guard = InstallationGuard(self._lifecycle, self._mutex, self._folder_writer)
            with guard.stopped(root, folder_wait=wait_seconds):
                # A journal with editor groups (10.10) checks their paths against the manager layout
                manager = None if self._editor is None else self._editor.paths
                return self._storage.inspect(self._journals, root, self._recovery_root, manager)
        except LifecycleFailure as failure:
            # A busy installation, or a server that is not proven stopped: recover later
            return {
                "blocked": True,
                "reason": RECOVERY_NOT_STOPPED,
                "diagnostics": [{
                    "code": failure.code,
                    "message": "Restore recovery needs a stopped server and the installation mutex.",
                    "usable": False,
                }],
            }
        except (OSError, RecordUnavailable, RestoreStorageError) as error:
            # Fail closed when recovery state cannot be inspected safely
            return {
                "blocked": True,
                "diagnostics": [{
                    "code": "RECOVERY_REQUIRED",
                    "message": "Restore recovery state could not be inspected safely.",
                    "usable": False,
                }],
            }
