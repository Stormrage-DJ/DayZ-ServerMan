"""`map show` and `map inspect`: the observer reads of the mission map plan (leaf 10.4, design D11, D12, D15)."""

from __future__ import annotations

import argparse
import math
import re
from collections.abc import Callable
from typing import Any

from ..exit_codes import USAGE
from ..map_inspect_driver import COMMAND, run_inspection
from ..map_inspect_render import inspect_blocks
from ..map_render import show_blocks
from ..output import CliFailure, CommandResult
from ..wording import missing_mission, usage
from .common import profile_id, profile_name, read_data, with_profile

# X,Z in native metres: two decimal numbers, one comma, no space
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
POINT = re.compile(f"({NUMBER}),({NUMBER})")


def show(context: Any) -> CommandResult:
    """Show the saved map plan and the state of the mission as the window load reports them."""
    loaded = read_data(lambda: context.call("load_mission_map", profile_id=profile_id(context.profile)),
                       lambda: missing_mission(profile_name(context.profile)))
    return CommandResult(loaded, show_blocks(context.profile, loaded))


def target(_call: Callable[..., Any], options: argparse.Namespace, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step of `map inspect`: exactly one of `--point X,Z` and `--object ID`, as the backend target."""
    if options.point is not None and options.object_id is None:
        found = POINT.fullmatch(options.point)
        coordinates = [float(part) for part in found.groups()] if found else []
        if found is None or not all(math.isfinite(value) for value in coordinates):
            raise _refused("--point needs two numbers as X,Z in native metres, for example 7500,7500")
        return {"target": {"point": {"x": coordinates[0], "z": coordinates[1]}}}
    if options.object_id is not None and options.point is None:
        return {"target": {"object": options.object_id}}
    if options.point is not None:
        raise _refused("use --point or --object, not both")
    raise _refused("name a point with --point or an object with --object")


def _refused(message: str) -> CliFailure:
    """Return the exit 2 refusal in the usage sentence of the parser."""
    return CliFailure("USAGE", usage(message, COMMAND), USAGE)


def inspect(context: Any) -> CommandResult:
    """Read the tier and the building capacity at one point, or for one object of the saved plan."""
    answer = run_inspection(context, profile_id(context.profile), context.resolved["target"])
    return CommandResult(with_profile(answer, context.profile), inspect_blocks(context.profile, answer))
