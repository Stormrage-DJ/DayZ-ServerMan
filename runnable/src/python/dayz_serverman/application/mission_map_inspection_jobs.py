"""The Inspect jobs of one session: the four-job limit, the 60 s idle end and retirement of generations.

A Start takes an arrival number for its profile when it enters. Only the newest arrival of a profile may
create a job. So an older Start that ends later never replaces the job of a newer one. The rule is
"Inspect Start Order" in architecture.md (Architect 2026-10-14 01:26:20).
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any


# Live jobs of one session; a fifth start evicts the oldest creation
JOB_LIMIT = 4
# A job ends this long after its last accepted call
IDLE_SECONDS = 60.0


class InspectionUnknown(LookupError):
    """The job token is unknown: cancelled, replaced, expired, evicted, ended or never issued."""


@dataclass(eq=False)
class InspectionJob:
    """One generation of an Inspect read; a retired generation never publishes or revives."""

    job_id: str
    profile_id: str
    run: Any
    created: int
    active_at: float
    # The last published running answer; a simultaneous continue returns it
    progress: dict[str, Any] | None = None
    advancing: bool = False
    retired: bool = field(default=False, repr=False)


class InspectionJobs:
    """Thread-safe registry; a short metadata lock guards it, and no file work runs under that lock."""

    def __init__(self, *, clock: Callable[[], float] = monotonic, limit: int = JOB_LIMIT,
                 idle_seconds: float = IDLE_SECONDS) -> None:
        """Start an empty registry; tests inject the clock, the limit stays four in production."""
        self._clock = clock
        self._limit = limit
        self._idle = idle_seconds
        self._lock = threading.Lock()
        self._jobs: dict[str, InspectionJob] = {}
        self._sequence = 0
        # The newest arrival number of each profile, and the last number given; kept in memory only
        self._arrivals: dict[str, int] = {}
        self._arrived = 0

    def arrive(self, profile_id: object) -> int:
        """Give a Start its arrival number and record it as the newest arrival of the profile.

        A profile ID that is not text gets a number, but its Start fails before it creates a job.
        """
        with self._lock:
            self._arrived += 1
            if isinstance(profile_id, str):
                self._arrivals[profile_id] = self._arrived
            return self._arrived

    def create(self, profile_id: str, run: Any, progress: Callable[[str], dict[str, Any]],
               arrival: int | None = None) -> InspectionJob:
        """Register a new job that its start advances at once; `progress` gives its first running answer.

        It replaces the earlier job of the same editor and evicts the oldest job when four are live.
        One editor window serves one profile, so the profile identifies the editor. With an arrival
        number that is no longer the newest of the profile, it raises InspectionUnknown and retires nothing.
        """
        with self._lock:
            if arrival is not None and self._arrivals.get(profile_id) != arrival:
                raise InspectionUnknown(None)
            now = self._clock()
            self._expire(now)
            for job in [job for job in self._jobs.values() if job.profile_id == profile_id]:
                self._retire(job)
            while len(self._jobs) >= self._limit:
                self._retire(min(self._jobs.values(), key=lambda job: job.created))
            self._sequence += 1
            job = InspectionJob(secrets.token_hex(16), profile_id, run, self._sequence, now)
            job.progress = progress(job.job_id)
            job.advancing = True
            self._jobs[job.job_id] = job
            return job

    def claim(self, profile_id: object, job_id: object) -> tuple[InspectionJob, dict[str, Any] | None]:
        """Accept a continue: return the job to advance, or the last progress while another call advances it."""
        with self._lock:
            job = self._find(profile_id, job_id)
            job.active_at = self._clock()
            if job.advancing:
                return job, job.progress
            job.advancing = True
            return job, None

    def settle(self, job: InspectionJob, answer: dict[str, Any], *, terminal: bool) -> bool:
        """Commit the answer of the current generation; return False when it was retired meanwhile."""
        with self._lock:
            if job.retired:
                return False
            job.advancing = False
            job.active_at = self._clock()
            if terminal:
                self._retire(job)
            else:
                job.progress = answer
            return True

    def fail(self, job: InspectionJob) -> bool:
        """Retire a failed generation; return False when it was already retired."""
        with self._lock:
            if job.retired:
                return False
            self._retire(job)
            return True

    def cancel(self, profile_id: object, job_id: object) -> None:
        """Retire one live job without any file work."""
        with self._lock:
            self._retire(self._find(profile_id, job_id))

    def live(self) -> int:
        """Return the number of live jobs, after expiry."""
        with self._lock:
            self._expire(self._clock())
            return len(self._jobs)

    def _find(self, profile_id: object, job_id: object) -> InspectionJob:
        """Return the live job of this profile, or raise InspectionUnknown."""
        self._expire(self._clock())
        job = self._jobs.get(job_id) if isinstance(job_id, str) else None
        if job is None or job.profile_id != profile_id:
            raise InspectionUnknown(job_id)
        return job

    def _expire(self, now: float) -> None:
        """Retire every job whose last accepted call is older than the idle time."""
        for job in [job for job in self._jobs.values() if now - job.active_at > self._idle]:
            self._retire(job)

    def _retire(self, job: InspectionJob) -> None:
        """Mark the generation retired and drop its references, so its memory can be released."""
        job.retired = True
        self._jobs.pop(job.job_id, None)
