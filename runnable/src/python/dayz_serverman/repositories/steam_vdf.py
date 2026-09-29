"""Bounded duplicate-rejecting parser for Steam text VDF evidence."""

from __future__ import annotations

from typing import Any


class VdfError(ValueError):
    """Raised when Steam text VDF content is malformed."""
    pass


def parse_vdf(text: str) -> dict[str, Any]:
    """Parse bounded Steam text VDF into nested dictionaries."""
    # Refuse oversized manifests before tokenizing them
    if len(text) > 4 * 1024 * 1024:
        raise VdfError("Steam manifest is too large")
    tokens = _tokens(text)
    position = 0

    def parse_object(expect_close: bool) -> dict[str, Any]:
        """Parse one object body, consuming its close marker when expected."""
        nonlocal position
        result: dict[str, Any] = {}
        while position < len(tokens):
            token = tokens[position]
            if token == "}":
                if not expect_close:
                    raise VdfError("Steam manifest has an unexpected close marker")
                position += 1
                return result
            if token == "{":
                raise VdfError("Steam manifest has an unexpected object marker")
            key = token
            position += 1
            if position >= len(tokens):
                raise VdfError("Steam manifest has a missing value")
            value: Any
            if tokens[position] == "{":
                position += 1
                value = parse_object(True)
            elif tokens[position] == "}":
                raise VdfError("Steam manifest has a missing value")
            else:
                value = tokens[position]
                position += 1
            # Steam manifests treat keys case-insensitively, so duplicates are errors
            identity = key.casefold()
            if any(existing.casefold() == identity for existing in result):
                raise VdfError("Steam manifest has a duplicate field")
            result[key] = value
        if expect_close:
            raise VdfError("Steam manifest object is not closed")
        return result

    parsed = parse_object(False)
    # The whole document must be consumed by exactly one root object
    if position != len(tokens):
        raise VdfError("Steam manifest has trailing data")
    return parsed


def field(mapping: dict[str, Any], name: str) -> Any:
    """Return the single case-insensitive match for a manifest field name."""
    matches = [value for key, value in mapping.items() if key.casefold() == name.casefold()]
    # Require exactly one match so ambiguity is rejected
    if len(matches) != 1:
        raise VdfError(f"Steam manifest field {name} is missing")
    return matches[0]


def _tokens(text: str) -> list[str]:
    """Split manifest text into quoted tokens and brace markers."""
    tokens: list[str] = []
    index = 0
    while index < len(text):
        mark = text[index]
        # Skip whitespace between tokens
        if mark.isspace():
            index += 1
            continue
        # Braces are structural markers; every other token must be quoted
        if mark in "{}":
            tokens.append(mark)
            index += 1
            continue
        if mark != '"':
            raise VdfError("Steam manifest contains an unquoted token")
        index += 1
        value: list[str] = []
        while index < len(text) and text[index] != '"':
            # A backslash escapes the next character inside the string
            if text[index] == "\\":
                index += 1
                if index >= len(text):
                    raise VdfError("Steam manifest has an incomplete escape")
            value.append(text[index])
            index += 1
        if index >= len(text):
            raise VdfError("Steam manifest has an unterminated string")
        tokens.append("".join(value))
        index += 1
        # Bound the token count so hostile manifests cannot exhaust memory
        if len(tokens) > 100_000:
            raise VdfError("Steam manifest has too many tokens")
    return tokens
