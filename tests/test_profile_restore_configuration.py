"""Verify context-aware mission patching without losing unrelated configuration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.repositories.configuration_common import ConfigurationFileError, UTF8_BOM
from dayz_serverman.repositories.profile_restore_configuration import (
    read_restore_configuration, transform_restore_configuration,
)

# Exercise comments, unrelated template fields, one-line classes and quoted syntax.
CONFIG = (
    '// instanceId = 99; class Missions { template="fake"; };\r\n'
    'hostname = "Original"; password = "unchanged { template=\\\"x\\\"; }";\r\n'
    'instanceId = 12 /* keep this comment */; steamQueryPort = 2305;\r\n'
    'class Other { template = "untouched"; instanceId = 93; };\r\n'
    '/* class Missions { class Fake { template="fake"; }; }; */\r\n'
    'class Missions { class DayZ : Parent { template = "dayzOffline.chernarusplus";\r\n'
    ' difficulty = "regular"; class Nested { template = "nested"; }; }; };\r\n'
    'customSetting[] = {1, 2, 3};\r\n'
).encode("utf-8")


class RestoreConfigurationTests(unittest.TestCase):
    """Reject ambiguous configs and patch only the owned assignment spans."""

    def test_patch_preserves_all_unowned_bytes_and_encoding(self) -> None:
        """Keep passwords, comments, line endings and unrelated class values intact."""
        for prefix in (b"", UTF8_BOM):
            with self.subTest(bom=bool(prefix)):
                source = prefix + CONFIG
                result = transform_restore_configuration(
                    source, mission_template="serverman-restored.chernarusplus",
                    instance_id=4, steam_query_port=2505, display_name="Restored á",
                )
                expected = source.replace(b'hostname = "Original"', 'hostname = "Restored á"'.encode())
                expected = expected.replace(b"instanceId = 12", b"instanceId = 4")
                expected = expected.replace(b"steamQueryPort = 2305", b"steamQueryPort = 2505")
                expected = expected.replace(b'dayzOffline.chernarusplus', b'serverman-restored.chernarusplus')
                self.assertEqual(result, expected)
                context = read_restore_configuration(result)
                self.assertEqual(context.instance_id, 4)
                self.assertEqual(context.mission_template, "serverman-restored.chernarusplus")
                self.assertEqual(context.steam_query_port, 2505)

    def test_missing_scalars_append_at_top_level_without_changing_template_scope(self) -> None:
        """Default instance one remains explicit after restore transformation."""
        source = b'class Missions { class DayZ { template="dayzOffline.enoch"; }; };'
        self.assertEqual(read_restore_configuration(source).instance_id, 1)
        result = transform_restore_configuration(
            source, mission_template="serverman-new.enoch", instance_id=2, steam_query_port=2405,
        )
        self.assertTrue(result.endswith(b"instanceId = 2;\nsteamQueryPort = 2405;\n"))
        self.assertEqual(result.count(b"template="), 1)
        self.assertEqual(read_restore_configuration(result).instance_id, 2)

    def test_rejects_ambiguous_or_unsafe_configs(self) -> None:
        """Malformed input cannot become a successful restore by appending defaults."""
        valid = b'class Missions { class DayZ { template="dayzOffline.enoch"; }; };'
        cases = (
            b'template="dayzOffline.enoch";',
            valid + valid,
            valid.replace(b'template=', b'template="other.enoch"; template='),
            valid.replace(b'dayzOffline.enoch', b'../dayzOffline.enoch'),
            valid.replace(b'dayzOffline.enoch', b'mpmissions/dayzOffline.enoch'),
            valid + b' instanceId=0;', valid + b' instanceId=-1;',
            valid + b' instanceId=1; INSTANCEID=2;',
            valid + b' steamQueryPort=65536;', valid + b' steamQueryPort=true;',
            valid[:-4], valid + b' /* unclosed', valid + b' hostname="unclosed;',
            valid.decode().encode("utf-16"),
        )
        for content in cases:
            with self.subTest(config=content):
                with self.assertRaises(ConfigurationFileError):
                    read_restore_configuration(content)

    def test_rejects_invalid_destination_values(self) -> None:
        """Prevent booleans, unsafe names and out-of-range ports reaching written config."""
        cases = ({"instance_id": True}, {"instance_id": 0}, {"instance_id": 1.5},
                 {"steam_query_port": True}, {"steam_query_port": 65536},
                 {"mission_template": "../escape"}, {"display_name": "bad\nname"})
        for changes in cases:
            with self.subTest(changes=changes):
                kwargs = dict(mission_template="serverman-new.enoch", instance_id=2,
                              steam_query_port=2405)
                kwargs.update(changes)
                with self.assertRaises(ConfigurationFileError):
                    transform_restore_configuration(CONFIG, **kwargs)


if __name__ == "__main__":
    unittest.main()
