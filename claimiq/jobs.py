"""Batch auditing: submit many claims, poll one job.

The synchronous `/api/audit` is right for the interactive path and wrong for the way a
hospital actually has this problem, which is four thousand claims from last month sitting
in a folder. Sending those one request at a time means the caller writes the retry logic,
the concurrency limit and the progress bar; sending them as one synchronous request means
a gateway timeout somewhere around claim ninety.

So: submit returns immediately with a job id, a bounded pool works through the claims,
and the caller polls. Partial failure is a first-class outcome -- one malformed claim
fails that claim and the other 3,999 still complete, with the failure recorded against
its own index rather than collapsing the batch.

**In-process, single-node, and not durable.** A restart loses in-flight jobs. That is a
deliberate stopping point rather than an oversight: the durable version is Celery or
arq with Redis, it is a deployment decision rather than a code one, and building half of
it here would produce something that looks durable and is not. `ARCHITECTURE.md` says
what replacing it involves. What this *does* give correctly is bounded concurrency,
per-claim isolation, tenant scoping, and cancellation.
"""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from claimiq.config import settings
from claimiq.observability import METRICS, log
from claimiq.state import AuditResult, ClaimPacket


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass
class ClaimOutcome:
    claim_id: str
    ok: bool
    verdict: str = ""
    settlement: str = ""
    patient_liability: str = ""
    hospital_writeoff: str = ""
    coverage: str = ""
    findings: int = 0
    error: str = ""

    @classmethod
    def succeeded(cls, result: AuditResult) -> ClaimOutcome:
        typical = result.typical
        return cls(
            claim_id=result.claim_id,
            ok=True,
            verdict=result.verdict,
            settlement=str(typical.projected_settlement),
            patient_liability=str(typical.patient_liability),
            hospital_writeoff=str(typical.hospital_writeoff),
            coverage=str(result.coverage.quantize(Decimal("0.001"))),
            findings=len(result.all_findings),
        )


@dataclass
class Job:
    job_id: str
    tenant: str
    key_id: str
    total: int
    submitted_at: str
    state: JobState = JobState.QUEUED
    completed: int = 0
    failed: int = 0
    outcomes: list[ClaimOutcome] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def summary(self, include_outcomes: bool = True) -> dict:
        """Money as strings, exactly as the single-claim API does."""
        body = {
            "job_id": self.job_id,
            "tenant": self.tenant,
            "state": self.state.value,
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "progress": round(self.completed / self.total, 4) if self.total else 1.0,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at or None,
            "finished_at": self.finished_at or None,
        }
        if include_outcomes:
            body["outcomes"] = [o.__dict__ for o in self.outcomes]
        return body

    def totals(self) -> dict:
        """Portfolio arithmetic over whatever has finished so far.

        Decimal, summed from the strings. Round-tripping through float here would undo
        the exactness the entire engine is built to preserve, at the one point where
        the numbers are largest and a reader is most likely to trust them.
        """
        ok = [o for o in self.outcomes if o.ok]
        add = lambda attr: sum((Decimal(getattr(o, attr) or "0") for o in ok), Decimal("0"))  # noqa: E731
        return {
            "claims": len(ok),
            "settlement": str(add("settlement")),
            "patient_liability": str(add("patient_liability")),
            "hospital_writeoff": str(add("hospital_writeoff")),
            "needs_attention": sum(1 for o in ok if o.verdict == "NEEDS_ATTENTION"),
            "cannot_verify": sum(1 for o in ok if o.verdict == "CANNOT_VERIFY"),
            "clean": sum(1 for o in ok if o.verdict == "CLEAN"),
        }


class JobRegistry:
    """Jobs in memory, with a bounded pool doing the work.

    `max_jobs` is a ring, not a leak-forever dict: a long-lived process that audits all
    day would otherwise hold every result it ever produced. Completed jobs age out
    oldest-first once the cap is reached; the durable copy of anything that matters is
    in the ledger and the claims database, not here.
    """

    def __init__(self, workers: int | None = None, max_jobs: int = 200) -> None:
        self.max_jobs = max_jobs
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, workers or settings().batch_workers),
            thread_name_prefix="claimiq-batch",
        )

    # --- submission --------------------------------------------------------

    def submit(self, packets: list[ClaimPacket], tenant: str, key_id: str) -> Job:
        job = Job(
            job_id=uuid.uuid4().hex[:16],
            tenant=tenant,
            key_id=key_id,
            total=len(packets),
            submitted_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        with self._lock:
            self._evict_locked()
            self._jobs[job.job_id] = job

        METRICS.inc("claimiq_batch_submitted_total", {"tenant": tenant})
        METRICS.gauge("claimiq_batch_queue_depth", self.active_count())

        # Snapshot the caller's context and run the batch inside a copy of it. Without
        # this the worker thread inherits whatever context the pool happened to keep
        # from a previous job, and the per-request contextvars (request id, trace run)
        # would carry across unrelated batches.
        context = contextvars.copy_context()
        self._pool.submit(context.run, self._run, job, packets)
        return job

    def _evict_locked(self) -> None:
        if len(self._jobs) < self.max_jobs:
            return
        finished = [j for j in self._jobs.values() if j.state in (JobState.DONE, JobState.CANCELLED)]
        for job in sorted(finished, key=lambda j: j.finished_at)[: len(self._jobs) - self.max_jobs + 1]:
            self._jobs.pop(job.job_id, None)

    # --- execution ---------------------------------------------------------

    def _run(self, job: Job, packets: list[ClaimPacket]) -> None:
        from claimiq.graph import audit  # deferred: graph imports jobs-free modules only

        job.state = JobState.RUNNING
        job.started_at = datetime.now(UTC).isoformat(timespec="seconds")
        started = time.perf_counter()

        for packet in packets:
            if job.cancelled:
                break
            try:
                # Each claim gets its own context, so its trace and LLM call ledger are
                # its own -- the same isolation a single HTTP request gets. Auditing in
                # a bare loop would append every claim's nodes to the first claim's trace.
                result = contextvars.copy_context().run(audit, packet)
                job.outcomes.append(ClaimOutcome.succeeded(result))
                METRICS.inc("claimiq_audits_total", {"result": result.verdict, "mode": "batch"})
            except Exception as exc:  # noqa: BLE001 - one bad claim must not stop the batch
                job.failed += 1
                job.outcomes.append(
                    ClaimOutcome(
                        claim_id=packet.claim_id, ok=False, error=f"{type(exc).__name__}: {exc}"
                    )
                )
                METRICS.inc("claimiq_audits_total", {"result": "error", "mode": "batch"})
                log("batch.claim_failed", job_id=job.job_id, claim_id=packet.claim_id, error=str(exc))
            finally:
                job.completed += 1

        job.state = JobState.CANCELLED if job.cancelled else JobState.DONE
        job.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        METRICS.observe("claimiq_batch_duration_seconds", time.perf_counter() - started)
        METRICS.gauge("claimiq_batch_queue_depth", self.active_count())
        log(
            "batch.finished",
            job_id=job.job_id,
            tenant=job.tenant,
            total=job.total,
            failed=job.failed,
            seconds=round(time.perf_counter() - started, 2),
        )

    # --- reads -------------------------------------------------------------

    def get(self, job_id: str, tenant: str | None = None) -> Job | None:
        job = self._jobs.get(job_id)
        # Tenant mismatch returns None rather than 403 on purpose: a 403 confirms the
        # job id exists, which is a small enumeration oracle across tenants. From the
        # caller's side an unknown id and someone else's id look identical.
        if job is None or (tenant is not None and job.tenant != tenant):
            return None
        return job

    def list(self, tenant: str | None = None, limit: int = 50) -> list[dict]:
        jobs = [j for j in self._jobs.values() if tenant is None or j.tenant == tenant]
        jobs.sort(key=lambda j: j.submitted_at, reverse=True)
        return [j.summary(include_outcomes=False) for j in jobs[:limit]]

    def cancel(self, job_id: str, tenant: str | None = None) -> bool:
        job = self.get(job_id, tenant)
        if job is None or job.state in (JobState.DONE, JobState.CANCELLED):
            return False
        job._cancel.set()
        return True

    def active_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state in (JobState.QUEUED, JobState.RUNNING))

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


@lru_cache(maxsize=1)
def registry() -> JobRegistry:
    return JobRegistry()
