"""Observe configured AI_Bandits installation and runtime data while keeping placement unavailable."""

from __future__ import annotations

import json

from ..domain.mission_map_providers import AI_BANDITS_WORKSHOP_ID, NO_VERIFIED_HEIGHT_REASON, ProviderCapability
from ..domain.mission_map_values import refuse_json_constant, unique_json_keys
from ..domain.profiles import ProfileRecord
from .configuration_common import UTF8_BOM
from .mission_map_providers_paths import ProviderReadError, ProviderReader


def read_bandits_capability(reader: ProviderReader, profile: ProfileRecord) -> ProviderCapability:
    """Combine the configured Workshop identity, installed folder and runtime provider file."""
    mods = [mod for mod in profile.values.mods if mod.source.kind == "workshop"
            and mod.source.workshop_id == AI_BANDITS_WORKSHOP_ID]
    evidence = {"workshop_id": AI_BANDITS_WORKSHOP_ID, "configured": bool(mods), "mod_present": False,
                "configuration_present": False, "pbo_sha256": None, "build_verified": False,
                "build_reason": "provider_build_unverified", "diagnostics": []}
    if not mods:
        evidence["diagnostics"].append("provider_not_configured")
        return ProviderCapability(False, False, NO_VERIFIED_HEIGHT_REASON, evidence=evidence)
    # The profile source identity selects the installed folder; folder names alone are not provider proof
    try:
        mod = reader.folder(mods[0].directory)
        evidence["mod_present"] = True
    except ProviderReadError as error:
        evidence["diagnostics"].append(str(error))
        return ProviderCapability(False, False, NO_VERIFIED_HEIGHT_REASON, evidence=evidence)
    # The inspected build digest is observed, not verified against an unpinned expected value
    try:
        evidence["pbo_sha256"] = mod.digest("addons/AI_Bandits.pbo")
    except ProviderReadError as error:
        evidence["diagnostics"].append(str(error))
    # Runtime files must belong to this profile and remain contained within the DayZ root
    runtime = profile.values.runtime_profile
    if runtime is None:
        evidence["diagnostics"].append("runtime_profile_unresolved")
        return ProviderCapability(False, False, NO_VERIFIED_HEIGHT_REASON, evidence=evidence)
    try:
        runtime_reader = reader.folder(runtime)
        content = runtime_reader.bytes("AI_Bandits/DynamicAIB.json")
        raw = json.loads(content.removeprefix(UTF8_BOM).decode("utf-8"),
                         parse_constant=refuse_json_constant, object_pairs_hook=unique_json_keys)
        if not isinstance(raw, dict) or not isinstance(raw.get("GroupLocations"), list):
            raise ValueError("the provider file does not hold GroupLocations")
        evidence["configuration_present"] = True
    except ProviderReadError as error:
        evidence["diagnostics"].append(str(error))
    except (UnicodeDecodeError, ValueError, RecursionError):
        evidence["diagnostics"].append("provider_configuration_invalid")
    detected = bool(evidence["mod_present"] and evidence["configuration_present"])
    return ProviderCapability(detected, False, NO_VERIFIED_HEIGHT_REASON, evidence=evidence)
