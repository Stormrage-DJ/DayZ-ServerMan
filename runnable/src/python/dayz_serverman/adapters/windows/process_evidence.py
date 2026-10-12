"""Immutable child evidence and the existing safe environment contract."""
from __future__ import annotations

import os
from dataclasses import dataclass

# Keep the existing launch allowlist; secrets never enter a child environment.
SAFE_ENVIRONMENT_NAMES = frozenset({
    "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
    "COMSPEC", "PATH", "PATHEXT", "PROGRAMDATA",
})


class ProcessIdentityUnavailable(OSError):
    """Keep the existing identity-query error category and native error code."""
    def __init__(self, error_code: int) -> None:
        """Record the Windows error which prevented identity acquisition."""
        self.error_code = error_code
        super().__init__("SteamCMD process identity is unavailable")


def safe_child_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return only the existing allowlisted environment variables."""
    values = source if source is not None else dict(os.environ)
    return {name: value for name, value in values.items()
            if name.upper() in SAFE_ENVIRONMENT_NAMES}


@dataclass(frozen=True)
class ProcessIdentity:
    """Pair a process id with its creation identity to detect reuse."""
    process_id: int
    creation_identity: str

    def to_dict(self) -> dict[str, object]:
        """Return the existing closed identity mapping."""
        return {"process_id": self.process_id, "creation_identity": self.creation_identity}


@dataclass(frozen=True)
class ChildEvidence:
    """Preserve the schema-1 root, member and private-job evidence values."""
    process_id: int
    creation_identity: str
    members: tuple[ProcessIdentity, ...] = ()
    job_name: str | None = None

    @property
    def normalized_members(self) -> tuple[ProcessIdentity, ...]:
        """Keep the root first without changing existing recorded members."""
        root = ProcessIdentity(self.process_id, self.creation_identity)
        values = self.members or (root,)
        return values if root in values else (root, *values)

    def to_dict(self) -> dict[str, object]:
        """Return the unchanged versioned recovery evidence mapping."""
        return {"schema_version": 1, "process_id": self.process_id,
                "creation_identity": self.creation_identity, "job_name": self.job_name,
                "members": [value.to_dict() for value in self.normalized_members]}
