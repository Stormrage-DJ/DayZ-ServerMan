"""Tests for server and gameplay configuration loading, patching, and publication."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.domain.configuration import (  # noqa: E402
    ConfigurationValidationError,
    GAMEPLAY_FIELDS,
    validate_updates,
)
from dayz_serverman.repositories.atomic_file import AtomicFilePublisher  # noqa: E402
from dayz_serverman.repositories.configuration_files import (  # noqa: E402
    ConfigurationFileError,
    UTF8_BOM,
    load_configuration_file,
    transform_configuration,
)


class ConfigurationFileTests(unittest.TestCase):
    """Loading, patching, and publication contracts for server and gameplay files."""
    def setUp(self) -> None:
        """Create the isolated directory used by each configuration fixture."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_configuration_")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        """Remove the temporary configuration directory."""
        self.temporary.cleanup()

    def test_server_patch_preserves_bom_crlf_comments_and_unknown_lines(self) -> None:
        """Patch server settings without losing the BOM, CRLF endings, or unknown lines."""
        # Seed a UTF-8 BOM file with a comment and a custom line
        path = self.root / "serverDZ.cfg"
        original = (
            'hostname = "Old"; // visible\r\n'
            "maxPlayers = 60;\r\n"
            "disableVoN = 0;\r\n"
            "customSetting = 77; // preserve\r\n"
        )
        path.write_bytes(UTF8_BOM + original.encode("utf-8"))
        snapshot = load_configuration_file(path, "server")

        # Patch the accepted fields and keep the unknown custom line intact
        proposed = transform_configuration(
            snapshot,
            {"hostname": "Közösségi; Szerver", "maxPlayers": 80, "disableVoN": True},
        )

        # Verify preserved bytes and reload the published file
        self.assertTrue(proposed.startswith(UTF8_BOM))
        text = proposed[len(UTF8_BOM):].decode("utf-8")
        self.assertIn('hostname = "Közösségi; Szerver"; // visible', text)
        self.assertIn("maxPlayers = 80;", text)
        self.assertIn("disableVoN = 1;", text)
        self.assertIn("customSetting = 77; // preserve", text)
        self.assertNotIn("\n", text.replace("\r\n", ""))
        path.write_bytes(proposed)
        self.assertEqual(load_configuration_file(path, "server").values["hostname"], "Közösségi; Szerver")

    def test_server_duplicate_malformed_and_bad_types_are_rejected(self) -> None:
        """Reject duplicate, malformed, non-integer, and non-UTF-8 server input."""
        # Duplicate keys and unparsable lines must fail closed
        for index, content in enumerate(
            ('hostname="one";\nhostname="two";\n', 'hostname = "missing semicolon"\n')
        ):
            path = self.root / f"bad-{index}.cfg"
            path.write_text(content, encoding="utf-8")
            with self.assertRaises(ConfigurationFileError):
                load_configuration_file(path, "server")
        # Boolean, fractional, and unsupported values must be rejected
        with self.assertRaises(ConfigurationValidationError):
            validate_updates("server", {"maxPlayers": True})
        with self.assertRaises(ConfigurationValidationError):
            validate_updates("server", {"maxPlayers": 60.5})
        with self.assertRaises(ConfigurationValidationError):
            validate_updates("server", {"unsupported": 1})
        # Invalid UTF-8 bytes must be rejected during load
        invalid_utf8 = self.root / "invalid-utf8.cfg"
        invalid_utf8.write_bytes(b"hostname=\xff;\n")
        with self.assertRaises(ConfigurationFileError):
            load_configuration_file(invalid_utf8, "server")

    def test_gameplay_patch_preserves_unknown_keys_bom_newline_and_value_shapes(self) -> None:
        """Patch gameplay values while unknown keys, BOM, CRLF, and value shapes survive."""
        # Build gameplay data with vendor extensions and CRLF endings
        path = self.root / "cfgGameplay.json"
        data = {
            "version": 123,
            "GeneralData": {"disableBaseDamage": False, "futureFlag": "keep"},
            "PlayerData": {"StaminaData": {"staminaMax": 100.0}},
            "VendorExtension": {"enabled": True},
            "MapData": {"ignoreMapOwnership": False, "futureMapFlag": 9},
            "UIData": {"use3DMap": False},
            "VehicleData": {"boatDecayMultiplier": 1.0, "futureVehicleFlag": "keep"},
        }
        original = json.dumps(data, ensure_ascii=False, indent=4).replace("\n", "\r\n") + "\r\n"
        path.write_bytes(UTF8_BOM + original.encode("utf-8"))
        snapshot = load_configuration_file(path, "gameplay")

        # Patch the map, UI, stamina, and vehicle fields
        proposed = transform_configuration(
            snapshot,
            {
                "GeneralData.disableBaseDamage": True,
                "PlayerData.StaminaData.staminaMax": 125.5,
                "MapData.ignoreMapOwnership": True,
                "MapData.ignoreNavItemsOwnership": True,
                "MapData.displayPlayerPosition": True,
                "MapData.displayNavInfo": False,
                "UIData.use3DMap": True,
                "VehicleData.boatDecayMultiplier": 2.5,
            },
        )
        path.write_bytes(proposed)
        reloaded = load_configuration_file(path, "gameplay")

        # Confirm reloaded values and untouched vendor data
        self.assertEqual(reloaded.values["GeneralData.disableBaseDamage"], True)
        self.assertEqual(reloaded.values["PlayerData.StaminaData.staminaMax"], 125.5)
        self.assertTrue(reloaded.values["MapData.ignoreMapOwnership"])
        self.assertTrue(reloaded.values["UIData.use3DMap"])
        self.assertEqual(reloaded.values["VehicleData.boatDecayMultiplier"], 2.5)
        decoded = proposed[len(UTF8_BOM):].decode("utf-8")
        self.assertIn('"futureFlag": "keep"', decoded)
        self.assertIn('"VendorExtension"', decoded)
        self.assertIn('"futureMapFlag": 9', decoded)
        self.assertIn('"futureVehicleFlag": "keep"', decoded)
        self.assertTrue(decoded.endswith("\r\n"))
        self.assertNotIn("\n", decoded.replace("\r\n", ""))

    def test_gameplay_duplicate_malformed_and_crossed_paths_are_rejected(self) -> None:
        """Reject duplicate keys, malformed JSON, and updates that cross scalar nodes."""
        # Duplicate and truncated documents must fail closed
        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"GeneralData":{"x":1,"x":2}}', encoding="utf-8")
        with self.assertRaises(ConfigurationFileError):
            load_configuration_file(duplicate, "gameplay")
        malformed = self.root / "malformed.json"
        malformed.write_text('{"GeneralData":', encoding="utf-8")
        with self.assertRaises(ConfigurationFileError):
            load_configuration_file(malformed, "gameplay")
        # A scalar where an object is expected must fail closed
        crossed = self.root / "crossed.json"
        crossed.write_text('{"PlayerData":false}', encoding="utf-8")
        snapshot = load_configuration_file(crossed, "gameplay")
        with self.assertRaises(ConfigurationFileError):
            transform_configuration(
                snapshot,
                {"PlayerData.StaminaData.staminaMax": 120.0},
            )

    def test_gameplay_inventory_contains_all_accepted_map_ui_vehicle_fields(self) -> None:
        """Declare every accepted map, UI, and vehicle field with its validation shape."""
        # The inventory must cover all accepted fields
        expected = {
            "MapData.ignoreMapOwnership", "MapData.ignoreNavItemsOwnership",
            "MapData.displayPlayerPosition", "MapData.displayNavInfo",
            "UIData.use3DMap", "VehicleData.boatDecayMultiplier",
        }
        self.assertTrue(expected <= set(GAMEPLAY_FIELDS))
        for key in expected - {"VehicleData.boatDecayMultiplier"}:
            self.assertEqual(GAMEPLAY_FIELDS[key].kind, "boolean")
        multiplier = GAMEPLAY_FIELDS["VehicleData.boatDecayMultiplier"]
        self.assertEqual((multiplier.kind, multiplier.minimum, multiplier.maximum), ("number", 0.0, 5.0))
        validate_updates("gameplay", {key: True for key in expected if key != "VehicleData.boatDecayMultiplier"})
        # Out-of-range, boolean, and infinite multipliers must be rejected
        for invalid in (-0.01, 5.01, True, float("inf")):
            with self.assertRaises(ConfigurationValidationError):
                validate_updates("gameplay", {"VehicleData.boatDecayMultiplier": invalid})

    def test_atomic_publish_failure_preserves_authoritative_content(self) -> None:
        """A failed replace must leave the authoritative file and directory untouched."""
        path = self.root / "serverDZ.cfg"
        path.write_text('hostname = "old";\n', encoding="utf-8")
        snapshot = load_configuration_file(path, "server")
        proposed = transform_configuration(snapshot, {"hostname": "new"})

        def fail_replace(_source: Path, _destination: Path) -> None:
            """Simulate a failing final swap for the atomic publisher."""
            raise OSError("injected replace failure")

        with self.assertRaises(OSError):
            AtomicFilePublisher(fail_replace).publish(path, proposed, snapshot.digest)
        self.assertEqual(path.read_text(encoding="utf-8"), 'hostname = "old";\n')
        # The original content and no staged temporary file must remain
        self.assertEqual(list(self.root.glob(".serverDZ.cfg.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
