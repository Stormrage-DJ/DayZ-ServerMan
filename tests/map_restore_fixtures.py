"""Fixture of the `map restore` and `map recover` tests (leaf 10.32): the CLI handlers over a real bridge facade.

The facade serves the five real methods of the publication coordinator: both previews, Apply, both restores and
the editor recovery. It also serves `get_operation`.

The methods run over the synthetic missions of `ApplyFixture`, with its fault hook and recording guard ports. So
a test can run a server, edit a file late or raise after the commit point. Only temporary missions are used.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

from map_publish_fixtures import EXCLUDE_U, OBJECTS, SPECS, PublishCliFixture, Run

from dayz_serverman.application.coordinator import ApplicationCoordinator
from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_apply_coordinator import MissionMapApplyCoordinator
from dayz_serverman.application.mission_map_publication_coordinator import MissionMapPublicationCoordinator
from dayz_serverman.application.mission_map_recovery import MissionMapRecovery
from dayz_serverman.application.mission_map_recovery_coordinator import MissionMapRecoveryCoordinator
from dayz_serverman.application.mission_map_restore import MissionMapRestore
from dayz_serverman.application.mission_map_restore_coordinator import MissionMapRestoreCoordinator
from dayz_serverman.application.mission_map_restore_review import RestoreReader
from dayz_serverman.bridge.facade import BridgeFacade
from dayz_serverman.cli.bridge_client import BridgeClient, CliBridgeError
from dayz_serverman.cli.commands import map_restore
from dayz_serverman.cli.interrupts import Interrupts
from dayz_serverman.cli.output import CliFailure, Output
from dayz_serverman.cli.runner import CommandContext
from tests.mission_map_apply_support import NOW

__all__ = ["EXCLUDE_U", "OBJECTS", "RestoreCliFixture", "Run"]


class RestoreCliFixture(PublishCliFixture):
    """The Apply CLI fixture with the restore service, the recovery handler and a facade with every method."""

    def setUp(self) -> None:
        """Build the restore review and service, the recovery coordinator and the facade of all five methods."""
        super().setUp()
        reader = RestoreReader(self.plans, self.profiles, medical_baseline=self.medical.zone_baseline_path)
        self.restorer = MissionMapRestore(reader, self.commit, self.paths, self.profiles, clock=lambda: NOW)
        settings = SimpleNamespace(load=lambda: self.settings)
        recovery = MissionMapRecovery(self.paths, settings, InstallationGuard(self.ports, self.ports),  # type: ignore[arg-type]
                                      self.ports, self.plans._locks)
        self.recovery = MissionMapRecoveryCoordinator(recovery, self.lane)
        publication = MissionMapPublicationCoordinator(
            self.preview, self.restorer, MissionMapApplyCoordinator(self.service, self.lane),
            MissionMapRestoreCoordinator(self.restorer, self.lane), self.recovery)
        operations = ApplicationCoordinator(None, self.lane, None).handlers()  # type: ignore[arg-type]
        self.facade = BridgeFacade({
            **publication.handlers(),
            "load_mission_map": lambda parameters: self.load(parameters["profile_id"]),
            "get_application_snapshot": lambda _parameters: {"mutation_block": self.block,
                                                             "mutation_block_owner": None},
            "get_operation": operations["get_operation"],
            "request_operation_cancellation": operations["request_operation_cancellation"],
        })

    def restore(self, mode: str, *, profile: str = "main", yes: bool = True, json_mode: bool = False,
                stdin: Any = None, interrupts: Interrupts | None = None) -> Run:
        """Run the pre-step and the handler of `map restore` as the runner does."""
        options = SimpleNamespace(yes=yes, json=json_mode, profile=profile, restore_mode=mode)
        record = {"profile_id": profile, "display_name": self.profiles.records[profile].values.display_name}
        return self._run("map restore", options, record, stdin, interrupts)

    def recover(self, *, json_mode: bool = False) -> Run:
        """Run the handler of `map recover`, which names no profile."""
        return self._run("map recover", SimpleNamespace(yes=False, json=json_mode), None, None, None)

    def _run(self, name: str, options: Any, record: dict[str, Any] | None, stdin: Any,
             interrupts: Interrupts | None) -> Run:
        """Run one command against the facade and return its exit code, output and bridge calls."""
        spec = SPECS[name]
        stdout, stderr = io.StringIO(), io.StringIO()
        output = Output(stdout, stderr, json_mode=options.json, command=spec.name)
        client = BridgeClient(self.facade.dispatch)
        try:
            resolved = map_restore.mode(client.call, options, None) if name == "map restore" else {}
            context = CommandContext(spec, options, client.call, None, output, interrupts or Interrupts(), record,
                                     resolved, stdin)
            handler = map_restore.restore if name == "map restore" else map_restore.recover
            code = output.success(handler(context))
        except CliFailure as failure:
            code = output.failure(failure)
        except CliBridgeError as error:
            code = output.failure(error.failure())
        self.drained()
        return Run(code=code, stdout=stdout.getvalue(), stderr=stderr.getvalue(), calls=list(client.calls),
                   output=output)
