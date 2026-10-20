"""Strict durable records for coordinated profile and server configuration publication."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from ..domain.profiles import ProfileInput
from .profile_migration import parsed_fields, serialize_profile_document


class ConfigurationPublicationError(RuntimeError):
    """Describe a failed publication and whether its evidence requires recovery."""

    def __init__(self, code: str, message: str, *, recovery_required: bool = False) -> None:
        """Keep the existing operation error classification."""
        self.code, self.recovery_required = code, recovery_required
        super().__init__(message)


@dataclass
class ConfigurationGroup:
    """One exact native file group consumed by the shared publication engine."""

    target_path: str
    staging_path: str
    recovery_path: str
    old_digest: str
    new_digest: str
    state: str = "READY"
    old_existed: bool = True
    created_ancestors: tuple[str, ...] = ()


@dataclass
class ConfigurationJournal:
    """Configuration-specific journal with one separately committed profile successor."""

    operation_id: str
    dayz_root: str
    profile_id: str
    settings_revision: int
    old_profile: dict[str, Any]
    old_profile_digest: str
    new_profile_digest: str
    new_port: int
    profile_stage: str
    groups: list[ConfigurationGroup]
    phase: str = "READY"
    publication_started: bool = False
    resolved: bool = False
    result: str | None = None
    schema_version: int = 1

    @property
    def committed(self) -> bool:
        """Report the durable terminal commit state."""
        return self.phase == "COMMITTED" and self.resolved and self.result == "COMMITTED"

    def document(self) -> dict[str, Any]:
        """Return the internal journal document, never a public configuration value."""
        return asdict(self)


def successor_bytes(old: dict[str, Any], port: int) -> bytes:
    """Validate and serialize an exact next-revision record in the existing format."""
    fields = {key: value for key, value in old.items() if key not in ("revision", "schema_version")}
    candidate = ProfileInput.parse({**fields, "game_port": port})
    return serialize_profile_document({"schema_version": 2, "revision": old["revision"] + 1,
                                       **parsed_fields(candidate)}).encode("utf-8")


def parse_journal(value: object) -> ConfigurationJournal:
    """Reject malformed or contradictory records before their paths can be used."""
    if not isinstance(value, dict) or set(value) != set(ConfigurationJournal.__dataclass_fields__):
        raise ValueError("configuration journal fields are invalid")
    groups = value["groups"]
    if not isinstance(groups, list) or len(groups) != 1:
        raise ValueError("configuration journal must contain one native group")
    group = groups[0]
    if not isinstance(group, dict) or set(group) != set(ConfigurationGroup.__dataclass_fields__):
        raise ValueError("configuration group fields are invalid")
    group = ConfigurationGroup(**{**group, "created_ancestors": tuple(group["created_ancestors"])})
    journal = ConfigurationJournal(**{**value, "groups": [group]})
    if not isinstance(journal.operation_id, str) or re.fullmatch(r"[0-9a-f]{32}", journal.operation_id) is None:
        raise ValueError("configuration operation identity is invalid")
    if type(journal.schema_version) is not int or journal.schema_version != 1:
        raise ValueError("configuration journal version is invalid")
    if type(journal.settings_revision) is not int or journal.settings_revision < 0:
        raise ValueError("configuration settings revision is invalid")
    old = journal.old_profile
    if not isinstance(old, dict) or type(old.get("revision")) is not int or old["revision"] < 0:
        raise ValueError("configuration profile revision is invalid")
    if type(old.get("schema_version")) is not int or old["schema_version"] != 2:
        raise ValueError("configuration profile schema is invalid")
    fields = {key: item for key, item in old.items() if key not in ("schema_version", "revision")}
    profile = ProfileInput.parse(fields)
    if profile.profile_id != journal.profile_id:
        raise ValueError("configuration profile identity is invalid")
    if journal.phase not in {"READY", "PUBLISHING", "COMPENSATING", "ROLLED_BACK", "COMMITTING", "COMMITTED"}:
        raise ValueError("configuration publication phase is invalid")
    if group.state not in {"READY", "PUBLISHING", "PUBLISHED", "COMPENSATED"}:
        raise ValueError("configuration group state is invalid")
    if group.old_existed is not True or group.created_ancestors:
        raise ValueError("configuration publication cannot add or remove targets")
    for digest in (group.old_digest, group.new_digest, journal.old_profile_digest, journal.new_profile_digest):
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("configuration digest is invalid")
    if type(journal.publication_started) is not bool or type(journal.resolved) is not bool:
        raise ValueError("configuration phase flags are invalid")
    terminal = journal.phase in {"COMMITTED", "ROLLED_BACK"}
    if journal.resolved != terminal or journal.result != (journal.phase if terminal else None):
        raise ValueError("configuration terminal flags are invalid")
    if journal.phase in {"COMMITTING", "COMMITTED"} and (group.state != "PUBLISHED" or not journal.publication_started):
        raise ValueError("configuration commit has an unpublished group")
    return journal
