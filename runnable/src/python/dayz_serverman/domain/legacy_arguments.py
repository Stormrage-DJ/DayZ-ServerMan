"""Windows legacy argument conversion without shell interpretation."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .profiles import ProfileValidationError, validate_extra_argument


# Legacy DayZ switches that map to structured profile fields
KNOWN_OPTIONS = {
    "config": "config_path",
    "port": "game_port",
    "profiles": "profiles_path",
    "mission": "mission_path",
    "mod": "mods",
    "servermod": "server_mods",
}
# Characters that would alter shell parsing if left unquoted
SHELL_METACHARACTERS = frozenset("&|<>^")


@dataclass(frozen=True)
class LegacyArgumentConversion:
    """Conversion result with structured fields, extras, warnings, and conflicts."""

    structured: Mapping[str, str]
    extra_arguments: tuple[str, ...]
    warnings: tuple[str, ...]
    conflicts: tuple[dict[str, str], ...]


def tokenize_windows_arguments(raw: object) -> tuple[str, ...]:
    """Tokenize a legacy argument string with the Windows command-line parser."""
    if not isinstance(raw, str):
        raise ValueError("legacy arguments must be text")
    # Screen the raw text before handing it to Windows
    _validate_raw(raw)
    if not raw.strip():
        return ()
    # The platform command-line parser only exists on Windows
    if os.name != "nt":
        raise OSError("legacy argument conversion requires Windows")
    # Prepend a dummy executable so parsing starts at the first real argument
    command = f'"DayZServer_x64.exe" {raw}'
    # Bind the Windows APIs that split a command line into arguments
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    pointer = shell32.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        raise ctypes.WinError(ctypes.get_last_error())
    # Copy the argument vector and release the Windows-owned buffer
    try:
        return tuple(pointer[index] for index in range(1, count.value))
    finally:
        kernel32.LocalFree(pointer)


def convert_legacy_arguments(
    raw: object,
    structured: Mapping[str, object],
) -> LegacyArgumentConversion:
    """Convert legacy arguments over existing structured values."""
    # Start from the structured values already in effect
    normalized = {key: str(value) for key, value in structured.items() if value not in (None, "")}
    # Unparsable command lines fail closed with a conflict
    try:
        tokens = tokenize_windows_arguments(raw)
    except (OSError, ValueError) as error:
        return LegacyArgumentConversion(
            MappingProxyType(normalized), (), (),
            ({"code": "UNSAFE_ARGUMENTS", "message": str(error)},),
        )
    seen: set[str] = set()
    extras: list[str] = []
    warnings: list[str] = []
    conflicts: list[dict[str, str]] = []
    # Extras pass validation; known switches merge into structured fields
    for token in tokens:
        known = _known(token)
        if known is None:
            try:
                extras.append(validate_extra_argument(token))
            except ProfileValidationError as error:
                conflicts.append({"code": "UNSAFE_ARGUMENTS", "message": str(error)})
            continue
        field, value = known
        # A repeated switch is a conflict, never a silent overwrite
        if field in seen:
            conflicts.append({
                "code": "DUPLICATE_KNOWN_ARGUMENT",
                "message": f"Legacy arguments repeat {field}.",
            })
            continue
        seen.add(field)
        # Matching values warn as redundant; conflicting values block
        if field in normalized and normalized[field].casefold() != value.casefold():
            conflicts.append({
                "code": "STRUCTURED_ARGUMENT_CONFLICT",
                "message": f"Legacy arguments conflict with {field}.",
            })
        elif field in normalized:
            warnings.append(f"Removed redundant legacy argument for {field}.")
        else:
            normalized[field] = value
    return LegacyArgumentConversion(
        MappingProxyType(normalized), tuple(extras), tuple(warnings), tuple(conflicts),
    )


def _validate_raw(raw: str) -> None:
    """Reject control characters, unbalanced quotes, and unquoted metacharacters."""
    if any(mark in raw for mark in "\x00\r\n"):
        raise ValueError("legacy arguments contain a control character")
    # Track quote state and backslash runs while scanning
    quoted = False
    slashes = 0
    for character in raw:
        if character == "\\":
            slashes += 1
            continue
        if character == '"' and slashes % 2 == 0:
            quoted = not quoted
        elif not quoted and character in SHELL_METACHARACTERS:
            raise ValueError("legacy arguments contain an unquoted shell metacharacter")
        slashes = 0
    # An open quote at the end marks the text as unparsable
    if quoted:
        raise ValueError("legacy arguments contain an unmatched quote")


def _known(token: str) -> tuple[str, str] | None:
    """Return the structured field and value for a known legacy switch."""
    # Only --name=value tokens count as structured switches
    if not token.startswith("-") or "=" not in token:
        return None
    option, value = token[1:].split("=", 1)
    field = KNOWN_OPTIONS.get(option.casefold())
    return (field, value) if field is not None else None
