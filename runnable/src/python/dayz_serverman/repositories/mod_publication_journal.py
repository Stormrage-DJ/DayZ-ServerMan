"""Strict atomic persistence for Task 6.2 publication journals."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..adapters.windows.shared_files import read_text_shared, replace_file, write_bytes_atomically
from ..domain.mod_publication import (
    GroupState,
    PublicationGroup,
    PublicationIntent,
    PublicationJournal,
    PublicationPhase,
    PublicationValidationError,
    TargetRole,
    PUBLICATION_SCHEMA,
    SHA256,
    journal_state_is_legal,
    publication_fingerprint,
    validate_intent,
    validate_publication_id,
)
from ..domain.profiles import validate_relative_path
from ..adapters.windows.publication_paths import (
    PublicationPathError,
    persist_authority,
    verify_authority as verify_authority_record,
)


class PublicationJournalError(RuntimeError):
    """Raised when a publication journal operation cannot proceed safely."""
    pass


class PublicationJournalRepository:
    """Persist, validate, and retire publication journals under one root."""

    def __init__(self, root: Path) -> None:
        """Store the journal root and its retired subdirectory."""
        self.root = root
        self.retired_root = root / "retired"

    def path_for(self, publication_id: str) -> Path:
        """Return the journal path for one validated publication id."""
        try:
            validate_publication_id(publication_id)
        except PublicationValidationError as error:
            raise PublicationJournalError(str(error)) from error
        return self.root / f"{publication_id}.json"

    def bind_authority(
        self, journal: PublicationJournal, intent: PublicationIntent | None,
    ) -> Path:
        """Bind a trusted intent to a journal and persist the authority record."""
        self.path_for(journal.publication_id)
        # Binding is impossible without the trusted intent
        if intent is None:
            raise PublicationJournalError("trusted publication intent is missing")
        try:
            validate_intent(intent)
            # Stamp the first binding time only; later bindings keep it
            if not journal.updated_at:
                journal.updated_at = _timestamp()
            validate_journal(journal)
            _validate_intent_plan(journal, intent)
        except PublicationValidationError as error:
            raise PublicationJournalError(str(error)) from error
        try:
            return persist_authority(self.root, journal, intent)
        except PublicationPathError as error:
            raise PublicationJournalError(str(error)) from error

    def verify_authority(self, journal: PublicationJournal) -> None:
        """Verify the stored authority record still matches the journal."""
        self.path_for(journal.publication_id)
        try:
            verify_authority_record(self.root, journal)
        except PublicationPathError as error:
            raise PublicationJournalError(str(error)) from error

    def save(self, journal: PublicationJournal) -> Path:
        """Write a journal atomically and verify the published bytes."""
        self.path_for(journal.publication_id)
        journal.updated_at = _timestamp()
        try:
            validate_journal(journal)
        except PublicationValidationError as error:
            raise PublicationJournalError(str(error)) from error
        # The stored authority must match the journal before any write
        self.verify_authority(journal)
        path = self.path_for(journal.publication_id)
        payload = json.dumps(
            journal.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
        ) + "\n"
        # Stage, fsync and swap the UTF-8 bytes; the helper creates the folder and removes its staging file
        write_bytes_atomically(path, payload.encode("utf-8"))
        # Re-read the published file so silent corruption is caught here
        if self.load(path).to_dict() != journal.to_dict():
            raise PublicationJournalError("published journal failed verification")
        return path

    def load(self, path: Path) -> PublicationJournal:
        """Load and fully validate one journal record from disk."""
        self._validate_record_path(path)
        try:
            raw = json.loads(read_text_shared(path, "utf-8"))
            journal = _parse(raw)
            # The filename must agree with the embedded identifier
            if path.stem != journal.publication_id:
                raise PublicationJournalError("journal filename does not match its identifier")
            return journal
        except PublicationJournalError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as error:
            raise PublicationJournalError("publication journal is unreadable") from error

    def records(self) -> tuple[tuple[Path, PublicationJournal | None], ...]:
        """Return every journal record, paired with None when unreadable."""
        result = []
        # Unreadable records are reported instead of aborting the scan
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name.casefold()):
            try:
                result.append((path, self.load(path)))
            except PublicationJournalError:
                result.append((path, None))
        return tuple(result)

    def retire(self, journal: PublicationJournal) -> Path:
        """Move a resolved journal into retirement after verifying it."""
        self.path_for(journal.publication_id)
        validate_journal(journal)
        # Only journals with a final outcome may retire
        if not journal.resolved or journal.phase not in (
            PublicationPhase.COMMITTED, PublicationPhase.ROLLED_BACK,
        ):
            raise PublicationJournalError("only resolved journals can retire")
        active = self.path_for(journal.publication_id)
        # The stored copy must match the caller's copy before the move
        if self.load(active).to_dict() != journal.to_dict():
            raise PublicationJournalError("journal changed before retirement")
        self.retired_root.mkdir(parents=True, exist_ok=True)
        target = self.retired_root / active.name
        if target.exists():
            raise PublicationJournalError("retired journal already exists")
        replace_file(active, target)
        # Re-read the retired file so a failed move is detected
        if self.load(target).to_dict() != journal.to_dict():
            raise PublicationJournalError("retired journal failed verification")
        return target

    def discard_prepared(self, journal: PublicationJournal) -> None:
        """Delete a prepared journal that never started publishing."""
        self.path_for(journal.publication_id)
        # Discard is limited to journals that were never published from
        if journal.publication_started or journal.phase != PublicationPhase.PREPARED:
            raise PublicationJournalError("only a prepared journal can be discarded")
        path = self.path_for(journal.publication_id)
        # The stored copy must match the caller's copy before deletion
        if self.load(path).to_dict() != journal.to_dict():
            raise PublicationJournalError("prepared journal changed before discard")
        path.unlink()
        # Remove the bound authority record when it exists
        authority = self._authority_path(journal.publication_id)
        if authority.exists():
            authority.unlink()

    def _authority_path(self, publication_id: str) -> Path:
        """Return the authority record path for one publication id."""
        self.path_for(publication_id)
        return self.root / "authority" / f"{publication_id}.json"

    def _validate_record_path(self, path: Path) -> None:
        """Reject record paths outside the active or retired storage roots."""
        if path.suffix != ".json":
            raise PublicationJournalError("publication journal path is invalid")
        self.path_for(path.stem)
        # Only the two storage directories may hold journal records
        parents = {self.root.absolute(), self.retired_root.absolute()}
        if path.parent.absolute() not in parents:
            raise PublicationJournalError("publication journal path is outside storage")


def _parse(raw: object) -> PublicationJournal:
    """Decode a raw mapping into a validated publication journal."""
    # The record must carry exactly the expected top-level field set
    fields = {
        "schema_version", "publication_id", "intent_fingerprint", "phase",
        "publication_fingerprint", "updated_at", "publication_started", "committed",
        "resolved", "result", "groups",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise PublicationJournalError("publication journal schema is invalid")
    groups = raw["groups"]
    # Groups must be a list before their members are decoded
    if not isinstance(groups, list):
        raise PublicationJournalError("publication journal groups are invalid")
    try:
        journal = PublicationJournal(
            publication_id=raw["publication_id"],
            intent_fingerprint=raw["intent_fingerprint"],
            phase=PublicationPhase(raw["phase"]),
            publication_started=raw["publication_started"],
            committed=raw["committed"],
            resolved=raw["resolved"],
            result=raw["result"],
            groups=[_parse_group(value) for value in groups],
            schema_version=raw["schema_version"],
            publication_fingerprint=raw["publication_fingerprint"],
            updated_at=raw["updated_at"],
        )
        # Flags decoded from JSON must be real booleans, not truthy values
        if any(not isinstance(value, bool) for value in (
            journal.publication_started, journal.committed, journal.resolved,
        )):
            raise PublicationJournalError("publication journal flags are invalid")
        validate_journal(journal)
        return journal
    except (KeyError, ValueError, TypeError, PublicationValidationError) as error:
        raise PublicationJournalError("publication journal fields are invalid") from error


def _parse_group(raw: object) -> PublicationGroup:
    """Decode one raw target group mapping into a publication group."""
    # Each group must carry exactly the expected field set
    fields = {
        "role", "ordinal", "target_relative", "stage_name", "recovery_name",
        "prior_existed", "prior_digest", "output_digest", "state",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise PublicationJournalError("publication group schema is invalid")
    return PublicationGroup(
        role=TargetRole(raw["role"]), ordinal=raw["ordinal"],
        target_relative=raw["target_relative"], stage_name=raw["stage_name"],
        recovery_name=raw["recovery_name"], prior_existed=raw["prior_existed"],
        prior_digest=raw["prior_digest"], output_digest=raw["output_digest"],
        state=GroupState(raw["state"]),
    )


def validate_journal(journal: PublicationJournal) -> None:
    """Reject any journal that violates schema, identity, or ordering rules."""
    # The schema version must be exact and not a boolean
    if journal.schema_version != PUBLICATION_SCHEMA or isinstance(journal.schema_version, bool):
        raise PublicationValidationError("publication journal schema is invalid")
    validate_publication_id(journal.publication_id)
    # Both fingerprints must be well-formed SHA-256 digests
    for digest in (journal.intent_fingerprint, journal.publication_fingerprint):
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise PublicationValidationError("publication fingerprint is invalid")
    # The fingerprint must still bind the current target groups
    if publication_fingerprint(journal.intent_fingerprint, journal.groups) != journal.publication_fingerprint:
        raise PublicationValidationError("publication target binding changed")
    # Timestamps must use the canonical UTC form
    _validate_timestamp(journal.updated_at)
    # A journal without targets cannot describe a publication
    if not journal.groups:
        raise PublicationValidationError("publication journal has no targets")
    # Group ordinals must match their positions in the plan
    for index, group in enumerate(journal.groups):
        if not isinstance(group.ordinal, int) or isinstance(group.ordinal, bool) or group.ordinal != index:
            raise PublicationValidationError("publication target order is invalid")
        validate_relative_path(group.target_relative, "publication target")
        expected_stage = f".serverman-{journal.publication_id}-{index}.stage"
        expected_recovery = f".serverman-{journal.publication_id}-{index}.recovery"
        # Stage and recovery names derive from the publication id and ordinal
        if group.stage_name != expected_stage or group.recovery_name != expected_recovery:
            raise PublicationValidationError("publication artifact identity is invalid")
        # Prior target proof fields must agree with each other
        if not isinstance(group.prior_existed, bool) or group.prior_existed != (
            group.prior_digest is not None
        ):
            raise PublicationValidationError("prior target proof is inconsistent")
        # Every recorded digest must be a well-formed SHA-256 value
        for digest in (group.output_digest, group.prior_digest):
            if digest is not None and (not isinstance(digest, str) or SHA256.fullmatch(digest) is None):
                raise PublicationValidationError("publication target digest is invalid")
        # Unchanged groups must prove identical prior and output digests
        if group.state == GroupState.UNCHANGED_VERIFIED and (
            not group.prior_existed or group.prior_digest != group.output_digest
        ):
            raise PublicationValidationError("unchanged publication proof is inconsistent")
    # Target paths must be unique when case is ignored
    targets = [group.target_relative.casefold() for group in journal.groups]
    if len(targets) != len(set(targets)):
        raise PublicationValidationError("publication targets are not unique")
    # The server keys target may appear once and must be last
    roles = [group.role for group in journal.groups]
    if roles.count(TargetRole.SERVER_KEYS_DIRECTORY) > 1:
        raise PublicationValidationError("server keys target is duplicated")
    if TargetRole.SERVER_KEYS_DIRECTORY in roles and roles[-1] != TargetRole.SERVER_KEYS_DIRECTORY:
        raise PublicationValidationError("server keys target must be last")
    # Role and path combinations must be consistent
    for group in journal.groups:
        if group.role == TargetRole.SERVER_KEYS_DIRECTORY and group.target_relative.casefold() != "keys":
            raise PublicationValidationError("server keys target is invalid")
        if group.role == TargetRole.MANAGED_MOD_DIRECTORY and group.target_relative.casefold() == "keys":
            raise PublicationValidationError("managed mod target cannot claim the keys role")
    # The full state machine must still hold
    if not journal_state_is_legal(journal):
        raise PublicationValidationError("publication journal state is illegal")


def _validate_timestamp(value: object) -> None:
    """Require an ISO-8601 UTC timestamp with a trailing Z."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PublicationValidationError("publication timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise PublicationValidationError("publication timestamp is invalid") from error
    # Only timestamps that resolve to UTC exactly are accepted
    if parsed.tzinfo != UTC:
        raise PublicationValidationError("publication timestamp is invalid")


def _timestamp() -> str:
    """Return the current UTC time in the canonical journal format."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_intent_plan(journal: PublicationJournal, intent: PublicationIntent) -> None:
    """Require the journal target plan to match the trusted intent exactly."""
    expected = [
        (TargetRole.MANAGED_MOD_DIRECTORY, source.target_relative, source.output_digest)
        for source in intent.managed_sources
    ]
    actual = [(group.role, group.target_relative, group.output_digest) for group in journal.groups]
    if intent.keys:
        # A key target in the intent must be the final journal group
        if not actual or actual[-1][:2] != (TargetRole.SERVER_KEYS_DIRECTORY, "keys"):
            raise PublicationValidationError("publication target plan does not match intent")
        actual = actual[:-1]
    # Remaining groups must mirror the intent managed sources in order
    if actual != expected:
        raise PublicationValidationError("publication target plan does not match intent")
