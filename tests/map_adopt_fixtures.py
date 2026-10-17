"""Fixture of the `map adopt` tests (leaf 10.36): the CLI handler over a real bridge facade and a real lane.

The facade serves the two real adoption methods of the 10.35 coordinator, `get_operation` and the snapshot. They
run over the moved, copied, retired and restored missions of `CommitFixture`. Its server state is RUNNING, because
adoption needs no stopped server (AR-3). A test can raise after the commit point through the fault hook.
"""

from __future__ import annotations

import io
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dayz_serverman.application.coordinator import ApplicationCoordinator
from dayz_serverman.application.mission_map_adoption_coordinator import MissionMapAdoptionCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.cli import waiter
from dayz_serverman.cli.bridge_client import BridgeClient, CliBridgeError
from dayz_serverman.cli.command_table import COMMANDS
from dayz_serverman.cli.commands import map_adopt
from dayz_serverman.cli.interrupts import Interrupts
from dayz_serverman.cli.output import CliFailure, Output
from dayz_serverman.cli.runner import CommandContext
from dayz_serverman.repositories.mission_map_layout import association_folder, invalidated_name
from tests.mission_map_adoption_commit_support import CommitFixture
from tests.mission_map_adoption_support import MOMENT
from tests.mission_map_mapgroup_support import zone

SPEC = next(command for command in COMMANDS if command.name == "map adopt")


class Run(SimpleNamespace):
    """One command run: exit code, stdout, stderr and the bridge methods called."""

    def json(self) -> dict[str, Any]:
        """Return the JSON document of the run."""
        return json.loads(self.stdout)


class AdoptCliFixture(CommitFixture):
    """The adoption commit fixture with a lane and a facade that serves the methods of `map adopt`."""

    def setUp(self) -> None:
        """Build the lane, the adoption coordinator and the facade; the waiter polls without a delay."""
        super().setUp()
        self.lane = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(self.lane.shutdown, 5)
        operations = ApplicationCoordinator(None, self.lane, None).handlers()  # type: ignore[arg-type]
        self.block: str | None = None
        self.facade = BridgeFacade({
            **MissionMapAdoptionCoordinator(self.adoption, self.lane).handlers(),
            "get_application_snapshot": lambda _parameters: {"mutation_block": self.block,
                                                             "mutation_block_owner": None},
            "get_operation": operations["get_operation"],
            "request_operation_cancellation": operations["request_operation_cancellation"],
        })
        poll = patch.object(waiter, "POLL_SECONDS", 0.01)
        poll.start()
        self.addCleanup(poll.stop)

    def command(self, *, profile: str = "main", yes: bool = True, json_mode: bool = False, stdin: Any = None,
                interrupts: Interrupts | None = None) -> Run:
        """Run the handler of `map adopt` as the runner does."""
        options = SimpleNamespace(yes=yes, json=json_mode, profile=profile)
        stdout, stderr = io.StringIO(), io.StringIO()
        output = Output(stdout, stderr, json_mode=json_mode, command=SPEC.name)
        client = BridgeClient(self.facade.dispatch)
        record = {"profile_id": profile, "display_name": self.profiles.records[profile].values.display_name}
        context = CommandContext(SPEC, options, client.call, None, output, interrupts or Interrupts(), record, {},
                                 stdin)
        try:
            code = output.success(map_adopt.adopt(context))
        except CliFailure as failure:
            code = output.failure(failure)
        except CliBridgeError as error:
            code = output.failure(error.failure())
        for _ in range(500):
            if self.lane.is_drained():
                break
            time.sleep(0.01)
        return Run(code=code, stdout=stdout.getvalue(), stderr=stderr.getvalue(), calls=list(client.calls),
                   output=output)

    # The six cases of the adoption review (10.13, 10.35); each returns the profile that adopts
    def case_move(self) -> str:
        """A true move: the DayZ root moved after an Apply."""
        self.applied()
        self.move_root()
        return "main"

    def case_copy(self) -> str:
        """A copy after a path change: the old root still exists."""
        self.applied()
        self.move_root(keep_old=True)
        return "main"

    def case_capture(self) -> str:
        """A copy after a capture refusal: a new profile on a copied mission folder."""
        self.applied()
        self.copy_mission()
        return "third"

    def case_records_only(self) -> str:
        """A path change before the first Apply: only the plan and the retirement."""
        self.save([zone("rich")])
        self.move_root()
        return "main"

    def case_retired(self) -> str:
        """A return to an earlier path whose records an earlier adoption retired."""
        self.save([zone("rich")])
        earlier = self.key()
        self.move_root()
        self.retire("main", earlier, self.key())
        self.save([zone("rich")])
        self.save([zone("rich")], expected=0)
        self.move_root(name="DayZServer")
        return "main"

    def case_restore(self) -> str:
        """A restore invalidated the last-applied record (AR-6), as the 10.35 lane test makes it."""
        self.applied()
        folder = association_folder(self.area, "main", self.key())
        (folder / "applied.json").rename(folder / invalidated_name(MOMENT, 1))
        return "main"
