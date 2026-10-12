"""Define read-only encounter provider capabilities and native event-name matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element

from .mission_map_values import EVENT_NAME


# Workshop identity recorded by the accepted provider assessment and height-source evidence
AI_BANDITS_WORKSHOP_ID = "3628006769"
# Exact customer-accepted choice-A limitation; presence never enables placement in this plan
NO_VERIFIED_HEIGHT_REASON = "Bandit placement is not available on this terrain: no verified ground height source."
# Mission registration path; only this native infected territory file is supported in leaf 7.2
INFECTED_TERRITORY_FILE = "env/zombie_territories.xml"


@dataclass(frozen=True)
class ProviderCapability:
    """Describe observed presence separately from placement availability and its reason."""

    detected: bool
    available: bool
    reason: str | None
    event_names: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return additive browser metadata without filesystem paths or inferred population counts."""
        return {"detected": self.detected, "available": self.available, "reason": self.reason,
                "event_names": list(self.event_names), "evidence": dict(self.evidence)}


def infected_registered(environment: Element) -> bool:
    """Recognize a direct territories/file registration of the supported infected file."""
    if environment.tag != "env":
        return False
    # Windows path spelling may use either separator and case, but never traversal or extra segments
    for element in environment.findall("territories/file"):
        path = element.get("path", "").replace("\\", "/")
        if path.lower() == INFECTED_TERRITORY_FILE:
            return True
    return False


def offered_infected_events(territories: Element, events: Element) -> tuple[str, ...]:
    """Offer only D1-valid names shared by territory zones and actual mission event declarations."""
    if territories.tag != "territory-type" or events.tag != "events":
        raise ValueError("native encounter XML has an unsupported root element")
    # Exact names matter to native CE; a similar name or a conventional prefix proves no event exists
    zones = {zone.get("name", "") for zone in territories.findall("territory/zone")}
    declared = {event.get("name", "") for event in events.findall("event")}
    return tuple(sorted(name for name in zones & declared if EVENT_NAME.fullmatch(name) is not None))
