"""Time allowed for one Workshop update: the download size at an assumed rate, at least 5 minutes.

SteamCMD ends a Workshop download after its own fixed time, about 5 minutes, and keeps what it downloaded. A
large item therefore needs several runs. The update runs SteamCMD again while this budget is not spent.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

# The floor: sign-in, connection setup and a small download fit in one SteamCMD session
MINIMUM_SECONDS = 300.0
# 80 % of a 100 Mbit/s line, the common ISP speed, left for Steam: 80 Mbit/s or 10 MB/s
BYTES_PER_SECOND = 10_000_000
# SteamCMD's own time for one Workshop download command
STEAMCMD_ATTEMPT_SECONDS = 300.0
# An item larger than this does not fit in one attempt at the assumed rate (90 % of 3 GB): it gets its own session
LARGE_ITEM_BYTES = 2_700_000_000
# Pause before SteamCMD runs again, so a SteamCMD that fails at once is not restarted in a tight loop
RETRY_PAUSE_SECONDS = 10.0


def download_budget(sizes: Iterable[int | None]) -> float:
    """Return the seconds allowed to download items of these sizes; an unknown size adds nothing."""
    return max(MINIMUM_SECONDS, sum(size for size in sizes if size) / BYTES_PER_SECOND)


def smallest_first(items: tuple, sizes: Mapping[str, int | None]) -> tuple:
    """Order the items by download size, smallest first; items of unknown size follow in their own order.

    SteamCMD downloads the items of one run in the order of its command line.
    """
    known = sorted((item for item in items if sizes.get(item.workshop_id)),
                   key=lambda item: sizes[item.workshop_id])
    return (*known, *(item for item in items if not sizes.get(item.workshop_id)))


def download_groups(items: tuple, sizes: Mapping[str, int | None]) -> list[tuple]:
    """Split the ordered items into SteamCMD sessions: all small and unknown-size items, then each large item alone."""
    large = [item for item in items if (sizes.get(item.workshop_id) or 0) > LARGE_ITEM_BYTES]
    small = tuple(item for item in items if item not in large)
    return [group for group in (small, *((item,) for item in large)) if group]


def session_repeats(group: tuple, sizes: Mapping[str, int | None]) -> int:
    """Return how often the download command of a large item repeats in its session; other groups run once.

    Each repeat continues the same download job for one more SteamCMD attempt, until the budget is covered.
    """
    if len(group) != 1 or (sizes.get(group[0].workshop_id) or 0) <= LARGE_ITEM_BYTES:
        return 1
    return math.ceil(download_budget([sizes[group[0].workshop_id]]) / STEAMCMD_ATTEMPT_SECONDS)
