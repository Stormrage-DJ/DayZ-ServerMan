"""Tests for the element-level medical loot-zone patcher, its per-group state and the shared clone rule."""
from __future__ import annotations

import unittest

from dayz_serverman.domain.medical_zone_rule import MedicalChildren, medical_children
from dayz_serverman.repositories.atomic_file import ContentChangedError
from dayz_serverman.repositories.configuration_common import ConfigurationFileError
from dayz_serverman.repositories.medical_features import legacy_zone_transform, transform_medical_feature
from dayz_serverman.repositories.medical_zone_fragments import holds_reserved_group, patch_zones, zones_enabled
from dayz_serverman.repositories.medical_zone_scan import children_of, scan_groups

# Unrelated parts that a patch must keep byte for byte: an editor group and an ordinary group
EDITOR_GROUP = (b'    <group name="ServerManMap_G_0123456789ab" lootmax="60">\r\n'
                b'        <usage name="Medic" />\r\n        <usage name="Town" />\r\n    </group>\r\n')
HOUSE_GROUP = b'    <group name="Land_House" lootmax="5"><usage name="Town" /></group>\r\n'
# A CRLF mission prototype with a comment, ceilings, an empty-element container and a points-only container
SOURCE = (b'<?xml version="1.0" encoding="utf-8"?>\r\n<prototype>\r\n    <!-- keep me -->\r\n'
          b'    <group name="Land_City_Hospital" lootmax="40">\r\n'
          b'        <usage name="Medic" />\r\n        <usage name="Town" />\r\n'
          b'        <container name="lootFloor" lootmax="20">\r\n'
          b'            <category name="weapons" />\r\n            <category name="tools" />\r\n'
          b'            <tag name="floor" />\r\n            <point pos="1 2 3" range="0.4" height="1" />\r\n'
          b'        </container>\r\n'
          b'        <container name="lootShelves">\r\n            <category name="clothes" />\r\n'
          b'            <point pos="1 2 3" />\r\n        </container>\r\n'
          b'        <container name="empty" lootmax="1" />\r\n'
          b'        <container name="pointsOnly">\r\n            <point pos="4 5 6" />\r\n        </container>\r\n'
          b'    </group>\r\n' + EDITOR_GROUP + HOUSE_GROUP + b'</prototype>\r\n')


def _children(data: bytes, name: str, position: int = 0) -> MedicalChildren:
    """Return the medical children of the named group at one position."""
    return children_of(data, [node for node in scan_groups(data) if node.attributes.get("name") == name][position])


class MedicalZonePatchTests(unittest.TestCase):
    """The patcher changes only medical children and restores the exact original bytes."""

    def test_enable_changes_only_medical_children_and_disable_restores_exact_bytes(self) -> None:
        """Ceilings, comments, layout, editor groups and other groups stay; off returns the original bytes."""
        enabled = patch_zones(SOURCE, SOURCE, True)
        for kept in (b"<!-- keep me -->", b'lootmax="40"', b'lootmax="20"', EDITOR_GROUP, HOUSE_GROUP,
                     b'<?xml version="1.0" encoding="utf-8"?>\r\n', b'<point pos="4 5 6" />'):
            self.assertIn(kept, enabled)
        self.assertNotIn(b'usage name="Town" />\r\n        <container', enabled)
        self.assertNotIn(b"weapons", enabled)
        self.assertNotIn(b"clothes", enabled)
        self.assertNotIn(b"\n\n", enabled.replace(b"\r", b""))
        self.assertTrue(zones_enabled(enabled, SOURCE))
        self.assertFalse(zones_enabled(SOURCE, SOURCE))
        self.assertEqual(patch_zones(enabled, SOURCE, False), SOURCE)

    def test_containers_without_categories_get_tools_then_containers(self) -> None:
        """Every container that keeps no category gets the pair, also an empty-element container."""
        children = _children(patch_zones(SOURCE, SOURCE, True), "Land_City_Hospital")
        names = [[item.name for item in container.categories] for container in children.containers]
        self.assertEqual(names, [["tools"], ["tools", "containers"], ["tools", "containers"], ["tools", "containers"]])

    def test_file_enabled_by_the_earlier_whole_file_code_is_on(self) -> None:
        """The re-serialized form that earlier versions wrote classifies as on and can be turned off."""
        legacy = legacy_zone_transform(SOURCE)
        self.assertTrue(zones_enabled(legacy, SOURCE))
        disabled = patch_zones(legacy, SOURCE, False)
        self.assertFalse(zones_enabled(disabled, SOURCE))
        self.assertEqual(_children(disabled, "Land_City_Hospital").shape(), _children(SOURCE, "Land_City_Hospital").shape())

    def test_groups_with_the_same_name_match_by_position(self) -> None:
        """Two groups with one name each keep their own children, and a different count refuses."""
        source = (b'<prototype><group name="Hospital"><usage name="Medic"/><usage name="Town"/>'
                  b'<container name="a"><category name="food"/></container></group>'
                  b'<group name="Hospital"><usage name="Medic"/><usage name="Village"/>'
                  b'<container name="b"><category name="tools"/></container></group></prototype>')
        enabled = patch_zones(source, source, True)
        self.assertIn(b'<container name="b"><category name="tools"/></container>', enabled)
        self.assertEqual(patch_zones(enabled, source, False), source)
        extra = enabled.replace(b"</prototype>", b'<group name="Hospital"><usage name="Medic"/></group></prototype>')
        with self.assertRaises(ContentChangedError):
            zones_enabled(extra, source)

    def test_mixed_or_foreign_children_differ_from_both(self) -> None:
        """One group on and one off, or children of neither form, keep the existing refusal."""
        source = (b'<prototype><group name="Hospital_A"><usage name="Medic"/><usage name="Town"/></group>'
                  b'<group name="Hospital_B"><usage name="Medic"/><usage name="Farm"/></group></prototype>')
        mixed = source.replace(b'<usage name="Town"/>', b"")
        foreign = source.replace(b'name="Farm"', b'name="Military"')
        for current in (mixed, foreign):
            with self.subTest(current=current), self.assertRaisesRegex(ContentChangedError, "differs from both"):
                zones_enabled(current, source)

    def test_changed_container_structure_refuses(self) -> None:
        """A container that was renamed or removed since the original refuses for the mission."""
        changed = SOURCE.replace(b'name="lootShelves"', b'name="lootShelf"')
        with self.assertRaisesRegex(ContentChangedError, "differs from both"):
            zones_enabled(changed, SOURCE)

    def test_nothing_to_change_reads_on_and_requested_state_refuses(self) -> None:
        """A file without a changing medical group reads as on and stays as it is; a repeated state refuses."""
        plain = b'<prototype><group name="Land_House"><usage name="Town"/></group></prototype>'
        self.assertTrue(zones_enabled(plain, plain))
        for enabled in (True, False):
            self.assertEqual(patch_zones(plain, plain, enabled), plain)
        with self.assertRaisesRegex(ValueError, "already has the requested state"):
            patch_zones(SOURCE, SOURCE, False)

    def test_split_or_commented_runs_refuse_before_any_change(self) -> None:
        """An element, a comment or text inside a usage or category run refuses with its named cause, both ways."""
        lead = "mapgroupproto.xml has a layout that this setting cannot change safely: group Land_City_Hospital has "
        cases = (
            (b'<usage name="Town" />', b'<value name="Tier1" /><usage name="Town" />', "another element"),
            (b'<category name="tools" />', b'<tag name="x" /><category name="tools" />', "another element"),
            (b'<usage name="Town" />', b'<!-- between -->\r\n        <usage name="Town" />', "a comment or text"),
            (b'<category name="tools" />', b'<!-- in cats -->\r\n            <category name="tools" />',
             "a comment or text"),
        )
        for old, new, cause in cases:
            changed = SOURCE.replace(old, new, 1)
            message = f"{lead}{cause} between usage and category. No file was changed."
            # Enable meets the split run in the current file, Disable meets it in the original
            for current, enabled in ((changed, True), (patch_zones(SOURCE, SOURCE, True), False)):
                with self.subTest(cause=cause, enabled=enabled), self.assertRaises(ConfigurationFileError) as caught:
                    patch_zones(current, changed, enabled)
                self.assertEqual(str(caught.exception), message)

    def test_a_medical_group_in_only_one_file_refuses_by_name(self) -> None:
        """A qualified group that the original lacks, or one that the file lacks, refuses and writes nothing."""
        lead = "mapgroupproto.xml differs from its stored original: group "
        added = b'    <group name="Clinic_New">\r\n        <usage name="Medic" />\r\n    </group>\r\n'
        missing = SOURCE[:SOURCE.index(b'    <group name="Land_City_Hospital"')] + SOURCE[SOURCE.index(EDITOR_GROUP):]
        cases = ((SOURCE.replace(HOUSE_GROUP, HOUSE_GROUP + added), SOURCE, "Clinic_New is not in the stored original"),
                 (missing, SOURCE, "Land_City_Hospital is missing from the file"))
        for current, original, cause in cases:
            for call in (lambda: zones_enabled(current, original), lambda: patch_zones(current, original, True),
                         lambda: patch_zones(current, original, False)):
                with self.subTest(cause=cause), self.assertRaises(ContentChangedError) as caught:
                    call()
                self.assertEqual(str(caught.exception), f"{lead}{cause}. No file was changed.")

    def test_off_keeps_the_line_endings_of_the_current_file(self) -> None:
        """An LF original gives CRLF runs in a CRLF file; a file with mixed line endings refuses."""
        enabled = patch_zones(SOURCE, SOURCE, True)
        lf_original = SOURCE.replace(b"\r\n", b"\n")
        self.assertEqual(patch_zones(enabled, lf_original, False), SOURCE)
        self.assertEqual(patch_zones(enabled.replace(b"\r\n", b"\n"), SOURCE, False), lf_original)
        mixed = enabled.replace(b"<!-- keep me -->\r\n", b"<!-- keep me -->\n")
        with self.assertRaises(ConfigurationFileError) as caught:
            patch_zones(mixed, SOURCE, False)
        self.assertEqual(str(caught.exception), "mapgroupproto.xml has a layout that this setting cannot change "
                         "safely: the file has more than one line-ending form. No file was changed.")

    def test_comments_outside_the_runs_stay_through_on_and_off(self) -> None:
        """Comments before a run and after it stay in place, and on then off gives the exact bytes."""
        commented = SOURCE.replace(b'        <usage name="Medic" />', b'        <!-- before -->\r\n        <usage name="Medic" />')
        commented = commented.replace(b'            <tag name="floor" />', b'            <!-- after -->\r\n            <tag name="floor" />')
        enabled = patch_zones(commented, commented, True)
        self.assertIn(b"<!-- before -->", enabled)
        self.assertIn(b"<!-- after -->", enabled)
        self.assertEqual(patch_zones(enabled, commented, False), commented)

    def test_added_pair_layout_and_empty_element_container(self) -> None:
        """The added pair takes the line layout of the run or first child; an empty-element tag returns as it was."""
        enabled = patch_zones(SOURCE, SOURCE, True)
        self.assertIn(b'<container name="lootShelves">\r\n            <category name="tools" />\r\n'
                      b'            <category name="containers" />\r\n            <point pos="1 2 3" />', enabled)
        self.assertIn(b'<container name="pointsOnly">\r\n            <category name="tools" />\r\n'
                      b'            <category name="containers" />\r\n            <point pos="4 5 6" />', enabled)
        self.assertIn(b'<container name="empty" lootmax="1" ><category name="tools" /><category name="containers" />'
                      b'</container>', enabled)
        self.assertIn(b'<container name="empty" lootmax="1" />\r\n', patch_zones(enabled, SOURCE, False))

    def test_malformed_or_utf16_files_refuse(self) -> None:
        """Bytes that cannot be parsed refuse as a file error; UTF-16, also without a byte order mark, names its cause."""
        with self.assertRaises(ConfigurationFileError):
            zones_enabled(b"<prototype><group>", b"<prototype><group>")
        message = ("mapgroupproto.xml has a layout that this setting cannot change safely: the file is not UTF-8. "
                   "No file was changed.")
        text = "<?xml version='1.0' encoding='UTF-16'?><prototype><group name='Hospital'><usage name='Medic'/>" \
               "<usage name='Town'/></group></prototype>"
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            data = text.encode(encoding)
            with self.subTest(encoding=encoding), self.assertRaises(ConfigurationFileError) as caught:
                patch_zones(data, data, True)
            self.assertEqual(str(caught.exception), message)

    def test_a_qualifying_editor_group_is_never_patched(self) -> None:
        """A reserved group that the medical rule would qualify keeps every byte, on and off."""
        editor = (b'    <group name="ServerManMap_G_hospital0123" lootmax="60">\r\n'
                  b'        <usage name="Medic" />\r\n        <usage name="Town" />\r\n    </group>\r\n')
        source = SOURCE.replace(HOUSE_GROUP, editor + HOUSE_GROUP)
        enabled = patch_zones(source, source, True)
        self.assertIn(editor, enabled)
        self.assertEqual(patch_zones(enabled, source, False), source)
        alone = b"<prototype>\r\n" + editor + b"</prototype>\r\n"
        self.assertEqual(patch_zones(alone, alone, True), alone)

    def test_off_keeps_a_point_added_to_an_opened_container(self) -> None:
        """An opened empty-element container that gained a point while on stays open; off keeps the point."""
        enabled = patch_zones(SOURCE, SOURCE, True)
        opened = b'<container name="empty" lootmax="1" ><category name="tools" /><category name="containers" />'
        edited = enabled.replace(opened, opened + b'<point pos="7 8 9" />', 1)
        disabled = patch_zones(edited, SOURCE, False)
        self.assertIn(b'<container name="empty" lootmax="1" ><point pos="7 8 9" /></container>', disabled)
        self.assertFalse(zones_enabled(disabled, SOURCE))

    def test_a_comment_in_a_current_run_refuses_off(self) -> None:
        """A comment that the current file holds inside a category run refuses Disable and is not removed."""
        enabled = patch_zones(SOURCE, SOURCE, True)
        pair = b'<category name="tools" />\r\n            <category name="containers" />'
        commented = enabled.replace(pair, b'<category name="tools" />\r\n            <!-- kept -->\r\n'
                                    b'            <category name="containers" />', 1)
        self.assertNotEqual(commented, enabled)
        with self.assertRaises(ConfigurationFileError) as caught:
            patch_zones(commented, SOURCE, False)
        self.assertEqual(str(caught.exception), "mapgroupproto.xml has a layout that this setting cannot change "
                         "safely: group Land_City_Hospital has a comment or text between usage and category. "
                         "No file was changed.")

    def test_an_added_editor_group_is_ignored(self) -> None:
        """A reserved medical group that only the current file holds is neither refused nor patched."""
        editor = (b'    <group name="ServerManMap_G_hospital0123" lootmax="60">\r\n'
                  b'        <usage name="Medic" />\r\n        <usage name="Town" />\r\n    </group>\r\n')
        current = SOURCE.replace(HOUSE_GROUP, editor + HOUSE_GROUP)
        self.assertFalse(zones_enabled(current, SOURCE))
        enabled = patch_zones(current, SOURCE, True)
        self.assertIn(editor, enabled)
        self.assertEqual(patch_zones(enabled, SOURCE, False), current)

    def test_reserved_groups_are_detected_without_regard_to_case(self) -> None:
        """The editor guard finds a reserved group in any letter case, and only then."""
        self.assertTrue(holds_reserved_group(SOURCE))
        self.assertTrue(holds_reserved_group(b'<prototype><group name="servermanmap_G_1"/></prototype>'))
        self.assertFalse(holds_reserved_group(HOUSE_GROUP.strip()))

    def test_whole_document_transform_keeps_its_meaning(self) -> None:
        """The zone transform of an original document is the localized on form."""
        self.assertEqual(transform_medical_feature("medical_loot_zones", SOURCE), patch_zones(SOURCE, SOURCE, True))


class MedicalCloneRuleTests(unittest.TestCase):
    """Generated groups take medical children from their source group by the same rule."""

    def test_clone_rule_matches_the_ordinary_patch(self) -> None:
        """On and off, the rule gives the children that the patched ordinary group has."""
        source = _children(SOURCE, "Land_City_Hospital")
        enabled = _children(patch_zones(SOURCE, SOURCE, True), "Land_City_Hospital")
        self.assertEqual(medical_children(source, True).shape(), enabled.shape())
        self.assertEqual(medical_children(source, False), source)

    def test_clone_eligibility_comes_from_the_source_group(self) -> None:
        """A source that does not qualify keeps its children; the clone's own name plays no part."""
        house = _children(SOURCE, "Land_House")
        self.assertEqual(medical_children(house, True), house)
        renamed = MedicalChildren("ServerManMap_G_0123456789ab", house.usages, house.containers)
        self.assertEqual(medical_children(renamed, True), renamed)
        source = _children(SOURCE, "Land_City_Hospital")
        clone = medical_children(source, True)
        self.assertEqual([usage.name for usage in clone.usages], ["Medic"])
        self.assertEqual(clone.containers[2].categories[0].data, b'<category name="tools" />')


if __name__ == "__main__":
    unittest.main()
