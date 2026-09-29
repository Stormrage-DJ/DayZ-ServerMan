"""Non-secret identity proofs shared by Workshop update and publication."""

from __future__ import annotations

import hashlib
import json
import unicodedata

from ..domain.workshop import AuthenticationMode


def authentication_identity_digest(mode: AuthenticationMode, account: str | None) -> str:
    """Return a stable digest of the non-secret authentication identity."""
    # Fold Unicode and case so equivalent account spellings share one identity
    identity = None if account is None else unicodedata.normalize("NFC", account).casefold()
    # Tag the payload with a versioned domain so digests cannot collide across purposes
    body = {
        "domain": "dayz-serverman/authentication-identity/v1",
        "authentication_mode": mode.value,
        "account_identity": identity,
    }
    # Serialize with sorted keys for a reproducible digest across runs
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
