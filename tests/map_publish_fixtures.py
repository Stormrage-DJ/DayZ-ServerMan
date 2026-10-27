"""Fixture of the `map preview` and `map apply` tests (leaf 10.5): the CLI handlers over a real bridge facade.

The facade serves the real 8.6 preview, the real 8.7 Apply lane operation and `get_operation`. They run over
the synthetic missions of the 8.6 and 8.7 tests (`ApplyFixture`).

The commit step keeps the fault hook and the recording guard ports of that fixture. So a test can run a server,
edit a file late or raise after the commit point. `load_mission_map` is the plan service load, and `get_application_snapshot` gives the recovery block of
the test. Only temporary synthetic missions are used.
"""

from __future__ import annotations

import io
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dayz_serverman.application.coordinator import ApplicationCoordinator
from dayz_serverman.application.mission_map_apply_coordinator import MissionMapApplyCoordinator
from dayz_serverman.application.mission_map_publication_coordinator import MissionMapPublicationCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.cli import waiter
from dayz_serverman.cli.bridge_client import BridgeClient, CliBridgeError
from dayz_serverman.cli.command_table import COMMANDS
from dayz_serverman.cli.commands import map_publish
from dayz_serverman.cli.interrupts import Interrupts
from dayz_serverman.cli.output import CliFailure, Output
from dayz_serverman.cli.runner import CommandContext
from tests.mission_map_apply_support import ApplyFixture
from tests.mission_map_mapgroup_support import zone
from tests.test_mission_map_infected import encounter
from tests.test_mission_map_preview import tree

# One included infected encounter, one whose event the mission does not offer (with linked loot), and a zone
INFECTED = encounter("e1", x=600.0, z=600.0, radius=40.0)
UNOFFERED = dict(encounter("u", "Unzoned", x=500.0, z=500.0, radius=50.0),
                 linked_loot={"enabled": True, "tier": None, "abundance": "rich", "radius": None})
OBJECTS = [INFECTED, UNOFFERED, zone("rich")]
# The CLI choice of the unavailable encounter of OBJECTS
EXCLUDE_U = ("--exclude-encounter", "u")
SPECS = {command.name: command for command in COMMANDS}


class Run(SimpleNamespace):
    """One command run: exit code, stdout, stderr, the bridge methods called and the output."""

    def json(self) -> dict[str, Any]:
        """Return the JSON document of the run."""
        return json.loads(self.stdout)


class PublishCliFixture(ApplyFixture):
    """The Apply fixture with a lane and a facade that serves the methods of `map preview` and `map apply`."""

    def setUp(self) -> None:
        """Build the lane, the Apply coordinator and the facade; the waiter polls without a delay."""
        super().setUp()
        self.lane = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(self.lane.shutdown, 5)
        operations = ApplicationCoordinator(None, self.lane, None).handlers()  # type: ignore[arg-type]
        publication = MissionMapPublicationCoordinator(self.preview, None, MissionMapApplyCoordinator(  # type: ignore[arg-type]
            self.service, self.lane), None, None)  # type: ignore[arg-type]
        self.block: str | None = None
        self.facade = BridgeFacade({
            "load_mission_map": lambda parameters: self.load(parameters["profile_id"]),
            "get_application_snapshot": lambda _parameters: {"mutation_block": self.block,
                                                             "mutation_block_owner": None},
            "preview_mission_map": publication.preview_mission_map,
            "apply_mission_map": publication.apply.apply_mission_map,
            "get_operation": operations["get_operation"],
            "request_operation_cancellation": operations["request_operation_cancellation"],
        })
        poll = patch.object(waiter, "POLL_SECONDS", 0.01)
        poll.start()
        self.addCleanup(poll.stop)

    def load(self, profile_id: object) -> dict[str, Any]:
        """Return the load answer of `load_mission_map`; a test can replace it."""
        return self.plans.load(profile_id)

    def command(self, name: str, *flags: str, profile: str = "main", yes: bool = True, json_mode: bool = False,
                stdin: Any = None, interrupts: Interrupts | None = None) -> Run:
        """Run the handler of `map preview` or `map apply` as the runner does, with the choice flags given."""
        spec = SPECS[f"map {name}"]
        values: dict[str, list[str]] = {"keep_change": [], "replace_file": [], "exclude_encounter": []}
        names = {"--keep-change": "keep_change", "--replace-file": "replace_file",
                 "--exclude-encounter": "exclude_encounter"}
        for flag, value in zip(flags[::2], flags[1::2]):
            values[names[flag]].append(value)
        options = SimpleNamespace(yes=yes, json=json_mode, profile=profile,
                                  **{key: items or None for key, items in values.items()})
        stdout, stderr = io.StringIO(), io.StringIO()
        output = Output(stdout, stderr, json_mode=json_mode, command=spec.name)
        client = BridgeClient(self.facade.dispatch)
        record = {"profile_id": profile, "display_name": self.profiles.records[profile].values.display_name}
        context = CommandContext(spec, options, client.call, None, output, interrupts or Interrupts(), record, {},
                                 stdin)
        handler = map_publish.preview if name == "preview" else map_publish.apply
        try:
            code = output.success(handler(context))
        except CliFailure as failure:
            code = output.failure(failure)
        except CliBridgeError as error:
            code = output.failure(error.failure())
        self.drained()
        return Run(code=code, stdout=stdout.getvalue(), stderr=stderr.getvalue(), calls=list(client.calls),
                   output=output)

    def drained(self) -> None:
        """Wait until the lane has no running work, so the next run and the cleanup see the end state."""
        for _ in range(500):
            if self.lane.is_drained():
                return
            time.sleep(0.01)

    def applied(self, objects: list[dict[str, Any]] | None = None) -> None:
        """Save the plan and apply it once through the CLI; the unavailable encounter is excluded."""
        self.save(OBJECTS if objects is None else objects, 25)
        run = self.command("apply", *EXCLUDE_U)
        self.assertEqual(run.code, 0, run.stderr)

    def stores(self) -> tuple[dict[str, bytes], dict[str, bytes]]:
        """Return every file of the DayZ root and of the editor area; operation records are left out."""
        return tree(self.root), tree(self.area)

    def edit_positions(self) -> None:
        """Change mapgrouppos.xml outside the map editor, so the next review lists it as an external change."""
        path = self.mission / "mapgrouppos.xml"
        path.write_bytes(path.read_bytes() + b"\r\n")
