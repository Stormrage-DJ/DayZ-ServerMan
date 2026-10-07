"""Windows-safe path bindings for managed-mod publication."""

from __future__ import annotations

import os
import re
import shutil
import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

from ...domain.profiles import ProfileValidationError, validate_relative_path
from ...domain.mod_publication import PublicationIntent, PublicationJournal
from ...repositories.backup_verification import is_reparse, path_has_reparse
from ...repositories.backup_verification import sha256_file
from ...repositories.workshop_cache import _has_alternate_stream
from .shared_files import open_shared, read_bytes_shared, read_text_shared


# Names of manager-owned stage and recovery artifacts that publication may touch
ARTIFACT = re.compile(r"\.serverman-[a-z0-9-]+-[0-9]+\.(?:stage|recovery)")


class PublicationPathError(RuntimeError):
    """Raised when a publication path or artifact fails validation."""
    pass


def safe_dayz_root(root: Path) -> Path:
    """Validate and resolve the DayZ root as a local, reparse-free directory."""
    raw = str(root).replace("/", "\\")
    # Reject UNC shares and alternate-data-stream spellings before touching disk
    if not root.is_absolute() or raw.startswith("\\\\") or ":" in raw[2:]:
        raise PublicationPathError("DayZ root must be a local absolute path")
    # The root must be an existing directory free of reparse points
    if not root.is_dir() or path_has_reparse(root):
        raise PublicationPathError("DayZ root is missing or contains a reparse point")
    return root.resolve(strict=True)


def dayz_root_identity(root: Path) -> str:
    """Derive a stable identity for the resolved DayZ root."""
    canonical = safe_dayz_root(root)
    state = canonical.stat()
    # Bind identity to path, volume, and creation time so a replaced folder is detected
    payload = json.dumps({
        "domain": "dayz-root/v1", "path": str(canonical).casefold(),
        "device": state.st_dev, "file_identity": state.st_ino,
        "created_ns": state.st_ctime_ns,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_target(root: Path, relative: str) -> Path:
    """Return the publication target directory inside the DayZ root."""
    try:
        normalized = validate_relative_path(relative, "publication target")
    except ProfileValidationError as error:
        # Surface the shared validator's message for the caller's context
        raise PublicationPathError(str(error)) from error
    assert normalized is not None
    # Join Windows-style parts so alternating separators cannot escape the root
    target = root.joinpath(*PureWindowsPath(normalized).parts)
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise PublicationPathError("publication target escapes DayZ root") from error
    # The immediate parent must be a real directory, not a reparse point
    if not target.parent.is_dir() or path_has_reparse(target.parent):
        raise PublicationPathError("publication target parent is unsafe")
    # An existing target must itself be a plain directory
    if target.exists() and (not target.is_dir() or is_reparse(target)):
        raise PublicationPathError("publication target is not a safe directory")
    # Publication writes into the target's parent and needs it writable
    if not os.access(target.parent, os.W_OK):
        raise PublicationPathError("publication target parent is not writable")
    return target


def artifact_paths(target: Path, publication_id: str, ordinal: int) -> tuple[Path, Path]:
    """Return the stage and recovery paths for one artifact ordinal."""
    # Manager-owned hidden names carry the publication id and ordinal
    stage_name = f".serverman-{publication_id}-{ordinal}.stage"
    recovery_name = f".serverman-{publication_id}-{ordinal}.recovery"
    # Both names must satisfy the artifact pattern before either is used
    if ARTIFACT.fullmatch(stage_name) is None or ARTIFACT.fullmatch(recovery_name) is None:
        raise PublicationPathError("publication artifact identity is invalid")
    return target.with_name(stage_name), target.with_name(recovery_name)


def resolve_artifacts(root: Path, relative: str, stage: str, recovery: str) -> tuple[Path, Path, Path]:
    """Validate recorded artifact names and rebuild their exact paths."""
    target = safe_target(root, relative)
    # Recorded names must match the artifact pattern before being trusted
    if ARTIFACT.fullmatch(stage) is None or ARTIFACT.fullmatch(recovery) is None:
        raise PublicationPathError("publication artifact name is invalid")
    # Recompute the expected names from the publication id and ordinal
    expected_stage, expected_recovery = artifact_paths(
        target, stage.removeprefix(".serverman-").rsplit("-", 1)[0],
        int(stage.removesuffix(".stage").rsplit("-", 1)[1]),
    )
    # Mismatched role names mean the journal cannot be trusted
    if expected_stage.name != stage or expected_recovery.name != recovery:
        raise PublicationPathError("publication artifact role is invalid")
    return target, target.with_name(stage), target.with_name(recovery)


def copy_tree(
    source: Path,
    target: Path,
    checkpoint: Callable[[str, int], None],
    ordinal: int,
) -> None:
    """Copy a workshop tree into the target, rejecting unsafe entries."""
    # The source must be a plain directory, never a reparse point
    if not source.is_dir() or is_reparse(source):
        raise PublicationPathError("publication copy source is unsafe")
    target.mkdir()
    # Walk without following links so the copy cannot leave the source tree
    for parent, directories, files in os.walk(source, followlinks=False):
        base = Path(parent)
        destination = target / base.relative_to(source)
        for name in directories:
            path = base / name
            # Reject name streams and reparse points before creating directories
            if is_reparse(path) or ":" in name:
                raise PublicationPathError("publication copy source changed")
            (destination / name).mkdir()
        for name in files:
            # Checkpoint before each file so progress survives interruption
            checkpoint("COPY_FILE", ordinal)
            path = base / name
            # Re-check the entry so a concurrent swap cannot sneak content in
            if is_reparse(path) or not path.is_file() or ":" in name:
                raise PublicationPathError("publication copy source changed")
            copy_file(path, destination / name, sha256_file(path))


def copy_file(source: Path, target: Path, digest: str) -> None:
    """Copy one file with a durable flush and verify the published digest."""
    # Stream through a bounded buffer into an exclusively created target
    with open_shared(source) as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        # Force the bytes to disk so a crash cannot leave torn content
        os.fsync(writer.fileno())
    # Re-read the copy and compare it with the source digest
    if sha256_file(target) != digest:
        raise PublicationPathError("publication copy failed verification")


def validate_existing_keys(target: Path) -> None:
    """Require every existing keys-directory entry to be a plain .bikey file."""
    # Links, streams, and non-key files disqualify the whole directory
    for entry in target.iterdir():
        if (
            not entry.is_file() or is_reparse(entry) or _has_alternate_stream(entry)
            or entry.suffix.casefold() != ".bikey"
        ):
            raise PublicationPathError("existing keys directory has an unsupported entry")


def persist_authority(root: Path, journal: PublicationJournal, intent: PublicationIntent) -> Path:
    """Write the signed publication authority record and return its path."""
    # The approved intent must match the journal before authority is recorded
    if intent.publication_id != journal.publication_id or intent.fingerprint != journal.intent_fingerprint:
        raise PublicationPathError("trusted publication intent does not match journal")
    body = authority_body(journal)
    # Sign the canonical body so later checks need no trust in the file itself
    record = {"schema_version": 1, "body": body, "signature": _sign(body, _key(root, True))}
    path = root / "authority" / f"{journal.publication_id}.json"
    # Authority records live in a dedicated directory under the DayZ root
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_reparse(path.parent):
        raise PublicationPathError("publication authority storage is unsafe")
    # An existing record is verified instead of rewritten
    if path.exists():
        verify_authority(root, journal)
        return path
    # Stage beside the final name so the publish step stays on one volume
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        # Write newline-stable JSON, then flush it durably before linking
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # A hard link publishes atomically and never overwrites an existing record
            os.link(temporary, path)
        except FileExistsError:
            # A concurrent writer won; verify its record instead
            verify_authority(root, journal)
            return path
        verify_authority(root, journal)
    finally:
        # Remove the staged file whether publication succeeded or not
        if temporary.exists():
            temporary.unlink()
    verify_authority(root, journal)
    return path


def verify_authority(root: Path, journal: PublicationJournal) -> None:
    """Verify the stored authority record against the journal."""
    path = root / "authority" / f"{journal.publication_id}.json"
    try:
        raw = json.loads(read_text_shared(path, encoding="utf-8"))
        # Only the exact record shape is accepted; anything else is invalid
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "body", "signature"}:
            raise PublicationPathError("publication authority schema is invalid")
        # Version 1 is the only supported authority schema
        if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
            raise PublicationPathError("publication authority schema is invalid")
        signature = raw["signature"]
        # Signatures are 64 hex characters produced by HMAC-SHA256
        if not isinstance(signature, str) or len(signature) != 64:
            raise PublicationPathError("publication authority signature is invalid")
        # Constant-time comparison avoids leaking signature bytes
        if not hmac.compare_digest(signature, _sign(raw["body"], _key(root, False))):
            raise PublicationPathError("publication authority signature is invalid")
        # The signed body must equal the journal-derived authority body
        if raw["body"] != authority_body(journal):
            raise PublicationPathError("publication authority does not match journal")
    except PublicationPathError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise PublicationPathError("publication authority is unreadable") from error


def authority_body(journal: PublicationJournal) -> dict[str, object]:
    """Return the canonical authority body derived from the journal."""
    # Flatten every journal group into the signed body
    return {
        "publication_id": journal.publication_id,
        "intent_fingerprint": journal.intent_fingerprint,
        "publication_fingerprint": journal.publication_fingerprint,
        "targets": [{
            "role": group.role.value, "ordinal": group.ordinal,
            "target_relative": group.target_relative, "stage_name": group.stage_name,
            "recovery_name": group.recovery_name, "prior_existed": group.prior_existed,
            "prior_digest": group.prior_digest, "output_digest": group.output_digest,
        } for group in journal.groups],
    }


def _key(root: Path, create: bool) -> bytes:
    """Return the 32-byte authority key, creating it when requested."""
    path = root / ".authority-key"
    if not path.exists() and create:
        root.mkdir(parents=True, exist_ok=True)
        try:
            # Exclusive creation keeps two writers from disagreeing on the key
            with path.open("xb") as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            # Another writer created the key first; read theirs below
            pass
    key = read_bytes_shared(path)
    # The key must be exactly 32 bytes from a plain, non-reparse file
    if len(key) != 32 or not path.is_file() or is_reparse(path):
        raise PublicationPathError("publication authority key is invalid")
    return key


def _sign(body: object, key: bytes) -> str:
    """Sign a canonical JSON body with HMAC-SHA256."""
    # Canonical separators and sorted keys keep signatures reproducible
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()
