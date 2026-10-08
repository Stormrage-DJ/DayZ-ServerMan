"""`mods verify` (10.3): the Mods page's "Verify files", with one result line per Workshop mod.

The verification reads every downloaded mod file and its server folder copy on the lane and
replaces the stored proofs; it changes no server file. Problems are a result, not a failure:
the operation succeeded, so the command exits 0 and lists them, as the page marks its rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..flow import run_operation
from ..mods_wording import VERIFIED, verify_problems, verify_summary
from ..output import Block, CommandResult, Table, sentence
from .common import profile_id, profile_line
from .write_common import read_revisions


def verify(context: Any) -> CommandResult:
    """Verify the downloads and server copies of the profile's Workshop mods; print one line per mod."""
    state = read_revisions(context)
    record = run_operation(context, "verify_mod_files", "VERIFY_WORKSHOP_FILES",
                           profile_id=profile_id(context.profile), expected_profile_revision=state.profile_revision,
                           expected_settings_revision=state.settings_revision)
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    items = [item for item in result.get("items") or [] if isinstance(item, Mapping)]
    blocks: list[Block] = [profile_line(context.profile)]
    if items:
        directories = mod_directories(state.profile)
        rows = tuple((directories.get(str(item.get("workshop_id")), ""), str(item.get("workshop_id", "")),
                      "; ".join(verify_problems(item)) or VERIFIED) for item in items)
        blocks.append(Table(("Mod", "Workshop item", "Result"), rows, value_columns=frozenset({0, 1})))
    blocks.append(sentence(verify_summary(items)))
    return CommandResult({"operations": [dict(record)], "review": None, "result": dict(result)}, blocks)


def mod_directories(profile: Mapping[str, Any]) -> dict[str, str]:
    """Return the mod folder of each Workshop item of a profile record, by its Workshop ID."""
    directories: dict[str, str] = {}
    for mod in profile.get("mods") or []:
        source = mod.get("source") if isinstance(mod, Mapping) and isinstance(mod.get("source"), Mapping) else {}
        if source.get("workshop_id"):
            directories[str(source["workshop_id"])] = str(mod.get("directory", ""))
    return directories
