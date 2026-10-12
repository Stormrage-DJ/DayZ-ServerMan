"""Retain exact resource custody and ordered faults without implicit retries."""
from __future__ import annotations

from typing import Callable, Any
from .process_evidence import ProcessIdentityUnavailable


def diagnostic(error: BaseException) -> str:
    """Keep formatting failure from replacing the original exception object."""
    try:
        return str(error)
    except BaseException:
        return "exception detail unavailable; original retained"


class CustodyError(OSError):
    """Carry the original error and surviving nonpersisted resource custodian."""
    def __init__(self, original: BaseException, custody: Custody) -> None:
        """Preserve legacy OSError catches without discarding partial ownership."""
        super().__init__(diagnostic(original))
        self.original = original
        self.custody = custody
        self.error_code = getattr(original, "error_code", getattr(original, "winerror", None))
        self.secondary = custody.secondary


class IdentityCustodyError(CustodyError, ProcessIdentityUnavailable):
    """Preserve the more specific legacy identity-unavailable catch contract."""


class Reference:
    """Own one acquired reference; failed release leaves an unknown state."""
    def __init__(self, kind: str, origin: str, close: Callable[[Any], None]) -> None:
        """Reserve bookkeeping before the acquisition can return a resource."""
        self.kind, self.origin, self.close_action = kind, origin, close
        self.handle = None
        self.last_error = None
        self.state = "reserved"

    def acquire(self, action: Callable[[], Any]) -> Any:
        """Store the exact returned reference before any later work."""
        self.handle = action()
        self.state = "owned"
        return self.handle

    def value(self) -> Any:
        """Refuse reuse of a released, transferred or ambiguous reference."""
        if self.state != "owned":
            raise OSError("resource custody unavailable: " + self.kind + "/" + self.state)
        return self.handle

    def close(self) -> None:
        """Attempt this particular release once; never retry an ambiguous value."""
        if self.state in ("reserved", "released", "transferred"):
            return
        value = self.value()
        # A failed close proves neither release nor continued live validity.
        self.state = "release-unknown"
        self.close_action(value)
        self.state = "released"

    def transfer(self) -> None:
        """Record successful ownership transfer to another exact custodian."""
        self.value()
        self.state = "transferred"


class Custody:
    """Survive partial acquisition and preserve original plus secondary faults."""
    def __init__(self) -> None:
        """Create the owner before identity, handle or thread acquisition."""
        self.references: list[Reference] = []
        self.primary: BaseException | None = None
        self.secondary: list[tuple[str, BaseException]] = []
        self.steps: dict[str, str] = {}
        self.anchors: dict[str, Any] = {}

    def reserve(self, kind: str, origin: str, close: Callable[[Any], None]) -> Reference:
        """Register an empty slot before its native acquisition is attempted."""
        reference = Reference(kind, origin, close)
        self.references.append(reference)
        return reference

    def fault(self, error: BaseException) -> None:
        """Latch the first exact operation failure without replacing it."""
        original = error.original if isinstance(error, CustodyError) else error
        # Keep the first operation failure ahead of later independent refusals.
        if self.primary is None:
            self.primary = original
        elif original is not self.primary:
            self.secondary.append(("operation", original))
        if isinstance(error, CustodyError) and error.custody is not self:
            # Preserve another exact custodian rather than merge its authority.
            self.anchors["nested-" + str(len(self.anchors))] = error.custody

    def attempt(self, name: str, action: Callable[[], Any]) -> Any:
        """Attempt one independent obligation and retain its exact refusal."""
        # A failed independent release must not skip the next obligation.
        try:
            value = action()
        except BaseException as error:
            self.secondary.append((name, error))
            self.steps[name] = "failed"
            return None
        self.steps[name] = "passed"
        return value

    def release(self, reference: Reference) -> None:
        """Retain a checked release fault without skipping later references."""
        self.attempt("release:" + reference.kind + ":" + reference.origin, reference.close)

    def raise_failure(self) -> None:
        """Return a catch-compatible carrier with the surviving exact custodian."""
        original = self.primary
        if original is None and self.secondary:
            original = self.secondary[0][1]
        if original is not None:
            kind = IdentityCustodyError if isinstance(original, ProcessIdentityUnavailable) else CustodyError
            raise kind(original, self) from original

    def report(self) -> dict[str, object]:
        """Expose diagnostics without persisting live handle values as authority."""
        return {"primary": diagnostic(self.primary) if self.primary is not None else None,
                "secondary": [{"step": name, "error": diagnostic(error)} for name, error in self.secondary],
                "references": [{"kind": ref.kind, "origin": ref.origin, "state": ref.state}
                               for ref in self.references], "steps": dict(self.steps),
                "nested": {name: value.report() for name, value in self.anchors.items()
                           if isinstance(value, Custody)}}
