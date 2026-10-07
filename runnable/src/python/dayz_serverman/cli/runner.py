"""Runs one CLI command in the check order of R1 and A11: arguments, D2, the command, its end state (6.4).

Reads run in an observer session. A write first runs its data-dependent argument checks in an
observer session (6.4.1), then takes the instance lock in an owner session and runs its handler.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from .. import session as owner_sessions
from .. import session_observer as observer_sessions
from ..adapters.windows.instance_lock import InstanceActive, InstanceLockUnsupported
from .bridge_client import BridgeClient, CliBridgeError, ObserverClient
from .exit_codes import FAILED, REFUSED, USAGE
from .flow import stop_if_interrupted
from .interrupts import Interrupts
from .output import CliFailure, CommandResult, Output, sentence
from .parser import HelpRequest, NoCommand, UsageError, command_list, parse
from .profiles import resolve_profile
from .registry import CommandSpec, Kind, ProfileRule
from .wording import INSTANCE_LOCK_UNSUPPORTED, INTERNAL, NOT_AVAILABLE, instance_active, usage

# Holder name of a command in the holder file of the instance lock (3.1)
HOLDER = "command"
# Reads that go on without a profile when none fits the default rule
OPTIONAL_PROFILE = frozenset(("status",))


@dataclass
class CommandContext:
    """What a command handler gets: its options, its bridge calls, its profile and the output."""

    spec: CommandSpec
    options: argparse.Namespace
    call: Callable[..., Any]
    session: Any
    output: Output
    interrupts: Interrupts
    profile: Mapping[str, Any] | None = None
    resolved: dict[str, Any] = field(default_factory=dict)
    # Input of a confirmation question; None or a stream that is not a terminal never asks (8.1)
    stdin: TextIO | None = None


# A command handler and a pre-step, as the registry names them
Handler = Callable[[CommandContext], CommandResult]
Prestep = Callable[[Callable[..., Any], argparse.Namespace, "str | None"], dict[str, Any]]


def load_handler(spec: CommandSpec) -> Handler:
    """Return the handler of a command; a PENDING command refuses before any session opens."""
    if spec.handler is None:
        raise CliFailure("USAGE", sentence(NOT_AVAILABLE), USAGE)
    return _named_function(spec.handler)


def _named_function(reference: str) -> Any:
    """Import "module:function" under this package."""
    module_name, function_name = reference.split(":", 1)
    return getattr(importlib.import_module(f"{__package__}.{module_name}"), function_name)


def run(arguments: list[str], application_root: Path | None, *, stdout: TextIO, stderr: TextIO,
        interrupts: Interrupts, stdin: TextIO | None = None) -> int:
    """Run one command line and return its exit code; stdout holds only the result."""
    output = Output(stdout, stderr, json_mode="--json" in arguments)
    output.stdin = stdin
    # (1) Arguments: the syntax of the command line
    try:
        parsed = parse(arguments)
    except NoCommand:
        stderr.write(command_list() + "\n")
        return USAGE
    except UsageError as error:
        output.command = error.command or ""
        return output.failure(CliFailure("USAGE", usage(error.message, error.command), USAGE))
    if isinstance(parsed, HelpRequest):
        stdout.write(parsed.text + "\n")
        return 0
    output.command = parsed.spec.name
    try:
        handler = load_handler(parsed.spec)
        if parsed.spec.kind is Kind.READ:
            result = _run_read(parsed.spec, parsed.options, handler, application_root, output, interrupts)
        else:
            result = _run_write(parsed.spec, parsed.options, handler, application_root, output, interrupts)
        return output.success(result)
    except CliFailure as failure:
        return output.failure(failure)
    except CliBridgeError as error:
        return output.failure(error.failure())
    except Exception:
        # No traceback and no file: the CLI writes nothing outside the lane (criterion 14)
        return output.failure(CliFailure("INTERNAL_FAILURE", sentence(INTERNAL), FAILED))


def _run_read(spec: CommandSpec, options: argparse.Namespace, handler: Handler, root: Path | None,
              output: Output, interrupts: Interrupts) -> CommandResult:
    """Run a read in an observer session: no lock, no recovery, no write (A4)."""
    session = observer_sessions.open_observer_session(root)
    try:
        client = ObserverClient(session)
        profile, resolved = _arguments_with_data(spec, options, client.call)
        result = handler(CommandContext(spec, options, client.call, session, output, interrupts, profile, resolved,
                                        output.stdin))
    finally:
        session.close()
    # Ctrl+C during a read: the call finished; the command ends cancelled (exit 5, QF-20)
    stop_if_interrupted(interrupts)
    return result


def _run_write(spec: CommandSpec, options: argparse.Namespace, handler: Handler, root: Path | None,
               output: Output, interrupts: Interrupts) -> CommandResult:
    """Run a write: argument checks that need data (2), then the instance lock (3), then the handler."""
    profile: Mapping[str, Any] | None = None
    resolved: dict[str, Any] = {}
    if spec.profile is not ProfileRule.NONE or spec.prestep is not None:
        # (1) continued: a read-only pre-step before the lock; it also works while the GUI runs
        observer = observer_sessions.open_observer_session(root)
        try:
            profile, resolved = _arguments_with_data(spec, options, ObserverClient(observer).call)
        finally:
            observer.close()
        # Ctrl+C during the read-only pre-step: nothing is submitted (exit 5)
        stop_if_interrupted(interrupts)
    # (2) D2: the instance lock of an owner session
    try:
        session = owner_sessions.open_owner_session(root, HOLDER, spec.name, require_byte_range_lock=True)
    except InstanceActive as refusal:
        holder = refusal.holder
        details = holder.to_details() if holder is not None else None
        raise CliFailure("INSTANCE_ACTIVE", instance_active(holder), REFUSED, True, details) from refusal
    except InstanceLockUnsupported as error:
        raise CliFailure("INSTANCE_LOCK_UNSUPPORTED", sentence(INSTANCE_LOCK_UNSUPPORTED), FAILED) from error
    try:
        client = BridgeClient(session.composition.bridge.dispatch)
        return handler(CommandContext(spec, options, client.call, session, output, interrupts, profile, resolved,
                                      output.stdin))
    finally:
        # A command waits for the lane without a limit (A8)
        session.close(drain_seconds=None)


def _arguments_with_data(spec: CommandSpec, options: argparse.Namespace,
                         call: Callable[..., Any]) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    """Resolve the profile (P4) and run the command's pre-step; every refusal here exits 2."""
    profile = None
    if spec.profile is not ProfileRule.NONE:
        # `backup list --all` lists every profile's backups, so it needs none
        optional = spec.name in OPTIONAL_PROFILE or bool(spec.name == "backup list" and options.all)
        profile = resolve_profile(call, spec.profile, getattr(options, "profile", None), optional=optional)
    resolved: dict[str, Any] = {}
    if spec.prestep is not None:
        prestep: Prestep = _named_function(spec.prestep)
        resolved = prestep(call, options, None if profile is None else str(profile.get("profile_id")))
    return profile, resolved
