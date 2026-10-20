"""Display reads of the map editor: one original tier layer and the background image in parts (3.15, 3.16).

Both reads are owner methods and permanent parity exclusions (A11), registered in the handler table by
leaf 3.17. The constants are the A9 budgets of leaf 3.14.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.mission_map_png import tier_png
from ..domain.mission_map_terrain import (
    AREAFLAGS_MISSING, FIXED_HEADER, MISSION_FILE_UNREADABLE, VALUE_BITS_FIELD, TerrainDataError, ValueRows,
    build_header, parse_fixed_header, row_byte_span, tier_mask, value_bits_position,
)
from ..repositories.mission_map_layout import TargetClass, target_key
from ..repositories.mission_map_plans import PlanState
from ..repositories.mission_map_terrain import AREAFLAGS_FILE, LIMITS_FILE, read_value_flags
from .mission_map_coordinator_errors import exact_parameters, refusal, translate
from .mission_map_images import CheckedImage, ImageRefusal, ImageSelectionService
from .mission_map_plans import AssociationMismatch, MissionMapPlanService, MissionPathChanged


# Base64 characters in one js_api answer, and the raw bytes that they carry (3 for each 4 characters)
CALL_TEXT_LIMIT = 8_388_608
CALL_BYTE_LIMIT = CALL_TEXT_LIMIT // 4 * 3
# Raw bytes of one background part (4,194,304 base64 characters); only the last part can be shorter
IMAGE_PART_BYTES = 3_145_728
# Largest image file (stat size) and the largest pixel count on each side
IMAGE_BYTE_LIMIT = 33_554_432
IMAGE_SIDE_LIMIT = 8_192
# Mission-file reasons of a tier layer and their codes; an unreadable file can succeed on a retry
_TERRAIN_CODES = {MISSION_FILE_UNREADABLE: ErrorCode.STORAGE_FAILURE}
# Reasons of a refused image and their codes; other reasons are availability reasons of a stored image
_IMAGE_CODES = {"over_budget": ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT,
                "unsupported_image_type": ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT,
                "selection_changed": ErrorCode.SOURCE_CHANGED}


class MissionMapDisplay:
    """Serve the tier-layer read and the background read of one profile's active mission."""

    def __init__(self, plans: MissionMapPlanService, images: ImageSelectionService, *,
                 encode: Callable[[ValueRows, int | None], bytes] = tier_png) -> None:
        """Keep the plan service, the image selection service and the PNG encoder."""
        self._plans = plans
        self._images = images
        self._encode = encode

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the two display-read handlers for the handler table; construction registers nothing."""
        return {"read_mission_map_tier_layer": self.read_tier_layer,
                "read_mission_map_background": self.read_background}

    def read_tier_layer(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the value-layer cells of one tier as a 1-bit PNG, or refuse a PNG above the call budget."""
        exact_parameters(parameters, {"profile_id", "mission_key", "tier"})
        try:
            mission = self._mission(parameters["profile_id"], parameters["mission_key"])
            content, header = _read_areaflags(mission / AREAFLAGS_FILE)
            flags = read_value_flags(mission / LIMITS_FILE)
            # Only a tier that the value layer can hold has cells; names compare without regard to case
            holdable = flags[:header.value_bits]
            tier = parameters["tier"]
            names = [name for name in holdable if isinstance(tier, str) and name.casefold() == tier.casefold()]
            if tier is not None and not names:
                raise refusal(ErrorCode.INVALID_REQUEST, "tier_unknown", "This tier is not in the tier map of this mission.")
            offset, length = row_byte_span(header, 0, header.rows)
            rows = ValueRows(header, 0, header.rows, content[offset:offset + length])
            png = self._encode(rows, None if tier is None else tier_mask(holdable, names[0]))
            # The budget check runs after the PNG encoding and before the base64 encoding
            if len(png) > CALL_BYTE_LIMIT:
                raise refusal(ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, "over_budget",
                              "The original tiers of this mission are too large to show on the map.",
                              limit=CALL_BYTE_LIMIT, size=len(png), unit="bytes")
            return {"tier": None if tier is None else names[0], "columns": header.columns, "rows": header.rows,
                    "world_width": header.world_width, "world_height": header.world_height,
                    "areaflags_sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(png),
                    "png": base64.b64encode(png).decode("ascii")}
        except ApplicationCallError:
            raise
        except TerrainDataError as error:
            raise refusal(_TERRAIN_CODES.get(error.reason, ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT), error.reason,
                          str(error), retryable=error.reason == MISSION_FILE_UNREADABLE) from error
        except Exception as error:
            raise translate(error) from error

    def read_background(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return one part of the chosen or stored image, or the reason why the stored image is not available."""
        exact_parameters(parameters, {"profile_id", "mission_key", "selection_id", "part"})
        part = parameters["part"]
        try:
            if isinstance(part, bool) or not isinstance(part, int) or part < 0:
                raise refusal(ErrorCode.INVALID_REQUEST, "part_unknown", "The image part is not known.")
            self._mission(parameters["profile_id"], parameters["mission_key"])
            if parameters["selection_id"] is None:
                image = self._stored_image(parameters["profile_id"])
                if not isinstance(image, CheckedImage):
                    return image
            else:
                image = self._images.read(parameters["profile_id"], parameters["selection_id"])
                if image is None:
                    raise refusal(ErrorCode.INVALID_REQUEST, "selection_unknown", "Choose the image again.")
            return _image_part(image, part)
        except ApplicationCallError:
            raise
        except ImageRefusal as error:
            raise refusal(_IMAGE_CODES.get(error.reason, ErrorCode.INVALID_REQUEST), error.reason, str(error),
                          **error.details) from error
        except Exception as error:
            raise translate(error) from error

    def _mission(self, profile_id: object, mission_key: object) -> Path:
        """Return the active mission folder when the key names it and no path change is open."""
        profile, _root, relative, mission = self._plans._resolve(profile_id)
        active = {"profile_id": profile.values.profile_id, "mission_root": relative,
                  "mission_key": target_key(TargetClass.MISSION, mission), "runtime_profile_key": None}
        if not isinstance(mission_key, str) or mission_key != active["mission_key"]:
            raise AssociationMismatch("The map editor names another mission. Reload the plan.")
        # A moved mission keeps its earlier plan read only; display reads refuse as every keyed method does
        matches = self._plans._path_change(active)
        if matches:
            raise MissionPathChanged(matches, self._plans._change_reason(active))
        return mission

    def _stored_image(self, profile_id: object) -> CheckedImage | dict[str, Any]:
        """Check the image of the active saved plan; a failed check is an availability answer."""
        loaded = self._plans.load(profile_id)
        plan = loaded["plan"] if loaded["state"] == PlanState.VALID.value else None
        background = plan["background"] if plan is not None else None
        if background is None:
            return {"available": False, "reason": "no_background"}
        if background.get("path") is None:
            return {"available": False, "reason": "missing"}
        try:
            image = self._images.check(background["path"])
        except ImageRefusal as error:
            return {"available": False, "reason": error.reason, **error.details}
        # A different file at the stored path is a replaced image
        if (image.byte_size, image.sha256) != (background["byte_size"], background["sha256"]):
            return {"available": False, "reason": "replaced"}
        return image


def _read_areaflags(path: Path) -> tuple[bytes, Any]:
    """Read the whole areaflags.map in one shared read and check its header and the A7 size formula."""
    try:
        content = read_bytes_shared(path)
    except FileNotFoundError as error:
        raise TerrainDataError(AREAFLAGS_MISSING, "the mission has no areaflags.map") from error
    except OSError as error:
        raise TerrainDataError(MISSION_FILE_UNREADABLE, "areaflags.map could not be read") from error
    fields = parse_fixed_header(content[:FIXED_HEADER.size])
    position = value_bits_position(fields[0], fields[1], fields[4])
    return content, build_header(fields, content[position:position + VALUE_BITS_FIELD.size], len(content))


def _image_part(image: CheckedImage, part: int) -> dict[str, Any]:
    """Return the answer of one part; each part repeats the identity of the whole file."""
    parts = -(-image.byte_size // IMAGE_PART_BYTES)
    if part >= parts:
        raise refusal(ErrorCode.INVALID_REQUEST, "part_unknown", "The image part is not known.")
    start = part * IMAGE_PART_BYTES
    data = image.data[start:min(image.byte_size, start + IMAGE_PART_BYTES)]
    return {"available": True, "file_name": image.file_name, "media_type": image.media_type,
            "sha256": image.sha256, "byte_size": image.byte_size, "pixel_width": image.pixel_width,
            "pixel_height": image.pixel_height, "part": part, "parts": parts,
            "data": base64.b64encode(data).decode("ascii")}
