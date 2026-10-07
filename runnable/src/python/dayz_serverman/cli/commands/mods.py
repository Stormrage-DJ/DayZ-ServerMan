"""`mods list` (10.1); `mods verify` and `mods update` come with phase 5."""

from __future__ import annotations

from typing import Any

from ..output import CommandResult, Table, sentence
from ..read_wording import NO_MODS, SCOPE_TEXTS, VERSION_FALLBACK, mod_state
from .common import profile_id, profile_line


def mods_list(context: Any) -> CommandResult:
    """List the mods of the profile with order, name, folder, scope, version and state."""
    rows = context.call("list_mod_inventory", profile_id=profile_id(context.profile))
    value = {"profile_id": profile_id(context.profile), "mods": rows}
    if not rows:
        return CommandResult(value, [profile_line(context.profile), sentence(NO_MODS)])
    table = Table(("#", "Mod", "Directory", "Scope", "Version", "Status"), tuple(
        (str(row.get("order", "")), str(row.get("name") or row.get("directory")), str(row.get("directory")),
         SCOPE_TEXTS.get(str(row.get("launch_scope")), "Client"), str(row.get("version") or VERSION_FALLBACK),
         mod_state(row)) for row in rows), value_columns=frozenset({0, 1, 2, 4}))
    return CommandResult(value, [profile_line(context.profile), table])
