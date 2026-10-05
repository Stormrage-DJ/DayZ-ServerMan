"""The repair save of QF-069 (D19): set the DayZ server folder while only "no DayZ server folder" blocks are active.

A startup recovery that finds an unfinished journal and no DayZ server folder
blocks every change. The lane lets one settings save through those blocks: a
save that sets only the DayZ server folder. It writes the settings file only,
lifts no block and runs no recovery; the recoveries run at the next start,
inside the installation guard (D14). It takes no installation mutex, reads no
server state and never asks for recovery.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ..domain.models import ManagerSettings, PathRole, PathStatus, RevisionConflict, SettingsInput
from ..repositories.paths import normalize_external_path
from ..repositories.profile_restore_storage import ProfileRestoreStorage
from ..repositories.restore_journal import RestoreJournalRepository
from ..repositories.restore_paths import journal_paths_safe
from .operations.models import OperationFailure
from .settings import SETTINGS_FIELDS, SettingsService, SettingsValidationError

# The DayZ server program below the folder, as the normal save derives it
DAYZ_PROGRAM = "DayZServer_x64.exe"
# Refusal of a folder that is not a usable DayZ server folder
NOT_A_SERVER_FOLDER = "The DayZ server folder must exist and hold the DayZ server program."
# Refusal of a folder that an unfinished restore journal does not name
NOT_THE_USED_FOLDER = "This is not the DayZ server folder that the interrupted work used. Choose that folder."


def _normalized(value: object) -> str | None:
    """Normalize one requested root like the save does; blank is None; a bad path raises ValueError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError("a location root must be a string")
    return normalize_external_path(value)


def _same(requested: str | None, stored: str | None) -> bool:
    """Compare two normalized paths without regard to case; null equals only null."""
    if requested is None or stored is None:
        return requested is stored
    return requested.casefold() == stored.casefold()


def is_repair_shaped(values: Mapping[str, object], current: ManagerSettings) -> bool:
    """Report whether a request sets a DayZ server folder and keeps the SteamCMD and backup folders as stored."""
    dayz_root = values.get("dayz_root")
    if not isinstance(dayz_root, str) or not dayz_root.strip():
        return False
    try:
        return (_normalized(dayz_root) is not None
                and _same(_normalized(values.get("steamcmd_root")), current.steamcmd_root)
                and _same(_normalized(values.get("custom_backup_root")), current.custom_backup_root))
    except ValueError:
        return False


class SettingsRepair:
    """Check and save a repair request; read-only on every journal and on the DayZ installation."""

    def __init__(
        self, settings: SettingsService, restore_journals: RestoreJournalRepository,
        backup_recovery: Path, profile_restores: ProfileRestoreStorage,
    ) -> None:
        """Bind the settings service, the backup-restore journals and the direct-restore storage."""
        self._settings = settings
        self._restore_journals = restore_journals
        self._backup_recovery = backup_recovery
        self._profile_restores = profile_restores

    def is_repair_shaped(self, values: Mapping[str, object]) -> bool:
        """Report the shape against the stored settings; a record that cannot be read is no repair."""
        try:
            return is_repair_shaped(values, self._settings.load())
        except Exception:
            return False

    def save(self, values: Mapping[str, object], expected_revision: int | None) -> ManagerSettings:
        """Save the DayZ server folder of a repair request; any failure writes nothing.

        Raises OperationFailure without recovery_required: REVISION_CONFLICT for a
        stale or no longer repair-shaped request, PATH_INVALID for a folder that is
        not usable or not the one that an unfinished restore used.
        """
        try:
            current = self._settings.load()
            if current.revision != expected_revision or not is_repair_shaped(values, current):
                raise RevisionConflict("Manager settings changed. Reload them.")
            dayz_root = _normalized(values["dayz_root"])
            # Every other field comes from the stored record, never from the request
            record = SettingsInput(**{
                **{field: getattr(current, field) for field in SETTINGS_FIELDS},
                "dayz_root": dayz_root, "dayz_executable": str(Path(dayz_root) / DAYZ_PROGRAM),
            })
            checked = self._settings.prepare_save(record, expected_revision)
            self._require_server_folder(checked)
            self._require_journal_match(Path(dayz_root))
            return self._settings.save(record, expected_revision)
        except RevisionConflict as error:
            raise OperationFailure("REVISION_CONFLICT", str(error)) from error
        except (SettingsValidationError, ValueError) as error:
            raise OperationFailure("PATH_INVALID", str(error)) from error
        except OSError as error:
            raise OperationFailure("STORAGE_FAILURE", "Settings could not be stored.", retryable=True) from error

    def _require_server_folder(self, candidate: ManagerSettings) -> None:
        """Require the folder and its program to be READY: present, not a reparse point, the program inside."""
        statuses = {item.role: item.status for item in self._settings.diagnostics(candidate)}
        if any(statuses.get(role) != PathStatus.READY for role in (PathRole.DAYZ_ROOT, PathRole.DAYZ_EXECUTABLE)):
            raise SettingsValidationError(NOT_A_SERVER_FOLDER)

    def _require_journal_match(self, candidate: Path) -> None:
        """Refuse a folder that a readable backup-restore or a pending direct-restore journal does not name.

        Read only and under no mutex. An unreadable journal does not refuse; the
        recovery after the restart fails closed on it. Publication journals record
        no DayZ root, so they are not checked here.
        """
        try:
            for _path, journal in self._restore_journals.records():
                if journal is not None and not journal_paths_safe(journal, candidate, self._backup_recovery):
                    raise SettingsValidationError(NOT_THE_USED_FOLDER)
        except (OSError, ValueError) as error:
            raise SettingsValidationError(NOT_THE_USED_FOLDER) from error
        try:
            records = self._profile_restores.journals.records()
        except Exception:
            return
        for record in records:
            try:
                pending = record["phase"] not in {"COMMITTED", "ROLLED_BACK"} or (
                    record["phase"] == "COMMITTED" and not record["cleanup_complete"])
                if pending:
                    self._profile_restores.validate_roots(record, candidate)
            except Exception as error:
                raise SettingsValidationError(NOT_THE_USED_FOLDER) from error
