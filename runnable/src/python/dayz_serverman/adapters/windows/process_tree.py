"""Compatible process-tree facade over one shared exact custody owner."""
from __future__ import annotations

import os
import signal
from .process_evidence import (
    SAFE_ENVIRONMENT_NAMES, ProcessIdentityUnavailable, safe_child_environment,
    ProcessIdentity, ChildEvidence,
)
from .process_custody import Custody, CustodyError
from .process_identity import held, from_handle, for_pid
from .process_jobs import PrivateJob, members


class OwnedProcessTree:
    """Keep legacy adoption without claiming pre-execution descendant admission."""
    def __init__(self, process) -> None:
        """Expose partial custody through a catch-compatible failure carrier."""
        self.custody, self.process, self.job = Custody(), process, None
        self._job_name = None
        self.custody.anchors["tree"] = self
        # Preserve the held child before adoption can fail part way through setup.
        try:
            self._creation_identity = self._read_creation_identity(process)
            if os.name == "nt":
                self.job = PrivateJob(self.custody)
                self.job.create()
                self._job_name = self.job.name
                self.job.configure_legacy()
                self.job.assign(process._handle)
        except BaseException as error:
            self.custody.fault(error)
            self.custody.raise_failure()

    @classmethod
    def prepare(cls, *, custody=None, kernel=None):
        """Prepare the additive Windows-only strict job before any launch."""
        return PreparedProcessTree(custody=custody, kernel=kernel)

    @property
    def _job(self):
        """Preserve the existing private handle read used by old consumers."""
        return self.job.reference.value() if self.job is not None else None

    @property
    def evidence(self) -> ChildEvidence:
        """Keep schema-1 evidence with complete deterministic job membership."""
        root = ProcessIdentity(self.process.pid, self._creation_identity)
        if self.job is None:
            return ChildEvidence(root.process_id, root.creation_identity, (root,))
        # Every current member receives its own checked borrowed identity reference.
        try:
            values = tuple(sorted((ProcessIdentity(pid, for_pid(pid, self.custody, self.job.kernel))
                                   for pid in self.job.pids()), key=lambda value: value.process_id))
            return ChildEvidence(root.process_id, root.creation_identity, values, self._job_name)
        except BaseException as error:
            self.custody.fault(error)
            self.custody.raise_failure()

    def request_close(self) -> None:
        """Keep the polite signal and exact-held fallback compatibility."""
        try:
            self.process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        except (AttributeError, OSError, ValueError):
            self.process.terminate()

    def terminate_tree(self) -> None:
        """Request checked containment of only the admitted job or held child."""
        try:
            self.job.terminate() if self.job is not None else self.process.terminate()
        except BaseException as error:
            self.custody.fault(error)
            self.custody.raise_failure()

    def kill_tree(self) -> None:
        """Preserve exact-owned escalation without a broad process lookup."""
        if self.job is not None:
            self.terminate_tree()
        else:
            self.process.kill()

    def wait_absent(self, seconds: float) -> bool:
        """Keep finite conservative absence separate from containment requests."""
        if self.job is not None:
            return self.job.wait_absent(seconds)
        import time
        deadline = time.monotonic() + seconds
        while self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.process.poll() is not None

    def _all_absent(self) -> bool:
        """Keep the legacy complete absence predicate and conservative errors."""
        try:
            return not self.job.pids() if self.job is not None else self.process.poll() is not None
        except OSError:
            return False

    def close_job(self) -> None:
        """Check the owned job reference release after proved absence."""
        if self.job is not None:
            try:
                self.job.close()
            except BaseException as error:
                self.custody.fault(error)
                self.custody.raise_failure()

    _read_creation_identity = staticmethod(held)
    _identity_from_handle = staticmethod(from_handle)
    _identity_for_pid = staticmethod(for_pid)
    _job_pids = staticmethod(members)

    @staticmethod
    def _assign_job(process, name):
        """Retain partial job custody on failure of the legacy helper seam."""
        custody = Custody()
        job = PrivateJob(custody)
        custody.anchors["job"] = job
        try:
            job.create(name)
            job.configure_legacy()
            job.assign(process._handle)
            handle = job.reference.value()
            job.reference.transfer()
            return handle
        except BaseException as error:
            custody.fault(error)
            custody.raise_failure()


class PreparedProcessTree(OwnedProcessTree):
    """Use the same shared job custodian for the stronger atomic launch seam."""
    def __init__(self, *, custody=None, kernel=None) -> None:
        """Expose the custodian before job creation or preparation fails."""
        self.custody, self.process, self.job = custody or Custody(), None, None
        self.custody.anchors["tree"] = self
        # Strict admission configures this one new job before any child can execute.
        try:
            if os.name != "nt":
                raise OSError("strict job-at-creation launch requires Windows")
            self.job = PrivateJob(self.custody, kernel)
            self.job.create()
            self._job_name = self.job.name
            self.job.configure()
        except BaseException as error:
            self.custody.fault(error)
            self.custody.raise_failure()

    def launch(self, command, cwd, environment, stderr):
        """Create in the job and preserve every partial native launch output."""
        from .process_creation import create
        try:
            self.process = create(self.custody, self.job, command, cwd, environment, stderr)
            # Creation success still requires exact held identity and membership proof.
            self._creation_identity = held(self.process, self.job.kernel)
            self.job.require_member(self.process.process_reference.value())
            return self.process
        except BaseException as error:
            self.process = self.custody.anchors.get("native-child")
            self.custody.fault(error)
            self.custody.raise_failure()


class WindowsChildProbe:
    """Keep conservative absence and surviving borrowed-handle diagnostics."""
    def __init__(self) -> None:
        """Retain nonpersisted diagnostics until the reporting boundary."""
        self.custodies = []

    def is_absent(self, evidence: ChildEvidence) -> bool:
        """Reject absence when a recorded job or member is unproved."""
        if evidence.job_name is not None and not self._job_absent(evidence.job_name):
            return False
        return all(self._identity_absent(member) for member in evidence.normalized_members)

    def _job_absent(self, job_name: str) -> bool:
        """Check release of the borrowed query reference without owning the job."""
        if os.name != "nt":
            return True
        from .process_bindings import Kernel, NativeError
        custody, kernel = Custody(), Kernel()
        self.custodies.append(custody)
        reference = custody.reserve("job-query", job_name, kernel.close)
        absent = False
        try:
            kernel.acquire(reference, "OpenJobObjectW", 4, False, job_name)
            absent = not members(reference.value(), kernel)
        except NativeError as error:
            if error.operation == "OpenJobObjectW" and error.error_code == 2:
                absent = True
            else:
                custody.fault(error)
        except OSError as error:
            custody.fault(error)
        except BaseException as error:
            custody.fault(error)
            error.custody = custody
            raise
        finally:
            custody.release(reference)
        return absent and custody.primary is None and not custody.secondary

    def _identity_absent(self, identity: ProcessIdentity) -> bool:
        """Preserve PID reuse/error-87 rules without granting termination rights."""
        if os.name != "nt":
            return True
        custody = Custody()
        self.custodies.append(custody)
        try:
            return for_pid(identity.process_id, custody) != identity.creation_identity
        except ProcessIdentityUnavailable as error:
            return error.error_code == 87 and not custody.secondary
        except OSError:
            return False
