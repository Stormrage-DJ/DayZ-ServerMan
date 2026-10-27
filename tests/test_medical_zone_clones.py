"""Regeneration of the medical children of generated loot groups for the 8.13 zone toggle (clone parity, R17)."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from dayz_serverman.application.mission_map_apply_records import realization
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.medical_features import project_zones
from dayz_serverman.repositories.medical_zone_clones import (
    GROUP_DIFFERS, NAMES_DIFFER, SOURCE_DIFFERS, CloneMismatch, regenerate_clones,
)
from dayz_serverman.repositories.medical_zone_fragments import patch_zones
from tests.mission_map_mapgroup_support import BASELINE, PROTOTYPE, build, contents, plan, zone

PROTO = "mapgroupproto.xml"


def applied(medical_on: bool, baseline: dict[str, bytes | None] | None = None,
            medical: bytes = PROTOTYPE) -> tuple[bytes, list[dict[str, Any]]]:
    """Return the mapgroupproto.xml that Apply writes with the medical state, and its realization."""
    files = dict(BASELINE if baseline is None else baseline)
    if medical_on:
        files[PROTO] = patch_zones(files[PROTO], medical, True)
    result = build(plan(25, zone("rich")), files, medical_baseline=medical)
    assert not result.refusals, result.refusals
    return contents(result)[PROTO], [realization(clone) for clone in result.clones]


def inside_house(data: bytes, element: bytes) -> bytes:
    """Return the file with an element as the first child of the Land_House group."""
    start = data.index(b'<group name="Land_House"')
    end = data.index(b">", start) + 1
    return data[:end] + element + data[end:]


class ParityTests(unittest.TestCase):
    """The toggle gives the bytes that Apply writes with the new medical state."""

    def test_on_and_off_equal_apply(self) -> None:
        """Off to on and on to off over the 6.8 candidates, with CRLF and with LF files."""
        for newline in (b"\r\n", b"\n"):
            with self.subTest(newline=newline):
                files = {path: data.replace(b"\r\n", newline) for path, data in BASELINE.items()}
                medical = files[PROTO]
                off, groups = applied(False, files, medical)
                on, groups_on = applied(True, files, medical)
                self.assertEqual(groups, groups_on)
                self.assertEqual(len(groups), 2)
                self.assertEqual(regenerate_clones(off, patch_zones(off, medical, True), medical, groups, False, True), on)
                self.assertEqual(regenerate_clones(on, project_zones(on, medical, False), medical, groups, True, False), off)

    def test_same_state_changes_nothing(self) -> None:
        """A regeneration in the current state returns the patched bytes unchanged."""
        off, groups = applied(False)
        self.assertEqual(regenerate_clones(off, off, PROTOTYPE, groups, False, False), off)

    def test_no_generated_group(self) -> None:
        """An empty realization over a file without generated groups changes only what the patcher changed."""
        patched = patch_zones(PROTOTYPE, PROTOTYPE, True)
        self.assertEqual(regenerate_clones(PROTOTYPE, patched, PROTOTYPE, [], False, True), patched)

    def test_only_generated_groups_change(self) -> None:
        """Ceilings, names and every byte outside the generated medical children stay."""
        off, groups = applied(False)
        patched = patch_zones(off, PROTOTYPE, True)
        result = regenerate_clones(off, patched, PROTOTYPE, groups, False, True)
        start = result.index(b'<group name="ServerManMap_G_')
        self.assertEqual(result[:start], patched[:start])
        self.assertEqual(result.count(b'lootmax="'), off.count(b'lootmax="'))
        for item in groups:
            self.assertIn(f'<group name="{item["name"]}" lootmax="{item["group_ceiling"]}"'.encode(), result)


class MismatchTests(unittest.TestCase):
    """A file whose generated groups differ from the realization refuses (Architect 2026-10-15 01:18:47)."""

    def setUp(self) -> None:
        """Build the applied file with medical off and its realization."""
        self.off, self.groups = applied(False)
        self.patched = patch_zones(self.off, PROTOTYPE, True)

    def refuse(self, current: bytes, groups: list[dict[str, Any]], reason: str, patched: bytes | None = None) -> None:
        """Assert one refusal reason."""
        with self.assertRaises(CloneMismatch) as caught:
            regenerate_clones(current, patch_zones(current, PROTOTYPE, True) if patched is None else patched,
                              PROTOTYPE, groups, False, True)
        self.assertEqual(caught.exception.reason, reason)

    def test_names_differ(self) -> None:
        """A missing, an extra, a nested or a repeated generated group refuses."""
        self.refuse(self.off, self.groups[:1], NAMES_DIFFER)
        extra = copy.deepcopy(self.groups) + [dict(self.groups[0], name="ServerManMap_G_000000000000")]
        self.refuse(self.off, extra, NAMES_DIFFER)
        name = self.groups[0]["name"].encode()
        nested = inside_house(self.off, b'<group name="ServerManMap_X_1" />')
        self.assertNotEqual(nested, self.off)
        self.refuse(nested, self.groups, NAMES_DIFFER)
        start = self.off.index(b'<group name="' + name)
        end = self.off.index(b"</group>", start) + len(b"</group>")
        repeated = self.off[:end] + b"\r\n    " + self.off[start:end] + self.off[end:]
        self.refuse(repeated, self.groups, NAMES_DIFFER)

    def test_patched_names_must_stay(self) -> None:
        """Patched bytes that lost a generated group refuse; the patcher never removes one."""
        self.refuse(self.off, self.groups, NAMES_DIFFER, patched=PROTOTYPE)

    def test_group_bytes_differ(self) -> None:
        """An edited generated ceiling, or a realization with another ceiling, refuses."""
        name = self.groups[0]["name"]
        edited = self.off.replace(f'<group name="{name}" lootmax="{self.groups[0]["group_ceiling"]}"'.encode(),
                                  f'<group name="{name}" lootmax="99"'.encode())
        self.assertNotEqual(edited, self.off)
        self.refuse(edited, self.groups, GROUP_DIFFERS)
        groups = copy.deepcopy(self.groups)
        groups[0]["container_ceilings"] = ["1"] * len(groups[0]["container_ceilings"])
        self.refuse(self.off, groups, GROUP_DIFFERS)

    def test_generated_children_in_another_state_refuse(self) -> None:
        """A generated group that the earlier patcher left in the other state differs from the realization."""
        on, _groups = applied(True)
        mixed = on[:on.index(b'<group name="ServerManMap_G_')] + self.off[self.off.index(b'<group name="ServerManMap_G_'):]
        with self.assertRaises(CloneMismatch) as caught:
            regenerate_clones(mixed, project_zones(mixed, PROTOTYPE, False), PROTOTYPE, self.groups, True, False)
        self.assertEqual(caught.exception.reason, GROUP_DIFFERS)

    def test_source_differs(self) -> None:
        """Another source file, a missing source, another spelling and other containers refuse."""
        for change in ({"source": ["mapgrouppos.xml", "Land_Shed"]}, {"source": [PROTO, "Land_Missing"]},
                       {"source": [PROTO, "land_shed"]}, {"containers": ["a", "b"]}):
            with self.subTest(change=change):
                groups = copy.deepcopy(self.groups)
                index = next(i for i, item in enumerate(groups) if item["source"][1] == "Land_Shed")
                groups[index].update(change)
                self.refuse(self.off, groups, SOURCE_DIFFERS)

    def test_ambiguous_source_refuses(self) -> None:
        """Two ordinary groups whose names match without case leave the source unresolved."""
        start = self.off.index(b'<group name="Land_House"')
        twin = self.off[:start] + b'<group name="LAND_SHED"><usage name="Farm" /></group>\r\n    ' + self.off[start:]
        self.refuse(twin, self.groups, SOURCE_DIFFERS)

    def test_mixed_line_endings_refuse_with_the_layout_reason(self) -> None:
        """A file with two line-ending forms cannot get canonical generated groups."""
        mixed = self.off.replace(b"<prototype>\r\n", b"<prototype>\n", 1)
        with self.assertRaises(ConfigurationFileError) as caught:
            regenerate_clones(mixed, mixed, PROTOTYPE, self.groups, False, False)
        self.assertIn("more than one line-ending form", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
