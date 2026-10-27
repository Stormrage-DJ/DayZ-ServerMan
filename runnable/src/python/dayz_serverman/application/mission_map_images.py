"""In-process selection of map background images: checks, tokens and token reads (3.16).

The service belongs to the owner composition. It is not a bridge method and is on no allowlist:
only the host image dialog registers a path, and the bridge sees only the token.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..adapters.windows.shared_files import open_shared
from ..domain.mission_map_image import ImageTypeError, image_header


# Number of the newest tokens that stay in memory; the tokens end with the process
TOKEN_CAPACITY = 16
# FILE_ATTRIBUTE_REPARSE_POINT when the running Python has no stat constant for it
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ImageRefusal(Exception):
    """Raised when an image check fails; it carries the closed reason and the budget details."""

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        """Store the reason code, the safe message and the optional budget details."""
        self.reason = reason
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class CheckedImage:
    """One image file that passed every check, with its bytes and identity."""

    path: str
    file_name: str
    media_type: str
    byte_size: int
    sha256: str
    pixel_width: int
    pixel_height: int
    data: bytes


@dataclass(frozen=True)
class Selection:
    """A registered image: the profile that chose it, its path and the identity that the token binds."""

    profile_id: str
    path: str
    byte_size: int
    sha256: str


def read_at_most(path: str, count: int) -> bytes:
    """Read at most count bytes from the start of a file through the shared-file adapter."""
    with open_shared(path) as stream:
        return stream.read(count)


def check_image(path: str | os.PathLike[str], byte_limit: int, side_limit: int) -> CheckedImage:
    """Check one image file in the accepted order and return its bytes, or raise ImageRefusal.

    Order: a regular file; no reparse point in the final component; the stat size within the byte
    limit before any read; a read of at most the limit plus 1 byte through the shared-file adapter; a PNG
    or JPEG signature; the pixel size within the side limit.
    """
    name = os.fspath(path)
    try:
        status = os.stat(name)
        if not stat.S_ISREG(status.st_mode):
            raise ImageRefusal("not_a_file", "The map image path is not a file.")
        link = os.lstat(name)
    except FileNotFoundError as error:
        raise ImageRefusal("missing", "The map image file is missing.") from error
    except OSError as error:
        raise ImageRefusal("unreadable", "The map image file could not be read.") from error
    if stat.S_ISLNK(link.st_mode) or getattr(link, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE:
        raise ImageRefusal("reparse_point", "The map image path is a link, which the map editor does not follow.")
    # The byte budget is checked from the stat size before any data is read
    if status.st_size > byte_limit:
        raise ImageRefusal("over_budget", "The map image file is too large.", limit=byte_limit,
                           size=status.st_size, unit="bytes")
    try:
        data = read_at_most(name, byte_limit + 1)
    except OSError as error:
        raise ImageRefusal("unreadable", "The map image file could not be read.") from error
    # A file that grew past the budget after its stat is refused without reading more of it
    if len(data) > byte_limit:
        raise ImageRefusal("over_budget", "The map image file is too large.", limit=byte_limit,
                           size=len(data), unit="bytes")
    # A file that changed size after its stat cannot be trusted
    if len(data) != status.st_size:
        raise ImageRefusal("unreadable", "The map image file changed while it was read.")
    try:
        header = image_header(data)
    except ImageTypeError as error:
        raise ImageRefusal("unsupported_image_type", "The file is not a PNG or JPEG image.") from error
    if header.width > side_limit or header.height > side_limit:
        raise ImageRefusal("over_budget", "The map image has too many pixels.", limit=side_limit,
                           size=max(header.width, header.height), unit="pixels",
                           width=header.width, height=header.height)
    return CheckedImage(name, Path(name).name, header.media_type, len(data), hashlib.sha256(data).hexdigest(),
                        header.width, header.height, data)


class ImageSelectionService:
    """Issue tokens for images that the host dialog chose and read them again by token."""

    def __init__(self, *, byte_limit: int, side_limit: int,
                 token: Callable[[], str] = lambda: secrets.token_hex(16)) -> None:
        """Keep the budgets of the composition and an empty token table."""
        self._byte_limit = byte_limit
        self._side_limit = side_limit
        self._token = token
        self._lock = threading.Lock()
        self._selections: OrderedDict[str, Selection] = OrderedDict()

    def check(self, path: str | os.PathLike[str]) -> CheckedImage:
        """Run every image check on one path with the budgets of this service."""
        return check_image(path, self._byte_limit, self._side_limit)

    def register(self, profile_id: str, path: str | os.PathLike[str]) -> tuple[str, CheckedImage]:
        """Check a dialog choice and return a new token bound to the profile, path, size and SHA-256."""
        try:
            image = self.check(path)
        except ImageRefusal as refusal:
            # A dialog path that no longer exists is not a file the admin can use
            if refusal.reason == "missing":
                raise ImageRefusal("not_a_file", "The map image path is not a file.") from refusal
            raise
        with self._lock:
            token = self._token()
            self._selections[token] = Selection(profile_id, image.path, image.byte_size, image.sha256)
            # Only the newest tokens stay; an older token reads as unknown
            while len(self._selections) > TOKEN_CAPACITY:
                self._selections.popitem(last=False)
        return token, image

    def resolve(self, profile_id: object, token: object) -> Selection | None:
        """Return the selection of a token of this profile, or None when the token is unknown here."""
        with self._lock:
            selection = self._selections.get(token) if isinstance(token, str) else None
        return selection if selection is not None and selection.profile_id == profile_id else None

    def read(self, profile_id: object, token: object) -> CheckedImage | None:
        """Check the token's file again; None when the token is unknown, ImageRefusal("selection_changed") on a change."""
        selection = self.resolve(profile_id, token)
        if selection is None:
            return None
        try:
            image = self.check(selection.path)
        except ImageRefusal as refusal:
            raise ImageRefusal("selection_changed", "The chosen image changed. Choose the image again.") from refusal
        if (image.byte_size, image.sha256) != (selection.byte_size, selection.sha256):
            raise ImageRefusal("selection_changed", "The chosen image changed. Choose the image again.")
        return image
