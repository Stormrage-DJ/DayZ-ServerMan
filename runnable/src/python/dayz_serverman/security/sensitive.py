"""Canonical credential option detection and text redaction."""

from __future__ import annotations

import re
from collections.abc import Sequence


# Canonical option names treated as credentials, without separators
SENSITIVE_NAMES = frozenset({
    "apikey", "authorization", "auth", "bearer", "token", "password", "passwd",
    "secret", "clientsecret", "accesstoken", "authtoken", "bearertoken",
    "credential", "cookie", "passphrase",
})
# Shared credential-name alternation used by the redaction patterns below
_NAME_PATTERN = (
    r"api[\s_-]*key|authorization|auth|bearer|token|password|passwd|secret|"
    r"client[\s_-]*secret|access[\s_-]*token|auth[\s_-]*token|"
    r"bearer[\s_-]*token|credential|cookie|passphrase"
)
# Matches name=value and name: value assignments carrying credentials
_ASSIGNMENT = re.compile(
    rf"(?P<name>\b(?:{_NAME_PATTERN})\b)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*'|"
    r"(?:Bearer|Basic)\s+[^&;,\s#]+|[^&;,\s#]+)",
    re.I,
)
# Matches the space-separated Bearer scheme credential
_BEARER = re.compile(r"(?P<name>\bBearer\b)(?P<separator>\s+)(?P<value>[^&;,\s#]+)", re.I)
# Matches credential command-line options whose value is a separate token
_OPTION_SEPARATE = re.compile(
    rf"(?P<name>(?:--?|/{{1,2}})(?:{_NAME_PATTERN}))(?P<separator>\s+)"
    r"(?P<value>(?:Bearer|Basic)\s+[^&;,\s#]+|\"(?:\\.|[^\"\\\r\n])*\"|"
    r"'(?:\\.|[^'\\\r\n])*'|[^&;,\s#]+)",
    re.I,
)
# Matches credential names whose equal or colon value is empty or missing
_EMPTY_ASSIGNMENT = re.compile(
    rf"(?P<name>\b(?:{_NAME_PATTERN})\b)(?P<separator>\s*[:=]\s*)(?=$|[&;,#])",
    re.I,
)


def canonical_option_name(value: str) -> str:
    """Normalize an option token to a comparable lower-case alphanumeric name."""
    # Strip option prefixes and any attached value before comparison
    token = value.strip().lstrip("-+/")
    token = re.split(r"[:=]", token, maxsplit=1)[0]
    return "".join(character for character in token.casefold() if character.isalnum())


def is_sensitive_name(value: str) -> bool:
    """Report whether a name matches a credential name or a credential suffix."""
    canonical = canonical_option_name(value)
    # Accept known names and common decorated suffixes such as db_password
    return canonical in SENSITIVE_NAMES or canonical.endswith((
        "apikey", "token", "password", "passwd", "secret", "credential",
    ))


def contains_sensitive_arguments(tokens: Sequence[str]) -> bool:
    """Report whether an argument list appears to carry credential material."""
    # Scan in order because a bare credential name consumes the next token
    expect_value = False
    for token in tokens:
        stripped = token.strip()
        name = canonical_option_name(stripped)
        # A preceding credential name takes this token as its value
        if expect_value:
            return True
        if is_sensitive_name(name):
            if ":" in stripped or "=" in stripped:
                return True
            expect_value = True
            continue
        # Report embedded assignments and bearer values in a single token
        if _ASSIGNMENT.search(stripped) or _BEARER.search(stripped):
            return True
    # A trailing credential name is still treated as sensitive
    return expect_value


def redact_text(value: str) -> str:
    """Return text with credential values replaced by a redaction marker."""
    # Replace values in option, assignment, and empty-value forms in turn
    redacted = _OPTION_SEPARATE.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}[REDACTED]",
        value,
    )
    redacted = _ASSIGNMENT.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}[REDACTED]",
        redacted,
    )
    redacted = _EMPTY_ASSIGNMENT.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}[REDACTED]",
        redacted,
    )
    return _BEARER.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}[REDACTED]",
        redacted,
    )


def redact_argument_tokens(values: Sequence[str]) -> list[str]:
    """Return a copy of argument tokens with credential values masked."""
    result: list[str] = []
    redact_next = False
    redact_bearer_value = False
    # Walk tokens in order, tracking whether a value is expected next
    for token in values:
        if redact_bearer_value:
            result.append("[REDACTED]")
            redact_bearer_value = False
            continue
        if redact_next:
            # Keep the scheme word visible and mask the value that follows it
            if token.casefold() in {"bearer", "basic"}:
                result.append(token)
                redact_bearer_value = True
            else:
                result.append("[REDACTED]")
            redact_next = False
            continue
        name = canonical_option_name(token)
        if is_sensitive_name(name):
            # Split at the earliest separator to preserve the option prefix
            separators = [index for mark in ("=", ":") if (index := token.find(mark)) >= 0]
            if separators:
                index = min(separators)
                prefix, separator, tail = token[:index], token[index], token[index + 1:]
                result.append(prefix + separator + "[REDACTED]")
                # An empty attached value may still appear in the next token
                if not tail.strip():
                    redact_next = True
            else:
                result.append(token)
                redact_next = True
        else:
            result.append(redact_text(token))
    return result
