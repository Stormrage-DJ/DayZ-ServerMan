"""Deterministic transformations of the two medical loot features.

Item spawns re-write `db/types.xml` as a whole. Loot zones patch only the medical children of
`mapgroupproto.xml`; the earlier whole-file form of the zone transform stays so its files are recognized.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..domain.medical_zone_rule import MEDICAL_CATEGORIES, MEDICAL_MARKERS
from .configuration_common import ConfigurationFileError
from .medical_zone_fragments import patch_zones

# Target (nominal, minimum) loot counts for each boosted medical item
MEDICAL_COUNTS = {
    "BandageDressing": (120, 60), "TetracyclineAntibiotics": (60, 30),
    "VitaminBottle": (100, 50), "CharcoalTablets": (60, 30),
    "PainkillerTablets": (50, 25), "DisinfectantSpray": (70, 35),
    "IodineTincture": (50, 25), "PurificationTablets": (100, 50),
}
# Start of every file that the earlier whole-file zone form wrote, so other files skip that comparison
_WHOLE_FILE_START = ET.tostring(ET.Element("x"), encoding="utf-8", xml_declaration=True).split(b"<x")[0]


def transform_medical_feature(feature: str, content: bytes) -> bytes:
    """Apply one supported medical loot feature to an XML document that is in its original state."""
    # Loot zones change only the medical children, with every other byte kept
    if feature == "medical_loot_zones":
        return patch_zones(content, content, True)
    if feature != "medical_item_spawns":
        raise ConfigurationFileError("medical feature is not supported")
    return _whole_file(content, _boost_medical_items)


def project_zones(current: bytes, original: bytes, enabled: bool) -> bytes:
    """Return the loot-zone bytes that a toggle to `enabled` writes, from the current file and its original.

    A file that earlier versions enabled as a whole returns to its exact original bytes. Otherwise only the
    medical children change, and an off write keeps the line endings of the current file.
    """
    if not enabled and whole_file_off(current, original):
        return original
    return patch_zones(current, original, enabled)


def whole_file_off(current: bytes, original: bytes) -> bool:
    """Return whether an off toggle puts back the original bytes, because the file is the earlier whole-file form.

    The toggle and the classification share this step (6.5). The original is parsed as that form needs, and
    ConfigurationFileError is raised when it cannot be. Only a file that starts like that form is compared
    with it, so other files skip the rewrite. The result is the same as a comparison of every file.
    """
    if not current.startswith(_WHOLE_FILE_START):
        _parse(original)
        return False
    return current == legacy_zone_transform(original)


def legacy_zone_transform(content: bytes) -> bytes:
    """Return the whole-file loot-zone form that earlier versions wrote, to recognize their enabled files."""
    return _whole_file(content, _restrict_medical_groups)


def _whole_file(content: bytes, change) -> bytes:
    """Parse a document strictly, change its tree, and write it again in the four-space server layout."""
    root = _parse(content)
    change(root)
    ET.indent(root, space="    ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _parse(content: bytes) -> ET.Element:
    """Parse a document strictly, so a malformed target is never partially rewritten."""
    try:
        return ET.fromstring(content)
    except ET.ParseError as error:
        raise ConfigurationFileError("medical feature target is malformed XML") from error


def _restrict_medical_groups(root: ET.Element) -> None:
    """Trim medical loot groups to the Medic usage and medical categories, as earlier versions did."""
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
