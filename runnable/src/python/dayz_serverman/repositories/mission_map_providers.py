"""Discover native infected support and the unavailable AI_Bandits, wolf and bear providers without mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..domain.mission_map_providers import (
    INFECTED_TERRITORY_FILE, UNVERIFIED_TERRITORY_PROVIDERS, ProviderCapability, infected_registered,
    offered_infected_events, unverified_territory_capability,
)
from ..domain.profiles import ProfileRecord
from .mission_map_providers_bandits import read_bandits_capability
from .mission_map_providers_paths import ProviderReadError, ProviderReader


def read_infected_capability(reader: ProviderReader) -> ProviderCapability:
    """Require native territory registration and offer only zone names declared as mission events."""
    evidence: dict[str, Any] = {"registered": False}
    try:
        # Registration is mandatory even when an unused territory file exists
        environment = reader.xml("cfgenvironment.xml")
        evidence["registered"] = infected_registered(environment)
        if not evidence["registered"]:
            return ProviderCapability(False, False, "territory_not_registered", evidence=evidence)
        territories = reader.xml(INFECTED_TERRITORY_FILE)
        events = reader.xml("db/events.xml")
        # Population and radius are native semantics, never fixed live counts at a marker
        names = offered_infected_events(territories, events)
        return ProviderCapability(True, bool(names), None if names else "no_matching_events", names, evidence)
    except ProviderReadError as error:
        return ProviderCapability(False, False, str(error), evidence=evidence)
    except ValueError:
        return ProviderCapability(False, False, "provider_configuration_invalid", evidence=evidence)


def read_encounter_providers(root: Path, mission: Path, profile: ProfileRecord, *,
                            containment: Callable[..., Path]) -> dict[str, Any]:
    """Return additive provider metadata; each failed discovery leaves other editor capabilities usable."""
    providers = {"native_infected": read_infected_capability(ProviderReader(mission, containment)).to_dict(),
                 "ai_bandits": read_bandits_capability(ProviderReader(root, containment), profile).to_dict()}
    # Leaf 7.9: wolves and bears are always unavailable; no wolf or bear territory file is read or mapped
    for provider in UNVERIFIED_TERRITORY_PROVIDERS:
        providers[provider] = unverified_territory_capability().to_dict()
    return providers
