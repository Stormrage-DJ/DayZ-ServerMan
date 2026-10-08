"""`migrate preview ROOT` and `migrate apply ROOT [--item ID ... / --all-items]` (10.4; criteria 5, 6, 19, 27, 32).

Both run the window's legacy import flow in one process: the folder is selected and previewed
in the same session that applies it (criterion 19), because a selection and a preview live only
in the composition that made them. `migrate preview` runs in an observer session and changes
nothing; `migrate apply` reviews, asks (8.1) and imports exactly the reviewed items.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..exit_codes import FAILED, REFUSED, USAGE
from ..flow import run_operation, stop_if_interrupted
from ..migrate_review import (
    all_items_blocked, blocked, dialog_blocks, imported_blocks, item_without_data, items, named_items_blocked,
    nothing_to_import, review_blocks, selectable_ids, unknown_item,
)
from ..output import CliFailure, CommandResult
from ..settings_wording import folder_path
from .write_common import ask, check_recovery_block


def preview(context: Any) -> CommandResult:
    """Show what an import of the folder would bring; the value holds the selection and the preview."""
    selection, previewed = _select_and_preview(context.call, context.options.root)
    return CommandResult({"selection": selection, "preview": previewed}, review_blocks(previewed))


def item_selection(call: Any, options: Any, _profile_id: str | None) -> dict[str, Any]:
    """Pre-step of `migrate apply` (6.4.1): the folder previews and every `--item` is listed (exit 2).

    Without `--item`, a folder with no importable and no blocked item exits 1 here (criterion 32
    rule 1). Blocked items and named items that cannot be imported are refusals of the current
    state: the handler checks them after D2 and the recovery gate (rule 4).
    """
    _selection, previewed = _select_and_preview(call, options.root)
    if options.item:
        return {"items": _listed(previewed, options.item)}
    if not selectable_ids(previewed) and not blocked(previewed):
        raise CliFailure("NOT_FOUND", nothing_to_import(), FAILED)
    return {"items": selectable_ids(previewed)}


def apply(context: Any) -> CommandResult:
    """Review the folder's import, ask, then import the chosen items as one lane operation."""
    stop_if_interrupted(context.interrupts)
    # State gate before the question: a recovery block exits 6 (rule 5)
    check_recovery_block(context.call("get_application_snapshot"))
    _selection, previewed = _select_and_preview(context.call, context.options.root)
    # A name that stopped resolving since the pre-step is still an argument error (6.4.1); then the state
    # checks of criterion 32, before the question
    selected = chosen_items(previewed, context.options.item)
    # JSON review: the preview, with the items that the import takes
    review = {**previewed, "selected_items": selected}
    ask(context, [*review_blocks(previewed, selected), *dialog_blocks(len(selected))], review)
    record = run_operation(context, "apply_legacy_import", "IMPORT_LEGACY", preview_id=previewed.get("preview_id"),
                           preview_fingerprint=previewed.get("preview_fingerprint"), selected_items=selected)
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    return CommandResult({"operations": [dict(record)], "review": review, "result": dict(result)},
                         imported_blocks(result))


def chosen_items(previewed: Mapping[str, Any], named: list[str] | None) -> list[str]:
    """Return the items to import: exactly the named ones, or every importable one (the window's default).

    An ID that the preview does not list exits 2. Named items are imported all or not at all
    (criterion 32): a blocked one exits 3 (`MIGRATION_CONFLICT`), one with nothing to import exits
    1 (`NOT_FOUND`). Without names, a folder whose items are all blocked exits 3, and a folder with
    nothing importable exits 1 (criterion 27): the folder resolves, the data is not there.
    """
    if named:
        chosen = _listed(previewed, named)
        refused = [item_id for item_id in chosen if item_id not in selectable_ids(previewed)]
        conflicting = blocked(previewed, refused)
        if conflicting:
            raise CliFailure("MIGRATION_CONFLICT", named_items_blocked(conflicting), REFUSED,
                             details={"blocked_items": conflicting})
        if refused:
            raise CliFailure("NOT_FOUND", item_without_data(refused[0]), FAILED)
        return chosen
    importable = selectable_ids(previewed)
    if not importable and blocked(previewed):
        raise CliFailure("MIGRATION_CONFLICT", all_items_blocked(), REFUSED,
                         details={"blocked_items": blocked(previewed)})
    if not importable:
        raise CliFailure("NOT_FOUND", nothing_to_import(), FAILED)
    return importable


def _listed(previewed: Mapping[str, Any], named: list[str]) -> list[str]:
    """Return the named IDs once each, in order; an ID that the preview does not list exits 2."""
    listed = {str(item.get("item_id")) for item in items(previewed)}
    for item_id in named:
        if item_id not in listed:
            raise CliFailure("USAGE", unknown_item(item_id), USAGE)
    # The same item named twice is one item
    return list(dict.fromkeys(named))


def _select_and_preview(call: Any, root: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Select the folder (its absolute path, as the folder dialog gives it) and preview its import."""
    selection = call("select_legacy_root", root=folder_path(root))
    return selection, call("preview_legacy_import", selection_id=selection.get("selection_id"))
