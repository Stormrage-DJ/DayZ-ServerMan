"""DayZ mission readiness evidence from the current server RPT."""

from __future__ import annotations

from pathlib import Path

from .windows.shared_files import open_shared


MISSION_READY_MARKER = b"Player connect enabled"


class RptMissionReadinessProbe:
    """Find the player-ready marker in an RPT created for the current launch."""

    def is_ready(self, directory: Path | None, started_after_ns: int) -> bool:
        """Return whether a current RPT confirms that the mission accepts players."""
        if directory is None or not directory.is_dir():
            return False
        try:
            candidates = [
                path
                for path in directory.glob("DayZServer_x64_*.RPT")
                if path.is_file() and path.stat().st_mtime_ns >= started_after_ns
            ]
            if not candidates:
                return False
            latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
            with open_shared(latest) as source:
                overlap = b""
                while chunk := source.read(64 * 1024):
                    combined = overlap + chunk
                    if MISSION_READY_MARKER in combined:
                        return True
                    overlap = combined[-(len(MISSION_READY_MARKER) - 1):]
        except OSError:
            return False
        return False
