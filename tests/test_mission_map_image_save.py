"""Verify that a save stores the path of a chosen image only through its selection token (3.16)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from dayz_serverman.application.mission_map_coordinator import MissionMapCoordinator
from dayz_serverman.application.mission_map_images import ImageSelectionService
from dayz_serverman.bridge.contracts import ErrorCode
from tests.mission_map_image_support import png_rgb
from tests.test_mission_map_editor_load_save import CoordinatorFixture


class ImageSaveTests(CoordinatorFixture):
    """The window never sends a path; the token names it, and its SHA-256 must be the plan's image."""

    def setUp(self) -> None:
        """Use a coordinator with an image selection service and one chosen image."""
        super().setUp()
        self.images = ImageSelectionService(byte_limit=1 << 20, side_limit=64)
        self.coordinator = MissionMapCoordinator(self.service, images=self.images)
        self.image = Path(self.temp.name) / "chosen.png"
        self.image.write_bytes(png_rgb(4, 3))
        self.token, checked = self.images.register("main", self.image)
        self.reference = {"file_name": "chosen.png", "sha256": checked.sha256, "byte_size": checked.byte_size,
                          "pixel_width": 4, "pixel_height": 3, "path": None,
                          "calibration": {"a": 250.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": -1000 / 3, "f": 1000.0}}

    def test_token_sets_the_stored_path_and_load_hides_it(self) -> None:
        """A matching token stores the dialog path; a later save without a token keeps it."""
        plan = dict(self.draft(), background=dict(self.reference))
        self.save(plan, None, selection=self.token)
        self.assertEqual(self.service.load("main")["plan"]["background"]["path"], str(self.image))
        self.assertIsNone(self.load()["plan"]["background"]["path"])
        self.save(plan, 0)
        self.assertEqual(self.service.load("main")["plan"]["background"]["path"], str(self.image))

    def test_mismatched_unknown_and_foreign_tokens_are_refused(self) -> None:
        """Another digest, no background, an unknown token and another profile's token store nothing."""
        other = dict(self.reference, sha256=hashlib.sha256(b"other").hexdigest())
        self.refused(lambda: self.save(dict(self.draft(), background=other), None, selection=self.token),
                     ErrorCode.INVALID_REQUEST, "selection_mismatch")
        self.refused(lambda: self.save(self.draft(), None, selection=self.token),
                     ErrorCode.INVALID_REQUEST, "selection_mismatch")
        plan = dict(self.draft(), background=dict(self.reference))
        self.refused(lambda: self.save(plan, None, selection="unknown"), ErrorCode.INVALID_REQUEST, "selection_unknown")
        foreign, _image = self.images.register("second", self.image)
        self.refused(lambda: self.save(plan, None, selection=foreign), ErrorCode.INVALID_REQUEST, "selection_unknown")
        self.assertFalse(self.area.exists())
