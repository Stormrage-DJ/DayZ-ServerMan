"""Image type and pixel size of a map image, read from its PNG or JPEG header (3.16)."""

from __future__ import annotations

import struct
from dataclasses import dataclass


# Media types of the accepted image files
PNG_MEDIA_TYPE = "image/png"
JPEG_MEDIA_TYPE = "image/jpeg"
# A PNG file starts with its signature and then the IHDR chunk, which holds the width and height
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Start-of-frame markers that give the frame size (baseline, progressive, lossless and arithmetic forms)
JPEG_FRAME_MARKERS = frozenset((0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF))
# Markers without a length field: TEM and the restart markers
JPEG_BARE_MARKERS = frozenset((0x01, *range(0xD0, 0xD8)))


class ImageTypeError(ValueError):
    """Raised when the bytes are not a PNG or JPEG file whose pixel size the header gives."""


@dataclass(frozen=True)
class ImageHeader:
    """Media type and pixel size of one image file."""

    media_type: str
    width: int
    height: int


def image_header(data: bytes) -> ImageHeader:
    """Return the media type and the pixel size of PNG or JPEG bytes, or raise ImageTypeError."""
    if data.startswith(PNG_SIGNATURE):
        return _png_header(data)
    if data.startswith(b"\xff\xd8\xff"):
        return _jpeg_header(data)
    raise ImageTypeError("the file is not a PNG or JPEG image")


def _png_header(data: bytes) -> ImageHeader:
    """Read the width and height of the IHDR chunk that must follow the signature."""
    if len(data) < 24 or data[12:16] != b"IHDR" or struct.unpack(">I", data[8:12])[0] != 13:
        raise ImageTypeError("the PNG file has no valid IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        raise ImageTypeError("the PNG file has an empty image")
    return ImageHeader(PNG_MEDIA_TYPE, width, height)


def _jpeg_header(data: bytes) -> ImageHeader:
    """Walk the JPEG segments to the first start-of-frame marker and read its frame size."""
    position = 2
    while position < len(data):
        if data[position] != 0xFF:
            raise ImageTypeError("the JPEG file has a damaged segment")
        # Fill bytes 0xFF can come before a marker
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in JPEG_BARE_MARKERS:
            continue
        # The image data or the end comes before any frame header
        if marker in (0xD9, 0xDA) or position + 2 > len(data):
            break
        (length,) = struct.unpack(">H", data[position:position + 2])
        if length < 2:
            raise ImageTypeError("the JPEG file has a damaged segment")
        if marker in JPEG_FRAME_MARKERS:
            if length < 7 or position + 7 > len(data):
                break
            height, width = struct.unpack(">HH", data[position + 3:position + 7])
            if width < 1 or height < 1:
                raise ImageTypeError("the JPEG file has no usable frame size")
            return ImageHeader(JPEG_MEDIA_TYPE, width, height)
        position += length
    raise ImageTypeError("the JPEG file has no frame header")
