"""Backend texts and the notice rule of the apply preview (leaf 8.6).

The window and `map preview` print these texts unchanged. A notice never
refuses Apply.
"""

from __future__ import annotations

from typing import Any

# Product Owner text of 2026-10-14 14:29:00 in evidence 7.6, plain text, exact
ZOMBIE_MAX_COUNT_ZERO_TEXT = (
    "ZombieMaxCount is 0 in db/globals.xml of this mission. With this value DayZ spawns no infected, also not in "
    "the infected encounters of this plan. DayZ-ServerMan does not change this value.")
# Product Owner text of 2026-10-14 18:45:00 in evidence 7.6, plain text, exact; the 17:23:24 text is withdrawn
ZONE_SPAWN_DIST_ZERO_TEXT = (
    "ZoneSpawnDist is 0 in db/globals.xml of this mission. In testing with this value, DayZ spawned infected in "
    "the mission's own infected zones where the player stood. It spawned no infected in infected zones whose edge "
    "was 100 m or more from the player. This included infected zones made by the editor. DayZ-ServerMan does not "
    "change this value.")
# Notice codes and texts in their fixed order (Architect 2026-10-14 18:32:23, decision 3)
NOTICES = (("zombie_max_count_zero", "zombie_max_count", ZOMBIE_MAX_COUNT_ZERO_TEXT),
           ("zone_spawn_dist_zero", "zone_spawn_dist", ZONE_SPAWN_DIST_ZERO_TEXT))

# Product Owner texts of 2026-10-15 01:16:10 in evidence 8.7: a stale review (also a late file change) and no plan
PREVIEW_STALE_TEXT = ("The map plan, the profile, the settings or the mission files changed after the preview. "
                      "Nothing was applied. Preview the changes again and check them before you apply.")
NO_SAVED_PLAN_TEXT = "This mission has no saved map plan. Make a plan in the map editor, then preview it."

# Residual risk of the capture check (detailed-design D2, T2.1-M3). Product Owner text of 2026-10-15 15:57:10 in
# evidence 8.10, decision 2; it replaces the 8.6 text
CAPTURE_RISK_TEXT = ("Before the first Apply on this mission folder, DayZ-ServerMan saves the current mission files as "
                     "the original. It does not save map editor output as the original, but it recognizes only "
                     "unchanged map editor output that this DayZ-ServerMan wrote. If these files contain other map "
                     "editor output, Restore original writes that output back.")
# A new relative order of the objects of the last Apply (R06 precedence). Product Owner text of 2026-10-15 15:57:10
# in evidence 8.10, decision 1
ORDER_CHANGED_TEXT = ("The order of zones or encounters in the Layers list changed since the last Apply. Where zones "
                      "overlap, the zone that is higher in the list wins. So this Apply can change the mission files "
                      "in ways that no listed change explains.")


def infected_section(caps: dict[str, Any], written: bool) -> dict[str, Any]:
    """Return the `infected` object of the preview answer from the 7.5 caps.

    A notice is present only when the infected candidate writes at least one
    included encounter and its value is 0. A null value ("not read") gives no
    notice.
    """
    notices = [{"code": code, "text": text} for code, field, text in NOTICES
               if written and caps.get(field) == 0]
    return {"events": caps["events"], "zombie_max_count": caps["zombie_max_count"],
            "zone_spawn_dist": caps["zone_spawn_dist"], "notices": notices}
