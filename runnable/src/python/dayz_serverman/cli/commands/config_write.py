"""`config set --target server/gameplay (--set k=v ... / --from-file F)` (10.4; criteria 5, 6, 8 and 19).

The pre-step typed the updates (10.6). In the owner session the command loads the target for
its revisions and digests, gets the preview as the review, asks (8.1) and applies exactly the
reviewed request, as the Configuration page's Review and Apply do.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..edit_review import config_review
from ..flow import run_operation, stop_if_interrupted
from ..output import CommandResult
from ..wording import missing_configuration
from .common import labelled, profile_id, profile_line, profile_name, read_data
from .write_common import ask, check_recovery_block, write_result


def config_set(context: Any) -> CommandResult:
    """Review and apply typed changes to the profile's server or gameplay configuration."""
    target = context.options.target
    stop_if_interrupted(context.interrupts)
    # State gate before the question: a recovery block exits 6 (rule 5)
    check_recovery_block(context.call("get_application_snapshot"))
    loaded = read_data(lambda: context.call("load_configuration", profile_id=profile_id(context.profile),
                                            target=target),
                       lambda: missing_configuration(target, profile_name(context.profile)))
    request = {"profile_id": profile_id(context.profile), "target": target,
               "expected_profile_revision": loaded.get("profile_revision"),
               "expected_settings_revision": loaded.get("settings_revision"),
               "expected_digest": loaded.get("digest"), "expected_server_digest": loaded.get("server_config_digest"),
               "updates": context.resolved["updates"]}
    # The preview validates the updates and is the review; its refusals come before the question
    preview = context.call("preview_configuration", **request)
    ask(context, [profile_line(context.profile), *config_review(target, loaded, preview)], preview)
    record = run_operation(context, "apply_configuration", "APPLY_CONFIGURATION", **request)
    return write_result(record, preview, written_lines(context, record))


def written_lines(context: Any, record: Mapping[str, Any]) -> list[Any]:
    """Return the text result of an edit: the profile and the file that was written."""
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    return [profile_line(context.profile), labelled("File", str(result.get("relative_path", "")))]
