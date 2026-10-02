"""Locate config assignments without treating comments or strings as syntax."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .configuration_common import ConfigurationFileError

# Catch every non-whitespace character so malformed syntax cannot disappear.
LEXEME = re.compile(r'//[^\r\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|[A-Za-z_][\w]*|[+-]?[0-9]+|[^\s]')


@dataclass(frozen=True)
class Assignment:
    """One assignment's class context and value span in the original text."""

    context: tuple[str, ...]
    key: str
    start: int
    end: int
    raw: str


def assignments(text: str) -> tuple[Assignment, ...]:
    """Read assignment spans; reject unbalanced or incomplete config syntax."""
    tokens = []
    for match in LEXEME.finditer(text):
        raw = match.group()
        if raw.startswith(("//", "/*")):
            continue
        tokens.append((raw, match.start(), match.end()))
    # Unterminated comments and strings survive lexing as bare delimiters.
    if any(token[0] == '"' for token in tokens):
        raise ConfigurationFileError("server config contains an unterminated string")
    if any(tokens[i][0] == "/" and tokens[i + 1][0] == "*"
           for i in range(len(tokens) - 1)):
        raise ConfigurationFileError("server config contains an unterminated comment")
    found: list[Assignment] = []
    _scan(text, tokens, 0, (), found, nested=False)
    return tuple(found)


def _scan(text: str, tokens: list, index: int, context: tuple[str, ...],
          found: list[Assignment], *, nested: bool) -> int:
    """Walk class bodies while keeping each assignment in its exact context."""
    while index < len(tokens):
        raw = tokens[index][0]
        if raw == "}":
            if not nested:
                raise ConfigurationFileError("server config contains an unexpected closing brace")
            return index + 1
        if raw.casefold() == "class":
            # Accept named class definitions, inheritance and forward declarations.
            index = _class(text, tokens, index, context, found)
            continue
        if index + 1 < len(tokens) and tokens[index + 1][0] == "=":
            # Consume the complete value so quoted class-like text cannot alter context.
            first = index + 2
            index = _value_end(tokens, first)
            if first == index:
                raise ConfigurationFileError("server config contains an empty assignment")
            start, end = tokens[first][1], tokens[index - 1][2]
            value = " ".join(token[0] for token in tokens[first:index])
            found.append(Assignment(context, raw.casefold(), start, end, value))
            index += 1
            continue
        if raw == "{":
            raise ConfigurationFileError("server config contains an unnamed block")
        index += 1
    if nested:
        raise ConfigurationFileError("server config contains an unclosed class")
    return index


def _class(text: str, tokens: list, index: int, context: tuple[str, ...],
           found: list[Assignment]) -> int:
    """Enter a named class body or consume a forward declaration."""
    index += 1
    if index >= len(tokens) or re.fullmatch(r"[A-Za-z_]\w*", tokens[index][0]) is None:
        raise ConfigurationFileError("server config contains an invalid class name")
    name = tokens[index][0].casefold()
    index += 1
    if index < len(tokens) and tokens[index][0] == ":":
        index += 1
        if index >= len(tokens) or re.fullmatch(r"[A-Za-z_]\w*", tokens[index][0]) is None:
            raise ConfigurationFileError("server config contains invalid class inheritance")
        index += 1
    if index < len(tokens) and tokens[index][0] == ";":
        return index + 1
    if index >= len(tokens) or tokens[index][0] != "{":
        raise ConfigurationFileError("server config class body is missing")
    return _scan(text, tokens, index + 1, (*context, name), found, nested=True)


def _value_end(tokens: list, index: int) -> int:
    """Find the assignment terminator without confusing array braces with classes."""
    depth = 0
    while index < len(tokens):
        raw = tokens[index][0]
        if raw == "{":
            depth += 1
        elif raw == "}":
            depth -= 1
            if depth < 0:
                break
        elif raw == ";" and depth == 0:
            return index
        index += 1
    raise ConfigurationFileError("server config assignment terminator is missing")
