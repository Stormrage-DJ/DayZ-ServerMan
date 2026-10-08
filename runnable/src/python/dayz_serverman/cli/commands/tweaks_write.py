"""`tweaks set`, `tweaks convert-loadout` and `tweaks medical set` (10.4; criteria 5, 6, 8, 19 and 30).

Each command gates on the state it reads before any question (rule 5), shows the review that
the Tweaks page shows (the preview, or the conversion dialog), asks or takes `--yes` (8.1), then
submits exactly the reviewed request as one lane operation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..edit_review import conversion_review, medical_review, tweaks_review
from ..edit_wording import nothing_to_convert
from ..exit_codes import REFUSED, USAGE
from ..flow import run_operation, stop_if_interrupted
from ..output import CliFailure, CommandResult
from ..wording import missing_mission, unknown_feature
from .common import profile_id, profile_line, profile_name, read_data
from .config_write import written_lines
from .write_common import ask, check_recovery_block, write_result

# The mission target of the starter loadout
STARTER = "starter_loadout"


def tweaks_set(context: Any) -> CommandResult:
    """Review and apply typed changes to one mission target of the profile."""
    target = context.options.target.replace("-", "_")
    loaded = _gate_and_load(context, target)
    request = {"profile_id": profile_id(context.profile), "target": target,
               "expected_profile_revision": loaded.get("profile_revision"),
               "expected_settings_revision": loaded.get("settings_revision"),
               "expected_digest": loaded.get("digest"), "updates": context.resolved["updates"]}
    # The preview validates the updates and is the review; its refusals come before the question
    preview = context.call("preview_mission_configuration", **request)
    ask(context, [profile_line(context.profile), *tweaks_review(target, loaded, preview, request["updates"])],
        preview)
    record = run_operation(context, "apply_mission_configuration", "APPLY_MISSION_CONFIGURATION", **request)
    return write_result(record, preview, written_lines(context, record))


def convert_loadout(context: Any) -> CommandResult:
    """Convert the legacy starter loadout after the window's "Convert legacy starter loadout?" dialog."""
    loaded = _gate_and_load(context, STARTER)
    # The window offers the conversion only for a legacy block: nothing to convert exits 3 (criterion 30)
    if not loaded.get("conversion_required"):
        raise CliFailure("NOTHING_TO_CONVERT", nothing_to_convert(), REFUSED)
    request = {"profile_id": profile_id(context.profile), "expected_profile_revision": loaded.get("profile_revision"),
               "expected_settings_revision": loaded.get("settings_revision"), "expected_digest": loaded.get("digest")}
    # JSON review: the request that the dialog confirms, with the recognized block
    review = {**request, "conversion": loaded.get("conversion")}
    ask(context, [profile_line(context.profile), *conversion_review(loaded)], review)
    record = run_operation(context, "convert_starter_loadout", "CONVERT_STARTER_LOADOUT", **request)
    return write_result(record, review, written_lines(context, record))


def medical_set(context: Any) -> CommandResult:
    """Review and apply one medical loot setting: on or off."""
    feature, enabled = context.resolved["feature"], context.options.state == "on"
    stop_if_interrupted(context.interrupts)
    check_recovery_block(context.call("get_application_snapshot"))
    loaded = read_data(lambda: context.call("load_medical_features", profile_id=profile_id(context.profile)),
                       lambda: missing_mission(profile_name(context.profile)))
    features = loaded.get("features") if isinstance(loaded.get("features"), Mapping) else {}
    state = features.get(feature)
    if not isinstance(state, Mapping):
        # A name that stopped resolving since the pre-step (6.4.1): still an argument error
        raise CliFailure("USAGE", unknown_feature(feature), USAGE)
    request = {"profile_id": profile_id(context.profile), "feature": feature, "enabled": enabled,
               "expected_profile_revision": loaded.get("profile_revision"),
               "expected_settings_revision": loaded.get("settings_revision"), "expected_digest": state.get("digest")}
    preview = context.call("preview_medical_feature", **request)
    ask(context, [profile_line(context.profile), *medical_review(feature, enabled, state)], preview)
    record = run_operation(context, "apply_medical_feature", "APPLY_MEDICAL_FEATURE", **request)
    return write_result(record, preview, [profile_line(context.profile)])


def _gate_and_load(context: Any, target: str) -> Mapping[str, Any]:
    """Refuse a recovery block (exit 6, rule 5), then load the mission target for its revisions and digest."""
    stop_if_interrupted(context.interrupts)
    check_recovery_block(context.call("get_application_snapshot"))
    return read_data(lambda: context.call("load_mission_configuration", profile_id=profile_id(context.profile),
                                          target=target),
                     lambda: missing_mission(profile_name(context.profile)))
