"""Medical loot-zone rule: which loot groups qualify, and their usage and category children when on (A5).

The ordinary patcher and the generated groups of the map editor use the same rule. A generated group takes
its children from its source group, because a generated name carries no medical marker.
"""

from __future__ import annotations

from dataclasses import dataclass


# Lowercase name parts that mark a loot group as medical
MEDICAL_MARKERS = ("medic", "medical", "healthcare", "hospital", "laboratory")
# The usage that a qualified group keeps when the feature is on
MEDIC_USAGE = "Medic"
# Container categories that a qualified group keeps, in the order they are added when none remain
MEDICAL_CATEGORIES = ("tools", "containers")
# Element bytes of the categories that the rule adds to a container that keeps none
ADDED_CATEGORIES = tuple(f'<category name="{name}" />'.encode("ascii") for name in MEDICAL_CATEGORIES)
# Prefix of the groups that the map editor generates (D8); the ordinary patcher never changes them
RESERVED_PREFIX = "ServerManMap_"


@dataclass(frozen=True)
class Fragment:
    """One usage or category child: its name attribute, all its attributes and its exact element bytes."""

    name: str | None
    attributes: tuple[tuple[str, str], ...]
    data: bytes


@dataclass(frozen=True)
class Container:
    """The category children of one container, in document order."""

    name: str | None
    categories: tuple[Fragment, ...]


@dataclass(frozen=True)
class MedicalChildren:
    """The medical children of one loot group: its usages and the categories of each container."""

    group: str
    usages: tuple[Fragment, ...]
    containers: tuple[Container, ...]

    def shape(self) -> tuple[object, ...]:
        """Return the parsed structure without bytes, so two layouts of the same children compare equal."""
        return (tuple(usage.attributes for usage in self.usages),
                tuple((container.name, tuple(item.attributes for item in container.categories))
                      for container in self.containers))


def is_reserved(group: str) -> bool:
    """Return whether a group name has the editor's reserved prefix, without regard to case."""
    return group.casefold().startswith(RESERVED_PREFIX.casefold())


def qualifies(children: MedicalChildren) -> bool:
    """Return whether a group is medical: Medic usage, and Medic alone or a medical marker in its name."""
    usages = {usage.name for usage in children.usages}
    if MEDIC_USAGE not in usages:
        return False
    return usages == {MEDIC_USAGE} or any(marker in children.group.lower() for marker in MEDICAL_MARKERS)


def medical_children(source: MedicalChildren, enabled: bool) -> MedicalChildren:
    """Return the children that a group derived from `source` has, for the off children of `source`.

    Off, or for a source that does not qualify, the children stay the off children. On, only the Medic
    usages and the medical categories stay; a container that keeps no category gets tools, then containers.
    """
    if not enabled or not qualifies(source):
        return source
    # Keep only the Medic usages, so only medics see the loot
    usages = tuple(usage for usage in source.usages if usage.name == MEDIC_USAGE)
    # Keep only the medical categories, and add the pair to a container that keeps none
    containers = []
    for container in source.containers:
        kept = tuple(item for item in container.categories if item.name in MEDICAL_CATEGORIES)
        containers.append(Container(container.name, kept or _added_categories()))
    return MedicalChildren(source.group, usages, tuple(containers))


def _added_categories() -> tuple[Fragment, ...]:
    """Return the tools and containers categories that the rule adds, in that order."""
    return tuple(Fragment(name, (("name", name),), data) for name, data in zip(MEDICAL_CATEGORIES, ADDED_CATEGORIES))
