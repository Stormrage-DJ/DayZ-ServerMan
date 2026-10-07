"""Direct profile reconstruction application boundary."""

from pathlib import Path
from collections.abc import Mapping, Callable
from typing import Any

from ..domain.backups import BACKUP_ID, SHA256, manifest_digest
from ..domain.lifecycle import ServerState, LifecycleFailure
from ..domain.models import RevisionConflict
from ..domain.models import ManagerSettings
from ..domain.backups import BackupManifest
from ..domain.profiles import ProfileInput
from ..repositories.backup_archives import read_archive_manifest
from ..repositories.backup_verification import sha256_file
from ..repositories.backups import BackupStorageError, BackupStorage
from ..repositories.profile_restore_storage import ProfileRestoreStorage
from .profiles import ProfileService
from .settings import SettingsService
from .folder_writer_scope import WriterScope
from .lifecycle_ports import InstallationMutexPort, ServerFolderWriterPort
from .profile_restore_preview import build_preview
from ..repositories.selected_backup import SelectedBackups

PREVIEW_FIELDS = {"backup_id", "profile_id", "display_name", "storage_policy", "game_port", "steam_query_port"}


class ProfileRestoreService:
    """Validate archive context and publish through the installation guard and joint journal."""

    def __init__(self, profiles: ProfileService, settings: SettingsService, backups: BackupStorage,
                 storage: ProfileRestoreStorage, lifecycle: Any, mutex: InstallationMutexPort,
                 udp_inventory: Callable[[], frozenset[int]], *,
                 folder_writer: ServerFolderWriterPort | None = None) -> None:
        """Bind shared service boundaries, a fail-closed UDP endpoint probe and the A13 writer side."""
        self.profiles, self.settings, self.backups = profiles, settings, backups
        self.storage, self.lifecycle, self.mutex = storage, lifecycle, mutex
        self.udp_inventory = udp_inventory
        self.folder_writer = folder_writer
        self.selected = SelectedBackups()

    def preview(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve a fresh deterministic plan without publishing targets or profile records."""
        self.validate_request(request)
        settings, root, backup_root, manifest = self.context(request["backup_id"])
        with self.mutex.guard(str(root)):
            self.require_stopped()
            with self.open_archive(backup_root, manifest, request["backup_id"]) as (directory, verified):
                return self.plan(directory, verified, root, settings, backup_root, request)[0]

    def plan(self, directory: Path, manifest: BackupManifest, root: Path, settings: ManagerSettings,
             backup_root: Path, request: Mapping[str, Any]) -> tuple[dict, ProfileInput, bytes, dict, dict | None]:
        """Bind the archive's actual bytes to the full destination fingerprint."""
        result = build_preview(directory, manifest, root, settings.revision, self.profiles.list(),
                               self.storage.profile_root, request, self.udp_inventory())
        preview = result[0]
        preview["preview_fingerprint"] = manifest_digest({"mapping": preview["preview_fingerprint"],
            "archive_sha256": sha256_file(self.archive_path(backup_root, manifest, request["backup_id"]))})
        return result

    def apply(self, parameters: Mapping[str, Any], operation_id: str, checkpoint: Callable[[str, int], None]) -> dict[str, Any]:
        """Recompute reviewed facts under the lane/mutex before any publication."""
        request = {key: parameters[key] for key in PREVIEW_FIELDS}
        self.validate_request(request)
        for field in ("expected_manifest_digest", "preview_fingerprint"):
            if not isinstance(parameters[field], str) or SHA256.fullmatch(parameters[field]) is None:
                raise ValueError("Restore digest or fingerprint is invalid.")
        settings, root, backup_root, manifest = self.context(request["backup_id"])
        with self.mutex.guard(str(root)):
            self.require_stopped()
            for prior in self.storage.journals.records():
                if prior["operation_id"] == operation_id:
                    result = prior["result"]
                    if prior["phase"] != "COMMITTED" or result["request"] != request or result["manifest_digest"] != parameters["expected_manifest_digest"] or result["preview_fingerprint"] != parameters["preview_fingerprint"]:
                        raise RevisionConflict("Restore operation already exists with a different request or requires recovery.")
                    if not prior["cleanup_complete"]:
                        self.storage.verify_committed(prior, root)
                    return result
            with self.open_archive(backup_root, manifest, request["backup_id"]) as (directory, verified):
                preview, profile, config, mapping, before = self.plan(directory, verified, root, settings, backup_root, request)
                if verified.manifest_digest != parameters["expected_manifest_digest"] or preview["preview_fingerprint"] != parameters["preview_fingerprint"]:
                    raise RevisionConflict("Restore preview changed. Review the current destinations again.")
                confirmation = parameters["overwrite_confirmation"]
                expected = {"preview_fingerprint": preview["preview_fingerprint"], "affected_profile_ids": preview["affected_profile_ids"]}
                if (preview["storage_policy"] == "replace_existing" and confirmation != expected) or (preview["storage_policy"] != "replace_existing" and confirmation is not None):
                    raise ValueError("Storage overwrite confirmation is missing or differs from the preview.")
                if self.settings.load() != settings:
                    raise RevisionConflict("Restore settings changed before publication.")
                # A13 (R-5): the writer side is held from before staging, which may create serverman, to the end
                with WriterScope(self.folder_writer).held_for():
                    checkpoint("VERIFYING_BACKUP", 10)
                    result = self.storage.restore(root, operation_id, directory, verified, preview, config, mapping, before, checkpoint)
                self.profiles.read(profile.profile_id)
                return result

    def context(self, backup_id: object) -> tuple[ManagerSettings, Path, Path, BackupManifest]:
        """Load archive identity independently of any registered profile."""
        if not isinstance(backup_id, str) or BACKUP_ID.fullmatch(backup_id) is None:
            raise ValueError("Backup identifier is invalid.")
        settings = self.settings.load()
        if settings.dayz_root is None or settings.revision is None:
            raise ValueError("Configure the DayZ root before restoring a profile.")
        root = Path(settings.dayz_root).resolve(strict=True)
        backup_root = self.settings.backup_root(settings)
        # Storage validates archive paths, complete content and reconstruction config bytes.
        if backup_id.startswith("selected-"):
            manifest = read_archive_manifest(self.selected.path(backup_id), check_archive_name=False)
        else:
            candidate = read_archive_manifest(backup_root / (backup_id + ".zip"), backup_id)
            manifest = self.backups.verified_manifest(backup_root, backup_id, candidate.profile_id)
        if manifest.schema_version != 3:
            raise BackupStorageError("INVALID_REQUEST", "This older backup lacks complete profile metadata. Create a new full backup first.")
        return settings, root, backup_root, manifest

    def archive_path(self, backup_root, manifest, reference):
        """Resolve the exact reviewed selection or configured archive."""
        return self.selected.path(reference) if reference.startswith("selected-") else backup_root / (manifest.backup_id + ".zip")

    def open_archive(self, backup_root, manifest, reference):
        """Reuse strict verification for either archive source."""
        return self.selected.open_verified(reference) if reference.startswith("selected-") else self.backups.open_verified(backup_root, manifest.backup_id, manifest.profile_id)

    def require_stopped(self) -> None:
        """Require positive stopped evidence, including absence of external servers."""
        status = self.lifecycle.status()
        if status.state != ServerState.STOPPED:
            raise LifecycleFailure("CONTROL_CONFLICT", "Stop all DayZ servers before restoring a profile.")

    @staticmethod
    def validate_request(request: Mapping[str, Any]) -> None:
        """Reject extra fields, ambiguous optional values and unsupported storage choices."""
        if set(request) != PREVIEW_FIELDS:
            raise ValueError("Direct restore request fields are invalid.")
        for field in ("profile_id", "display_name"):
            if request[field] is not None and (not isinstance(request[field], str) or not request[field]):
                raise ValueError("Restore profile identity is invalid.")
        if request["storage_policy"] not in (None, "preserve_original", "allocate_new", "replace_existing"):
            raise ValueError("Restore storage policy is invalid.")
        for field in ("game_port", "steam_query_port"):
            value = request[field]
            if value is not None and (type(value) is not int or not 1 <= value <= 65535):
                raise ValueError("Restore port is invalid.")
