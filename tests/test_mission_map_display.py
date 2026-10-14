"""Verify the display reads: the tier layer as a 1-bit PNG with its budget, and the background in parts.

The tests call the handlers directly, or through a bridge facade as the handler table registers them
(3.17), over a temporary DayZ root and manager area. No real mission, profile or image is read or written.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any
from unittest import mock

from dayz_serverman.application import mission_map_display
from dayz_serverman.application.mission_map_display import (
    CALL_BYTE_LIMIT, CALL_TEXT_LIMIT, IMAGE_BYTE_LIMIT, IMAGE_PART_BYTES, IMAGE_SIDE_LIMIT, MissionMapDisplay,
)
from dayz_serverman.application.mission_map_images import ImageSelectionService
from dayz_serverman.bridge.contracts import ErrorCode, ensure_json_value
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.domain.mission_map_png import tier_png
from dayz_serverman.domain.mission_map_terrain import ValueRows
from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.mission_map_image_support import png_header_only, png_rgb
from tests.test_mission_map_editor_load_save import CoordinatorFixture
from tests.test_mission_map_terrain import areaflags_bytes, header_of
from tests.test_mission_map_terrain_files import limits_text


class DisplayFixture(CoordinatorFixture):
    """Add the display service and one image selection service to the coordinator fixture."""

    def setUp(self) -> None:
        """Build the display reads over the fixture's plan service."""
        super().setUp()
        self.images = ImageSelectionService(byte_limit=IMAGE_BYTE_LIMIT, side_limit=IMAGE_SIDE_LIMIT)
        self.display = MissionMapDisplay(self.service, self.images)
        self.key = self.service.active_association("main")["mission_key"]
        self.cells = {(0, 0): 0b01, (7, 0): 0b11, (3, 7): 0b10}
        self.areaflags = areaflags_bytes(8, 8, 1000, 1000, 16, 4, self.cells)
        (self.mission / "areaflags.map").write_bytes(self.areaflags)
        (self.mission / "cfglimitsdefinition.xml").write_text(limits_text("Tier1", "Tier2"), encoding="utf-8")

    def tier(self, tier: object, key: object = None) -> dict[str, Any]:
        """Call the tier-layer handler with a complete request."""
        return self.display.read_tier_layer({"profile_id": "main", "mission_key": key or self.key, "tier": tier})

    def background(self, selection: object = None, part: object = 0) -> dict[str, Any]:
        """Call the background handler with a complete request."""
        return self.display.read_background({"profile_id": "main", "mission_key": self.key,
                                             "selection_id": selection, "part": part})

    def store_image(self, data: bytes | None, *, no_path: bool = False) -> Path:
        """Save a plan whose background names an image file in the temporary folder."""
        image = Path(self.temp.name) / "maps" / "map.png"
        image.parent.mkdir(exist_ok=True)
        if data is not None:
            image.write_bytes(data)
        plan = self.plan_for(self.service.active_association("main"), [])
        content = data or b"x"
        plan["background"] = {"file_name": "map.png", "sha256": hashlib.sha256(content).hexdigest(),
                              "byte_size": len(content), "pixel_width": 4, "pixel_height": 4,
                              "path": None if no_path else str(image),
                              "calibration": {"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": -1.0, "f": 4.0}}
        self.service.save("main", plan, self.service.load("main")["revision"])
        return image


class TierLayerTests(DisplayFixture):
    """The tier layer needs a valid header, not tier writes, and refuses only above the call budget."""

    def test_budget_constants_follow_the_a9_gate(self) -> None:
        """The constants are those of the 3.14 Architect decision."""
        self.assertEqual((CALL_TEXT_LIMIT, CALL_BYTE_LIMIT, IMAGE_PART_BYTES), (8_388_608, 6_291_456, 3_145_728))
        self.assertEqual((IMAGE_BYTE_LIMIT, IMAGE_SIDE_LIMIT), (33_554_432, 8_192))
        self.assertEqual(set(self.display.handlers()), {"read_mission_map_tier_layer", "read_mission_map_background"})

    def test_answer_fields_and_png_bytes(self) -> None:
        """A tier name of another case gives the stored spelling and the exact PNG of its value bit."""
        value = self.tier("tier2")
        ensure_json_value(value)
        header = header_of(self.areaflags)
        expected = tier_png(ValueRows(header, 0, 8, self.areaflags[header.value_offset:]), 0b10)
        self.assertEqual(base64.b64decode(value["png"]), expected)
        self.assertEqual({key: value[key] for key in value if key != "png"}, {
            "tier": "Tier2", "columns": 8, "rows": 8, "world_width": 1000, "world_height": 1000,
            "areaflags_sha256": hashlib.sha256(self.areaflags).hexdigest(), "byte_size": len(expected)})
        # Display reads create no manager folder and change no mission file
        self.assertFalse(self.area.exists())
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), self.areaflags)

    def test_unknown_and_unholdable_tiers(self) -> None:
        """A name outside the value flags, past the value bits or of another type is tier_unknown."""
        (self.mission / "cfglimitsdefinition.xml").write_text(
            limits_text("Tier1", "Tier2", "Tier3", "Tier4", "Tier5"), encoding="utf-8")
        self.assertEqual(self.tier("Tier4")["tier"], "Tier4")
        for tier in ("Tier5", "Other", 2, None):
            self.refused(lambda: self.tier(tier), ErrorCode.INVALID_REQUEST, "tier_unknown")

    def test_png_above_the_call_budget_is_refused_after_encoding(self) -> None:
        """The budget compares the PNG bytes with 6,291,456 bytes; no base64 text is made."""
        with mock.patch.object(mission_map_display, "CALL_BYTE_LIMIT", 40), \
                mock.patch.object(mission_map_display.base64, "b64encode") as encode:
            error = self.refused(lambda: self.tier("Tier1"), ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, "over_budget")
        encode.assert_not_called()
        self.assertEqual((error.details["limit"], error.details["unit"]), (40, "bytes"))
        self.assertGreater(error.details["size"], 40)
        self.assertFalse(error.retryable)

    def test_mission_file_reasons(self) -> None:
        """Missing, damaged and unreadable files keep their capability reasons; a retry can help only a read."""
        with mock.patch.object(mission_map_display, "read_bytes_shared", side_effect=PermissionError("locked")):
            error = self.refused(lambda: self.tier("Tier1"), ErrorCode.STORAGE_FAILURE, "mission_file_unreadable")
        self.assertTrue(error.retryable)
        (self.mission / "cfglimitsdefinition.xml").write_text("<lists/>", encoding="utf-8")
        self.refused(lambda: self.tier("Tier1"), ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, "limits_invalid")
        (self.mission / "areaflags.map").write_bytes(self.areaflags[:-1])
        self.refused(lambda: self.tier("Tier1"), ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, "tier_header_invalid")
        (self.mission / "areaflags.map").unlink()
        self.refused(lambda: self.tier("Tier1"), ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, "areaflags_missing")

    def test_context_refusals(self) -> None:
        """Another mission key, a moved mission, an unknown profile and wrong parameters are refused."""
        self.refused(lambda: self.tier("Tier1", key="b" * 64), ErrorCode.PROFILE_CONTEXT_MISMATCH,
                     "association_mismatch")
        with mock.patch.object(self.profiles, "read", side_effect=ProfileNotFound("gone")):
            self.refused(lambda: self.tier("Tier1"), ErrorCode.NOT_FOUND, "profile_not_found")
        self.refused(lambda: self.display.read_tier_layer({"profile_id": "main", "tier": "Tier1"}),
                     ErrorCode.INVALID_REQUEST, "invalid_document")
        self.old_folder("earlier")
        self.refused(lambda: self.tier("Tier1"), ErrorCode.PROFILE_CONTEXT_MISMATCH, "mission_path_changed")


class BackgroundReadTests(DisplayFixture):
    """The background read takes a token or reads the stored image, and returns one part per answer."""

    def test_stored_image_availability_reasons(self) -> None:
        """No plan, no path, a missing file, another file, too many pixels and other bytes are explained."""
        self.assertEqual(self.background(), {"available": False, "reason": "no_background"})
        image = self.store_image(None, no_path=True)
        self.assertEqual(self.background()["reason"], "missing")
        image.unlink(missing_ok=True)
        self.store_image(png_rgb(4, 4))
        image.write_bytes(png_rgb(4, 4, lambda x, y: (9, 9, 9)))
        self.assertEqual(self.background(), {"available": False, "reason": "replaced"})
        image.unlink()
        self.assertEqual(self.background(), {"available": False, "reason": "missing"})
        self.store_image(png_header_only(9000, 20))
        self.assertEqual(self.background(), {"available": False, "reason": "over_budget", "limit": 8192,
                                             "size": 9000, "unit": "pixels", "width": 9000, "height": 20})
        self.store_image(b"not an image")
        self.assertEqual(self.background(), {"available": False, "reason": "unsupported_image_type"})

    def test_parts_carry_one_identity_and_join_to_the_file(self) -> None:
        """Each part repeats the checks; the parts in order join to the file; other parts are unknown."""
        data = png_rgb(6, 5, lambda x, y: (x * 40, y * 50, 7), extra=bytes(range(256)))
        self.store_image(data)
        with mock.patch.object(mission_map_display, "IMAGE_PART_BYTES", 100):
            first = self.background()
            parts = [first] + [self.background(part=part) for part in range(1, first["parts"])]
            self.refused(lambda: self.background(part=first["parts"]), ErrorCode.INVALID_REQUEST, "part_unknown")
        self.assertEqual(first["parts"], -(-len(data) // 100))
        self.assertEqual(b"".join(base64.b64decode(part["data"]) for part in parts), data)
        for index, part in enumerate(parts):
            self.assertEqual((part["part"], part["sha256"], part["byte_size"]),
                             (index, hashlib.sha256(data).hexdigest(), len(data)))
            self.assertEqual((part["file_name"], part["media_type"], part["pixel_width"], part["pixel_height"]),
                             ("map.png", "image/png", 6, 5))
        self.assertNotIn("path", first)
        for part in (-1, True, "0", 1.0):
            self.refused(lambda: self.background(part=part), ErrorCode.INVALID_REQUEST, "part_unknown")

    def test_token_reads_and_refusals(self) -> None:
        """A registered token reads its file; an unknown token or a changed file is refused."""
        image = Path(self.temp.name) / "chosen.png"
        image.write_bytes(png_rgb(3, 3))
        token, _checked = self.images.register("main", image)
        value = self.background(token)
        self.assertEqual((value["available"], value["parts"], base64.b64decode(value["data"])), (True, 1, image.read_bytes()))
        self.refused(lambda: self.background("unknown"), ErrorCode.INVALID_REQUEST, "selection_unknown")
        image.write_bytes(png_rgb(3, 4))
        self.refused(lambda: self.background(token), ErrorCode.SOURCE_CHANGED, "selection_changed")
        self.refused(lambda: self.display.read_background({"profile_id": "main", "mission_key": "x" * 64,
                     "selection_id": token, "part": 0}), ErrorCode.PROFILE_CONTEXT_MISMATCH, "association_mismatch")


class RegisteredDisplayReadTests(DisplayFixture):
    """Through the bridge, the budget details and the `part` parameter keep the accepted A9 form (3.17)."""

    def call(self, method: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one request to the display handlers as the handler table registers them."""
        return BridgeFacade(self.display.handlers()).dispatch({
            "contract_version": 1, "request_id": "r-1", "method": method,
            "parameters": {"profile_id": "main", "mission_key": self.key, **parameters}})

    def test_over_budget_details_and_the_part_parameter_cross_the_bridge(self) -> None:
        """A refused tier keeps limit, size and unit; a stored image adds width and height; `part` is required."""
        with mock.patch.object(mission_map_display, "CALL_BYTE_LIMIT", 40):
            refused = self.call("read_mission_map_tier_layer", {"tier": "Tier1"})
        error = refused["error"]
        self.assertEqual((error["code"], error["retryable"]), ("UNSUPPORTED_SNAPSHOT_CONTENT", False))
        self.assertEqual(set(error["details"]), {"reason", "limit", "size", "unit"})
        self.assertEqual((error["details"]["reason"], error["details"]["limit"], error["details"]["unit"]),
                         ("over_budget", 40, "bytes"))
        self.store_image(png_header_only(9000, 20))
        value = self.call("read_mission_map_background", {"selection_id": None, "part": 0})["value"]
        self.assertEqual(value, {"available": False, "reason": "over_budget", "limit": 8192, "size": 9000,
                                 "unit": "pixels", "width": 9000, "height": 20})
        missing = self.call("read_mission_map_background", {"selection_id": None})
        self.assertEqual(missing["error"]["code"], "INVALID_REQUEST")
