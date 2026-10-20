"""Configuration-specific publication and recovery over the existing journal engine."""
from __future__ import annotations

import json
import uuid

from .configuration_journal_paths import artifacts_known
from pathlib import Path
from typing import Callable

from ..adapters.windows.shared_files import read_bytes_shared, replace_file
from ..domain.profiles import ProfileInput, ProfileRecord
from .configuration_common import digest_bytes
from .configuration_journal import ConfigurationGroup, ConfigurationJournal, ConfigurationPublicationError, parse_journal, successor_bytes
from .configuration_journal_paths import journal_paths_safe, native_target, profile_target
from .configuration_journal_store import ConfigurationJournalStore, durable_create
from .journaled_publication import JournaledPublication
from .publication_contracts import PublicationPolicy, RecoveryHooks, in_new_state
from .restore_paths import matches


POLICY = PublicationPolicy(
    error_type=ConfigurationPublicationError, journal_error_type=ValueError,
    cleanup_message="Configuration staging cleanup requires recovery.",
    invalid_message="A configuration journal is unreadable or unsafe.",
    unresolved_message="Configuration recovery cannot prove a complete saved state.",
    verify_message="Configuration publication failed verification.", recheck_old_state=True,
    changed_message="Configuration changed before publication.", ready_groups_untouched=True,
)


class ConfigurationEngine(JournaledPublication):
    """Add exact artifact and pair proofs at destructive shared-engine entry points."""

    def __init__(self, owner) -> None:
        """Keep the configuration adapter as the sole proof-policy owner."""
        self.owner = owner
        super().__init__(owner._engine_fault, POLICY)

    def compensate(self, journal, repository) -> bool:
        """Unknown stages cannot be overwritten by compensation copies."""
        self.owner._active_journal = journal
        if not artifacts_known(journal, self.owner.recovery):
            return False
        return super().compensate(journal, repository)

    def cleanup(self, journal, recovery_root) -> None:
        """Prove the full terminal state and all artifacts before any deletion."""
        self.owner._require_terminal(journal)
        super().cleanup(journal, recovery_root)


class ConfigurationPublication:
    """Own exact two-target staging, commit direction, and idempotent profile publication."""

    def __init__(self, profiles: Path, operations: Path, fault_hook: Callable | None = None) -> None:
        """Bind trusted manager storage without creating directories during construction."""
        self.profiles = profiles
        self.store = ConfigurationJournalStore(operations / "configuration-journals", self._require_terminal)
        self.recovery = operations / "configuration-recovery"
        self.fault = fault_hook or (lambda _phase, _index: None)
        self._active_journal = None
        self.engine = ConfigurationEngine(self)
        self.hooks = RecoveryHooks(self._forward, self._rollback)

    def publish(self, root: Path, profile: ProfileRecord, settings_revision: int,
                proposed: bytes, expected_digest: str, port: int) -> str:
        """Stage a validated pair, publish native bytes, then commit its exact profile successor."""
        target = native_target(root, profile.values.server_config)
        record = profile_target(self.profiles, profile.values.profile_id)
        old = read_bytes_shared(record)
        document = json.loads(old.decode("utf-8"))
        fields = {key: value for key, value in document.items() if key not in ("revision", "schema_version")}
        if document.get("revision") != profile.revision or ProfileInput.parse(fields) != profile.values:
            raise ConfigurationPublicationError("REVISION_CONFLICT", "Profile changed before publication.")
        if not matches(target, expected_digest):
            raise ConfigurationPublicationError("REVISION_CONFLICT", "Configuration changed before publication.")
        operation_id = uuid.uuid4().hex
        native_stage = target.with_name(f".{target.name}.{operation_id}.configuration-stage")
        profile_stage = record.with_name(f".{record.name}.{operation_id}.configuration-stage")
        recovery = self.recovery / operation_id / "native.old"
        successor = successor_bytes(document, port)
        group = ConfigurationGroup(str(target), str(native_stage), str(recovery), expected_digest, digest_bytes(proposed))
        journal = ConfigurationJournal(operation_id, str(root.resolve()), profile.values.profile_id,
                                       settings_revision, document, digest_bytes(old), digest_bytes(successor),
                                       port, str(profile_stage), [group])
        if not journal_paths_safe(journal, self.profiles, root, self.recovery):
            raise ConfigurationPublicationError("PATH_INVALID", "Configuration publication paths are unsafe.")
        parse_journal(journal.document())
        # Stage only after every candidate and target is proven; no preview reaches this code.
        try:
            durable_create(native_stage, proposed)
            durable_create(profile_stage, successor)
            durable_create(recovery, read_bytes_shared(target))
            if not matches(recovery, expected_digest):
                raise OSError("configuration recovery copy changed")
            self.store.save(journal)
            self._active_journal = journal
            self._engine_fault("READY", 0)
            self.engine.publish_groups(journal, self.store)
            self._engine_fault("BEFORE_COMMIT", 0)
            if not self._new_native(journal) or not self._profile_old(journal):
                raise self._required()
            journal.phase = "COMMITTING"
            self.store.save(journal)
            self._engine_fault("COMMITTING", 0)
            if not self._forward(journal):
                raise OSError("configuration profile commit could not be proven")
            if not self._new_pair(journal):
                raise self._required()
            journal.phase, journal.resolved, journal.result = "COMMITTED", True, "COMMITTED"
            self.store.save(journal)
            self._engine_fault("COMMITTED", 0)
            self.engine.cleanup(journal, self.recovery)
            self.store.retire(journal)
            return group.new_digest
        except Exception as error:
            # The durable record, not the local phase, selects rollback versus recovery forward.
            try:
                persisted = self.store.load(operation_id)
            except FileNotFoundError:
                if journal.publication_started:
                    raise self._required() from error
                self._discard_unpublished(journal)
                if self.store.records():
                    raise self._required() from error
                raise
            except (OSError, ValueError, TypeError, KeyError):
                raise self._required() from error
            if persisted.phase in {"COMMITTING", "COMMITTED"}:
                raise self._required() from error
            try:
                if not self._safe_states(persisted, root) or not self.engine.compensate(persisted, self.store):
                    raise self._required()
                self.engine.cleanup(persisted, self.recovery)
                if not self._rollback(persisted):
                    raise self._required()
                self.store.retire(persisted)
            except Exception:
                raise self._required() from error
            raise ConfigurationPublicationError("STORAGE_FAILURE", "Configuration changes were not saved.") from error

    def inspect(self, root: Path) -> dict[str, object]:
        """Recover only known states inside this configuration owner's exact path policy."""
        try:
            return self.engine.inspect(self.store, self.recovery,
                                       lambda journal: self._safe_states(journal, root), self.hooks)
        except (OSError, ValueError, TypeError, KeyError):
            return {"blocked": True, "diagnostics": [{"code": "RECOVERY_REQUIRED",
                    "message": "Configuration recovery records could not be verified.", "usable": False}]}

    def _safe_states(self, journal: ConfigurationJournal, root: Path) -> bool:
        """Unknown target bytes never authorize compensation or commit."""
        if not journal_paths_safe(journal, self.profiles, root, self.recovery):
            return False
        self._active_journal = journal
        if not artifacts_known(journal, self.recovery):
            return False
        group = journal.groups[0]
        native_known = matches(Path(group.target_path), group.old_digest) or in_new_state(group)
        record = profile_target(self.profiles, journal.profile_id)
        profile_known = self._profile_old(journal) or matches(record, journal.new_profile_digest)
        if journal.phase not in {"COMMITTING", "COMMITTED"} and not self._profile_old(journal):
            return False
        return native_known and profile_known

    def _forward(self, journal: ConfigurationJournal) -> bool:
        """Publish the exact successor at most once, preserving every unrelated profile value."""
        self._active_journal = journal
        if not self._new_native(journal) or not artifacts_known(journal, self.recovery):
            return False
        target = profile_target(self.profiles, journal.profile_id)
        stage = Path(journal.profile_stage)
        if matches(target, journal.new_profile_digest):
            return self._new_pair(journal) and self._remove_stage(stage, journal.new_profile_digest)
        if not self._profile_old(journal) or not matches(stage, journal.new_profile_digest):
            return False
        self._engine_fault("BEFORE_PROFILE", 0)
        # Recheck after the observable checkpoint, before replacing old known bytes.
        if not self._new_native(journal) or not self._profile_old(journal):
            return False
        replace_file(stage, target)
        self._engine_fault("AFTER_PROFILE", 0)
        return self._new_pair(journal)

    def _profile_old(self, journal: ConfigurationJournal) -> bool:
        """Prove compensation can leave the profile unchanged."""
        target = profile_target(self.profiles, journal.profile_id)
        return (matches(target, journal.old_profile_digest)
                and json.loads(read_bytes_shared(target).decode("utf-8")) == journal.old_profile)

    def _rollback(self, journal: ConfigurationJournal) -> bool:
        """Discard only a proven unpublished successor; never restore an older profile revision."""
        if not self._profile_old(journal):
            return False
        return self._remove_stage(Path(journal.profile_stage), journal.new_profile_digest)

    @staticmethod
    def _remove_stage(path: Path, digest: str) -> bool:
        """Remove a known owner stage while preserving any unknown artifact."""
        if not path.exists():
            return True
        if not matches(path, digest):
            return False
        path.unlink()
        return True

    def _discard_unpublished(self, journal: ConfigurationJournal) -> None:
        """Clean known pre-journal stages when no data publication was possible."""
        if not artifacts_known(journal, self.recovery):
            raise self._required()
        for path, digest in ((journal.profile_stage, journal.new_profile_digest),
                             (journal.groups[0].staging_path, journal.groups[0].new_digest),
                             (journal.groups[0].recovery_path, journal.groups[0].old_digest)):
            if not self._remove_stage(Path(path), digest):
                raise self._required()
        folder = self.recovery / journal.operation_id
        if folder.exists():
            folder.rmdir()

    def _new_native(self, journal: ConfigurationJournal) -> bool:
        """Prove all native targets still contain the reviewed bytes."""
        return all(in_new_state(group) for group in journal.groups)

    def _new_pair(self, journal: ConfigurationJournal) -> bool:
        """Verify both data owners together before claiming a complete save."""
        return self._new_native(journal) and matches(profile_target(self.profiles, journal.profile_id),
                                                    journal.new_profile_digest)

    def _engine_fault(self, phase: str, index: int) -> None:
        """Recheck artifacts after any observable checkpoint and before engine mutation."""
        self.fault(phase, index)
        if self._active_journal is not None and not artifacts_known(self._active_journal, self.recovery):
            raise self._required()

    def _require_terminal(self, journal: ConfigurationJournal) -> None:
        """Retain the journal when either final targets or owner artifacts are unknown."""
        group = journal.groups[0]
        pair = self._new_pair(journal) if journal.committed else (
            self._profile_old(journal) and matches(Path(group.target_path), group.old_digest))
        if not pair or not artifacts_known(journal, self.recovery):
            raise self._required()

    @staticmethod
    def _required() -> ConfigurationPublicationError:
        """Classify unresolved pair publication for the operation recovery block."""
        return ConfigurationPublicationError("RECOVERY_REQUIRED", "Configuration changes require recovery.",
                                             recovery_required=True)
