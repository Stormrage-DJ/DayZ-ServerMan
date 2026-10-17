"""The map commands of the command table; each CLI leaf of task 10 adds the specs of its commands (D20).

`command_table.py` appends `MAP_COMMANDS` to `COMMANDS`. The phase value 10 stands for task 10 of the
mission map plan; no code reads it (AR-8).
"""

from __future__ import annotations

from .registry import CommandSpec, Confirm, Form, Kind, OptionSpec, ProfileRule

# The same option as `PROFILE` of `command_table.py`, which imports this module
PROFILE = OptionSpec("profile", Form.VALUE, "--profile", metavar="ID-OR-NAME")
# Exactly one of the two targets of `map inspect` (D11); the pre-step refuses none and both
POINT = OptionSpec("point", Form.VALUE, "--point", metavar="X,Z")
OBJECT = OptionSpec("object_id", Form.VALUE, "--object", metavar="ID")
# `map save`: the plan document to save, and the pin of the plan revision (D11); new destinations, new help texts
PLAN_FILE = OptionSpec("plan_file", Form.VALUE, "--from-file", required=True, metavar="FILE")
PLAN_REVISION = OptionSpec("plan_revision", Form.INTEGER, "--expect-revision", minimum=0, metavar="N")
# `map apply`: one choice for each file and encounter of the review, each option repeatable (D11, D14)
KEEP_CHANGE = OptionSpec("keep_change", Form.APPEND, "--keep-change", metavar="PATH")
REPLACE_FILE = OptionSpec("replace_file", Form.APPEND, "--replace-file", metavar="PATH")
EXCLUDE_ENCOUNTER = OptionSpec("exclude_encounter", Form.APPEND, "--exclude-encounter", metavar="ID")
# `map restore`: the mode as a word. The pre-step refuses another word with the 8.8 text, so the parser has no choices
RESTORE_MODE = OptionSpec("restore_mode", Form.POSITIONAL, metavar="original|previous")
# `map export`: the file to write, and the flag that lets it replace an existing file (D11, D17)
OUTPUT_FILE = OptionSpec("output_file", Form.VALUE, "--output", required=True, metavar="FILE")
OVERWRITE_OUTPUT = OptionSpec("overwrite_output", Form.FLAG, "--overwrite")
# `map import`: Replace or Merge with no default, and the confirmation of the listed items (D11, D14). The pre-step
# refuses another mode with the Product Owner text, so the parser has no choices
TRANSFER_MODE = OptionSpec("transfer_mode", Form.VALUE, "--mode", required=True, metavar="replace|merge")
CONFIRM_LISTED = OptionSpec("confirm_listed", Form.FLAG, "--confirm-listed")
# `map copy`: the source profile, resolved as `--profile` in the pre-step (D11)
SOURCE_PROFILE = OptionSpec("source_profile", Form.VALUE, "--from-profile", required=True, metavar="ID-OR-NAME")

MAP_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(("map", "show"), Kind.READ, 10, frozenset(("load_mission_map",)), (PROFILE,),
                profile=ProfileRule.READ_DEFAULT, handler="commands.map_read:show"),
    CommandSpec(("map", "inspect"), Kind.READ, 10,
                frozenset(("inspect_mission_map", "continue_mission_map_inspection",
                           "cancel_mission_map_inspection")),
                (PROFILE, POINT, OBJECT), profile=ProfileRule.READ_DEFAULT, prestep="commands.map_read:target",
                handler="commands.map_read:inspect"),
    # Owner commands of the saved plan (leaf 10.15): save asks nothing (PO-5), set-aside confirms (D14)
    CommandSpec(("map", "save"), Kind.WRITE, 10, frozenset(("load_mission_map", "save_mission_map_plan")),
                (PROFILE, PLAN_FILE, PLAN_REVISION), profile=ProfileRule.WRITE_REQUIRED,
                prestep="commands.map_plan:plan_document", handler="commands.map_plan:save"),
    CommandSpec(("map", "set-aside"), Kind.WRITE, 10, frozenset(("load_mission_map", "set_aside_mission_map_plan")),
                (PROFILE,), confirm=Confirm.CONFIRM, profile=ProfileRule.WRITE_REQUIRED,
                handler="commands.map_plan:set_aside"),
    # Leaf 10.5: the preview is an owner command that changes nothing (AR-1); Apply reviews and asks (D14)
    CommandSpec(("map", "preview"), Kind.WRITE, 10, frozenset(("load_mission_map", "preview_mission_map")),
                (PROFILE,), profile=ProfileRule.READ_DEFAULT, handler="commands.map_publish:preview"),
    CommandSpec(("map", "apply"), Kind.WRITE, 10,
                frozenset(("get_application_snapshot", "load_mission_map", "preview_mission_map",
                           "apply_mission_map")),
                (PROFILE, KEEP_CHANGE, REPLACE_FILE, EXCLUDE_ENCOUNTER), confirm=Confirm.REVIEW,
                profile=ProfileRule.WRITE_REQUIRED, handler="commands.map_publish:apply"),
    # Leaf 10.32: the restore reviews and asks (D14); recovery is installation-wide, so it names no profile
    CommandSpec(("map", "restore"), Kind.WRITE, 10,
                frozenset(("get_application_snapshot", "preview_mission_map_restore", "restore_mission_map")),
                (PROFILE, RESTORE_MODE), confirm=Confirm.REVIEW, profile=ProfileRule.WRITE_REQUIRED,
                prestep="commands.map_restore:mode", handler="commands.map_restore:restore"),
    CommandSpec(("map", "recover"), Kind.WRITE, 10, frozenset(("inspect_mission_map_recovery",)), (),
                handler="commands.map_restore:recover"),
    # Leaf 10.6: an observer read that asks nothing; the CLI writes the file after the call (PO-4, AR-2)
    CommandSpec(("map", "export"), Kind.READ, 10, frozenset(("export_mission_map_plan",)),
                (PROFILE, OUTPUT_FILE, OVERWRITE_OUTPUT), profile=ProfileRule.READ_DEFAULT,
                handler="commands.map_transfer:export"),
    # Leaf 10.33: an owner command that reviews and asks; --yes never confirms the listed items (PO-3)
    CommandSpec(("map", "import"), Kind.WRITE, 10,
                frozenset(("preview_mission_map_import", "import_mission_map_plan")),
                (PROFILE, PLAN_FILE, TRANSFER_MODE, CONFIRM_LISTED, PLAN_REVISION), confirm=Confirm.REVIEW,
                profile=ProfileRule.WRITE_REQUIRED, prestep="commands.map_transfer:transfer_input",
                handler="commands.map_transfer:import_plan"),
    # Leaf 10.7: the same review and confirmations as import, with the plan of another profile
    CommandSpec(("map", "copy"), Kind.WRITE, 10, frozenset(("preview_mission_map_copy", "copy_mission_map_plan")),
                (PROFILE, SOURCE_PROFILE, TRANSFER_MODE, CONFIRM_LISTED, PLAN_REVISION), confirm=Confirm.REVIEW,
                profile=ProfileRule.WRITE_REQUIRED, prestep="commands.map_transfer:copy_input",
                handler="commands.map_transfer:copy_plan"),
    # Leaf 10.36: the adoption reviews and asks (D14); it takes no other option (D11)
    CommandSpec(("map", "adopt"), Kind.WRITE, 10,
                frozenset(("get_application_snapshot", "preview_mission_map_adoption", "adopt_mission_map")),
                (PROFILE,), confirm=Confirm.REVIEW, profile=ProfileRule.WRITE_REQUIRED,
                handler="commands.map_adopt:adopt"),
)
