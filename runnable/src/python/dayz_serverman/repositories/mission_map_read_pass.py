"""Resumable read pass over the mission files of an Inspect snapshot (Architect A181).

One pass reads every byte of areaflags.map and then of cfglimitsdefinition.xml in
short slices. An object target without a working baseline also reads the three
mapgroup files after them (6.9 ruling).

The caller holds the reader side for each slice. Every slice opens every file
of the pass and compares its signature with the first slice of the pass. Then
it reads blocks of the current file into a running SHA-256 and closes every
handle.
"""

from __future__ import annotations

import hashlib
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import IO, Any

from ..adapters.windows.shared_files import open_shared
from ..domain.mission_map_terrain import (
    FIXED_HEADER, MISSION_FILE_UNREADABLE, VALUE_BITS_FIELD, TerrainDataError, value_bits_position,
)
from .mission_map_mapgroup_read import DIRT, POS, PROTO
from .mission_map_terrain import AREAFLAGS_FILE, LIMITS_FILE


# The files of one pass, in read order
PASS_FILES = (AREAFLAGS_FILE, LIMITS_FILE)
# Building files that an object pass without a working baseline adds after them; an absent file is explicit
BUILDING_FILES = (PROTO, POS, DIRT)
# Bytes of one read; the slice deadline is checked before each block
BLOCK_SIZE = 1 << 20


@dataclass(frozen=True)
class FileSignature:
    """Identity of one open file from fstat of its handle; it detects change, it never proves freshness."""

    volume: int
    file_id: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class PassContent:
    """Retained bytes of a completed capture pass; None marks a file that was absent for the whole pass."""

    area_size: int | None
    head: bytes
    # The value-bit count and the value layer behind it; the usage layer is only hashed
    value_bits_field: bytes
    value_layer: bytes
    limits: bytes | None
    # The building files by relative path when the pass read them, each None when absent; else empty
    buildings: dict[str, bytes | None] = field(default_factory=dict)


def signature_of(stream: IO[Any]) -> FileSignature:
    """Return the signature of an open handle: volume serial, file ID, size and last-write time."""
    status = os.fstat(stream.fileno())
    return FileSignature(status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


class MissionReadPass:
    """Read the mission files of the pass once, slice by slice; no handle survives a slice."""

    def __init__(self, mission_root: Path, *, retain: bool, clock: Callable[[], float] = monotonic,
                 block_size: int = BLOCK_SIZE, buildings: bool = False) -> None:
        """Prepare an empty pass; a capture retains content, a final proof only hashes.

        buildings adds the three mapgroup files; the capture and its proof must use the same value.
        """
        self._names = PASS_FILES + (BUILDING_FILES if buildings else ())
        self._paths = tuple(mission_root / name for name in self._names)
        self._retain = retain
        self._clock = clock
        self._block_size = block_size
        self._index = 0
        self._cursor = 0
        self._hashers = [hashlib.sha256() for _ in self._names]
        # A file without an entry has not been seen; None marks a file that was absent
        self._signatures: dict[int, FileSignature | None] = {}
        self.changed = False
        self._head = bytearray()
        self._keep_from: int | None = None
        self._value_part = bytearray()
        self._limits = bytearray()
        self._extra = {index: bytearray() for index in range(len(PASS_FILES), len(self._names))}

    @property
    def complete(self) -> bool:
        """Report whether every byte of every file of the pass was read without a change."""
        return self._index == len(self._names) and not self.changed

    @property
    def position(self) -> tuple[int, int]:
        """Return the current file index and byte cursor."""
        return self._index, self._cursor

    def digests(self) -> tuple[str | None, ...]:
        """Return the full SHA-256 of each file, or None for an absent file; valid only when complete."""
        return tuple(None if self._signatures.get(index) is None else hasher.hexdigest()
                     for index, hasher in enumerate(self._hashers))

    def read_slice(self, deadline: float) -> None:
        """Run one slice: check every signature, then read blocks until the deadline or the end of the pass.

        The caller holds the reader side. An unreadable file raises mission_file_unreadable.
        """
        for index, path in enumerate(self._paths):
            if self.changed:
                return
            try:
                stream = open_shared(path)
            except FileNotFoundError:
                self._observe(index, None)
                if index == self._index and not self.changed:
                    self._next_file()
                continue
            except OSError as error:
                raise TerrainDataError(MISSION_FILE_UNREADABLE, f"{path.name} could not be read") from error
            # The handle closes before the next file opens, so no handle survives the slice
            with stream:
                try:
                    signature = signature_of(stream)
                    self._observe(index, signature)
                    if index == self._index and not self.changed and self._read_blocks(stream, signature.size,
                                                                                        deadline):
                        self._next_file()
                except OSError as error:
                    raise TerrainDataError(MISSION_FILE_UNREADABLE, f"{path.name} could not be read") from error

    def content(self) -> PassContent:
        """Return the retained bytes of a completed capture pass."""
        if not self.complete or not self._retain:
            raise RuntimeError("only a completed capture pass has content")
        area = self._signatures[0]
        # One copy of the value layer; the retained buffer is released with the pass
        width = bytes(self._value_part[:VALUE_BITS_FIELD.size])
        layer = bytes(memoryview(self._value_part)[VALUE_BITS_FIELD.size:])
        buildings = {self._names[index]: None if self._signatures[index] is None else bytes(data)
                     for index, data in self._extra.items()}
        return PassContent(None if area is None else area.size, bytes(self._head), width, layer,
                           None if self._signatures[1] is None else bytes(self._limits), buildings)

    @property
    def retained_bytes(self) -> int:
        """Return the number of bytes that the pass keeps in memory."""
        return (len(self._head) + len(self._value_part) + len(self._limits)
                + sum(len(data) for data in self._extra.values()))

    def _observe(self, index: int, signature: FileSignature | None) -> None:
        """Record the first signature of a file in this pass, or mark the pass changed on a difference."""
        if index not in self._signatures:
            self._signatures[index] = signature
        elif self._signatures[index] != signature:
            self.changed = True

    def _read_blocks(self, stream: IO[Any], size: int, deadline: float) -> bool:
        """Read from the cursor until the deadline or the recorded size; return True at the end of the file."""
        stream.seek(self._cursor)
        while self._cursor < size:
            if self._clock() >= deadline:
                return False
            block = stream.read(min(self._block_size, size - self._cursor))
            # A file shorter than its signature size changed while it was read
            if not block:
                self.changed = True
                return False
            self._hashers[self._index].update(block)
            if self._retain:
                self._keep(block)
            self._cursor += len(block)
        return True

    def _keep(self, block: bytes) -> None:
        """Retain the header, the value part and the limits bytes; the usage layer is only hashed."""
        start = self._cursor
        if self._index == 1:
            self._limits += block
            return
        if self._index in self._extra:
            self._extra[self._index] += block
            return
        if start < FIXED_HEADER.size:
            self._head += block[:FIXED_HEADER.size - start]
            if len(self._head) == FIXED_HEADER.size:
                columns, rows, _width, _height, usage_bits = struct.unpack("<5I", self._head)
                # The header checks run after the pass; an invalid header only retains less
                self._keep_from = max(FIXED_HEADER.size, value_bits_position(columns, rows, usage_bits))
        if self._keep_from is not None and start + len(block) > self._keep_from:
            self._value_part += block[max(self._keep_from - start, 0):]

    def _next_file(self) -> None:
        """Move the cursor to the start of the next file."""
        self._index += 1
        self._cursor = 0
