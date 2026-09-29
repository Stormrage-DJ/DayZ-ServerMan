"""Deterministic transformations for the two legacy medical loot features."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .configuration_common import ConfigurationFileError

# Name fragments that mark a loot group as medical for restriction
MEDICAL_MARKERS = ("medic", "medical", "healthcare", "hospital", "laboratory")
# Container categories that stay allowed in restricted medical groups
MEDICAL_CATEGORIES = {"tools", "containers"}
# Target (nominal, minimum) loot counts for each boosted medical item
MEDICAL_COUNTS = {
    "BandageDressing": (120, 60), "TetracyclineAntibiotics": (60, 30),
    "VitaminBottle": (100, 50), "CharcoalTablets": (60, 30),
    "PainkillerTablets": (50, 25), "DisinfectantSpray": (70, 35),
    "IodineTincture": (50, 25), "PurificationTablets": (100, 50),
}


def transform_medical_feature(feature: str, content: bytes) -> bytes:
    """Apply one supported medical loot feature to an XML document."""
    # Parse strictly so a malformed target is never partially rewritten
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ConfigurationFileError("medical feature target is malformed XML") from error
    # Dispatch on the selected feature and refuse unknown names
    if feature == "medical_loot_zones":
        _restrict_medical_groups(root)
    elif feature == "medical_item_spawns":
        _boost_medical_items(root)
    else:
        raise ConfigurationFileError("medical feature is not supported")
    # Re-serialize deterministically with the four-space server layout
    ET.indent(root, space="    ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _restrict_medical_groups(root: ET.Element) -> None:
    """Trim medical loot groups to the Medic usage and medical categories."""
    # Visit every group and keep only Medic-marked candidates
    for group in root.findall(".//group"):
        name = group.get("name", "").lower()
        usages = {node.get("name") for node in group.findall("usage")}
        # A group qualifies when Medic is listed alone or its name reads medical
        if "Medic" not in usages or not (usages == {"Medic"} or any(mark in name for mark in MEDICAL_MARKERS)):
            continue
        # Drop usages other than Medic so only medics see the loot
        for usage in list(group.findall("usage")):
            if usage.get("name") != "Medic":
                group.remove(usage)
        # Keep only medical categories, restoring the pair when none remain
        for container in group.findall("container"):
            for category in list(container.findall("category")):
                if category.get("name") not in MEDICAL_CATEGORIES:
                    container.remove(category)
            if not container.findall("category"):
                ET.SubElement(container, "category", {"name": "tools"})
                ET.SubElement(container, "category", {"name": "containers"})


def _boost_medical_items(root: ET.Element) -> None:
    """Raise medical spawn counts to the configured nominal and minimum values."""
    by_name = {node.get("name"): node for node in root.findall("type")}
    # Apply the configured count pair to each present medical item
    for name, (nominal, minimum) in MEDICAL_COUNTS.items():
        item = by_name.get(name)
        if item is None:
            continue
        nominal_node, minimum_node = item.find("nominal"), item.find("min")
        # Both count elements must exist before the item is rewritten
        if nominal_node is None or minimum_node is None:
            raise ConfigurationFileError(f"medical item {name} has no nominal or minimum value")
        nominal_node.text, minimum_node.text = str(nominal), str(minimum)
        # Tag the item as Medic loot when it carries no usage yet
        if not any(node.get("name") == "Medic" for node in item.findall("usage")):
            ET.SubElement(item, "usage", {"name": "Medic"})
