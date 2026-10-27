"""Verify the image checks in their accepted order, the header reads and the selection tokens (3.16)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dayz_serverman.application import mission_map_images
from dayz_serverman.application.mission_map_images import (
    TOKEN_CAPACITY, ImageRefusal, ImageSelectionService, check_image,
)
from dayz_serverman.domain.mission_map_image import ImageTypeError, image_header
from tests.mission_map_image_support import jpeg_header_only, png_header_only, png_rgb


class ImageHeaderTests(unittest.TestCase):
    """The pixel size comes from the PNG IHDR chunk or the JPEG start-of-frame header."""

    def test_png_and_jpeg_sizes(self) -> None:
        """Both types give their media type and size; every frame marker is accepted."""
        self.assertEqual(image_header(png_header_only(3, 2)), image_header(png_rgb(3, 2)))
        header = image_header(png_header_only(8192, 17))
        self.assertEqual((header.media_type, header.width, header.height), ("image/png", 8192, 17))
        for marker in (0xC0, 0xC2, 0xCF):
            header = image_header(jpeg_header_only(640, 480, marker))
            self.assertEqual((header.media_type, header.width, header.height), ("image/jpeg", 640, 480))

    def test_other_bytes_are_unsupported(self) -> None:
        """Text, a truncated PNG, an empty size and a JPEG without a frame header are refused."""
        for data in (b"GIF89a", b"", png_header_only(3, 3)[:20], png_header_only(0, 3),
                     b"\xff\xd8\xff\xd9", jpeg_header_only(0, 4), b"\xff\xd8\xff\xe0\x00\x01"):
            with self.subTest(data=data[:12]), self.assertRaises(ImageTypeError):
                image_header(data)


class ImageFolderFixture(unittest.TestCase):
    """Image files live in a temporary folder only."""

    def setUp(self) -> None:
        """Create an empty temporary folder."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_images_")
        self.folder = Path(self.temp.name)

    def tearDown(self) -> None:
        """Remove the temporary folder."""
        self.temp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        """Write one file and return its path."""
        path = self.folder / name
        path.write_bytes(data)
        return path

    def reason(self, path: Path, byte_limit: int = 1 << 20, side_limit: int = 64) -> ImageRefusal:
        """Return the refusal of one check."""
        with self.assertRaises(ImageRefusal) as caught:
            check_image(path, byte_limit, side_limit)
        return caught.exception


class ImageCheckTests(ImageFolderFixture):
    """check_image refuses in the accepted order: file, link, stat size, read, type, pixels."""

    def test_accepted_image_identity(self) -> None:
        """A good PNG gives its name, type, size, SHA-256, pixel size and bytes."""
        data = png_rgb(5, 4)
        image = check_image(self.write("map.png", data), 1 << 20, 64)
        self.assertEqual((image.file_name, image.media_type, image.byte_size), ("map.png", "image/png", len(data)))
        self.assertEqual((image.pixel_width, image.pixel_height, image.data), (5, 4, data))
        self.assertEqual(len(image.sha256), 64)

    def test_order_and_reasons(self) -> None:
        """A folder, a missing file, a large file, other bytes and too many pixels each have their reason."""
        self.assertEqual(self.reason(self.folder).reason, "not_a_file")
        self.assertEqual(self.reason(self.folder / "absent.png").reason, "missing")
        large = self.reason(self.write("large.png", png_rgb(2, 2, extra=bytes(2000))), byte_limit=1000)
        self.assertEqual((large.reason, large.details["unit"], large.details["limit"]), ("over_budget", "bytes", 1000))
        self.assertGreater(large.details["size"], 1000)
        self.assertEqual(self.reason(self.write("text.png", b"not an image")).reason, "unsupported_image_type")
        wide = self.reason(self.write("wide.png", png_header_only(65, 10)))
        self.assertEqual((wide.reason, wide.details), ("over_budget", {"limit": 64, "size": 65, "unit": "pixels",
                                                                      "width": 65, "height": 10}))
        tall = self.reason(self.write("tall.jpg", jpeg_header_only(3, 70)))
        self.assertEqual((tall.details["size"], tall.details["width"], tall.details["height"]), (70, 3, 70))

    def test_byte_budget_uses_stat_before_any_read(self) -> None:
        """A file above the byte budget is never read."""
        path = self.write("large.png", png_rgb(2, 2, extra=bytes(4000)))
        with mock.patch.object(mission_map_images, "open_shared") as read:
            self.assertEqual(self.reason(path, byte_limit=100).reason, "over_budget")
        read.assert_not_called()

    def test_reparse_point_and_unreadable_file(self) -> None:
        """A link in the final component and a failed shared read are refused."""
        path = self.write("map.png", png_rgb(2, 2))
        real_lstat = os.lstat
        linked = lambda name: SimpleNamespace(st_mode=real_lstat(name).st_mode, st_file_attributes=0x400)
        with mock.patch.object(mission_map_images.os, "lstat", linked):
            self.assertEqual(self.reason(path).reason, "reparse_point")
        with mock.patch.object(mission_map_images, "open_shared", side_effect=PermissionError("locked")):
            self.assertEqual(self.reason(path).reason, "unreadable")
        with mock.patch.object(mission_map_images, "read_at_most", return_value=b"short"):
            self.assertEqual(self.reason(path).reason, "unreadable")

    def test_read_stops_after_the_byte_budget(self) -> None:
        """QA-316-2: the read asks for at most the limit plus 1 byte; a file that grew past it is refused."""
        path = self.write("map.png", png_rgb(2, 2, extra=bytes(5000)))
        self.assertEqual(len(mission_map_images.read_at_most(str(path), 101)), 101)
        # The stat size is within the budget, so only the bounded read sees that the file grew
        small = mock.Mock(st_mode=os.stat(path).st_mode, st_size=50)
        with mock.patch.object(mission_map_images, "read_at_most", wraps=mission_map_images.read_at_most) as read,                 mock.patch.object(mission_map_images.os, "stat", return_value=small):
            grown = self.reason(path, byte_limit=100)
        read.assert_called_once_with(str(path), 101)
        self.assertEqual((grown.reason, grown.details), ("over_budget", {"limit": 100, "size": 101, "unit": "bytes"}))


class SelectionServiceTests(ImageFolderFixture):
    """Tokens bind profile, path, size and SHA-256; only the 16 newest stay."""

    def service(self) -> ImageSelectionService:
        """Return a service with predictable tokens."""
        counter = iter(range(1000))
        return ImageSelectionService(byte_limit=1 << 20, side_limit=64, token=lambda: f"token-{next(counter)}")

    def test_register_read_and_profile_binding(self) -> None:
        """A token reads the same file for its profile only."""
        service, path = self.service(), self.write("map.png", png_rgb(4, 4))
        token, image = service.register("alpha", path)
        self.assertEqual(service.read("alpha", token).sha256, image.sha256)
        self.assertIsNone(service.read("bravo", token))
        self.assertIsNone(service.read("alpha", "unknown"))
        self.assertIsNone(service.read("alpha", 7))
        selection = service.resolve("alpha", token)
        self.assertEqual((selection.path, selection.byte_size), (str(path), len(path.read_bytes())))
        self.assertEqual(len(ImageSelectionService(byte_limit=1, side_limit=1)._token()), 32)

    def test_changed_or_removed_file_reads_as_selection_changed(self) -> None:
        """A different size, different bytes of the same size or a removed file end the token's use."""
        service, path = self.service(), self.write("map.png", png_rgb(4, 4))
        token, _image = service.register("alpha", path)
        for change in (lambda: path.write_bytes(png_rgb(5, 4)), lambda: path.write_bytes(png_rgb(4, 4, lambda x, y: (1, 2, 3))),
                       path.unlink):
            change()
            with self.assertRaises(ImageRefusal) as caught:
                service.read("alpha", token)
            self.assertEqual(caught.exception.reason, "selection_changed")

    def test_register_refusals_and_token_capacity(self) -> None:
        """A missing dialog path is not a file; the 17th token removes the oldest."""
        service = self.service()
        with self.assertRaises(ImageRefusal) as caught:
            service.register("alpha", self.folder / "absent.png")
        self.assertEqual(caught.exception.reason, "not_a_file")
        path = self.write("map.png", png_rgb(2, 2))
        tokens = [service.register("alpha", path)[0] for _ in range(TOKEN_CAPACITY + 1)]
        self.assertIsNone(service.resolve("alpha", tokens[0]))
        self.assertTrue(all(service.resolve("alpha", token) for token in tokens[1:]))


if __name__ == "__main__":
    unittest.main()
