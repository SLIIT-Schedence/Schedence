"""Single-process development job registry. NOT a durable production queue.

    Atomicity holds within one event loop/application process only. IDs and
    idempotency reservations expire on process loss; never deploy with multiple
    API workers/replicas. Reports on disk are not a recoverable job database.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from time import monotonic
from uuid import uuid4

from schedence_teaching.api.errors import ApiError
from schedence_teaching.persistence.lifecycle import academic_limitations
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.snapshots import content_digest, prepare_attempt

from .contracts import GenerationJobOut, JobError
from .persistence import NoDraftPersistence
from .worker import WorkerFailure


def now():
    return datetime.now(timezone.utc)


@dataclass
class _Job:
    public: GenerationJobOut
    digest: str
    work: object
    actor: object
    task: object = None
    started_clock: float | None = None
    persisting: bool = False


class DevelopmentGenerationJobs:
    durability = "DEVELOPMENT_NON_DURABLE"

    def __init__(self, inputs, executor, *, enabled=False, persistence=None,
                 per_institution_limit=2, worker_limit=1, retained_job_limit=1000,
                 max_timeout_seconds=600, preflight_timeout_seconds=90):
        if min(per_institution_limit, worker_limit, retained_job_limit) < 1 or max_timeout_seconds <= 0 or preflight_timeout_seconds <= 0:
            raise ValueError("job resource limits must be positive")
        self.inputs, self.executor, self.enabled = inputs, executor, enabled
        self.persistence = persistence or NoDraftPersistence()
        self.per_institution_limit, self.retained_job_limit = per_institution_limit, retained_job_limit
        self.max_timeout_seconds, self.preflight_timeout_seconds = max_timeout_seconds, preflight_timeout_seconds
        self._jobs, self._keys = {}, {}
        self._lock = asyncio.Lock()
        self._workers = asyncio.Semaphore(worker_limit)
        self._closed = False

    def _public(self, job):
        values = job.public.model_dump()
        if job.started_clock is not None and job.public.finished_at is None:
            values["elapsed_seconds"] = monotonic() - job.started_clock
        return GenerationJobOut.model_validate(values)

    async def submit(self, request, key, actor):
        if not self.enabled or self._closed:
            raise ApiError(503, "GENERATION_DISABLED", "No generation worker is enabled in this deployment.")
        if key is None or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key) is None:
            raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Supply an Idempotency-Key of 1–128 safe characters.")
        if request.job_timeout_seconds > self.max_timeout_seconds:
            raise ApiError(400, "TIMEOUT_LIMIT", "The requested wall timeout exceeds the deployment limit.")
        digest = content_digest(request)
        # Serialize preparation as well as enqueueing. Slow I/O/CPU preparation
        # yields the event loop; polling and unrelated HTTP requests stay live.
        async with self._lock:
            if self._closed:
                raise ApiError(503, "GENERATION_DISABLED", "The generation worker is shutting down.")
            identity = request.institution_id, key
            previous = self._keys.get(identity)
            if previous is not None:
                job = self._jobs[previous]
                if job.digest != digest:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This idempotency key was used for a different request.")
                return self._public(job)
            active = [job for job in self._jobs.values() if job.public.institution_id == request.institution_id
                      and job.public.status in {"PENDING", "RUNNING"}]
            if any(job.digest == digest for job in active):
                raise ApiError(409, "DUPLICATE_ACTIVE_GENERATION", "An identical generation is already pending or running.")
            if len(active) >= self.per_institution_limit:
                raise ApiError(409, "INSTITUTION_JOB_LIMIT", "This institution has reached its active generation limit.")
            if len(self._jobs) >= self.retained_job_limit:
                raise ApiError(503, "JOB_REGISTRY_FULL", "The development job registry is full; no request was queued.")
            try:
                async with asyncio.timeout(self.preflight_timeout_seconds):
                    work = await self.inputs.prepare(request)
                    await self.persistence.preflight(request)
            except ApiError:
                raise
            except PersistenceConflict:
                raise ApiError(400, "INVALID_GENERATION_INPUTS", "The snapshot or selected scope is unavailable or inconsistent.") from None
            except TimeoutError:
                raise ApiError(503, "INPUTS_UNAVAILABLE", "Input preparation timed out; no job was queued.") from None
            except Exception:
                raise ApiError(503, "INPUTS_UNAVAILABLE", "Validated generation inputs are unavailable; no job was queued.") from None
            if work.request != request or work.document.institution_id != request.institution_id:
                raise ApiError(503, "INPUTS_UNAVAILABLE", "The trusted input provider returned an inconsistent scope.")
            if self._closed:
                raise ApiError(503, "GENERATION_DISABLED", "The generation worker is shutting down; no job was queued.")
            limits = academic_limitations(work.document)
            public = GenerationJobOut(job_id=uuid4(), institution_id=request.institution_id, status="PENDING", request=request,
                created_at=now(), elapsed_seconds=0, persistence=self.persistence.mode,
                expected_occurrences=sum(row.sessions_per_week for row in work.document.teaching_input.session_requirements),
                calendar_label=work.document.calendar_label,
                unresolved_requirement_codes=tuple(sorted({row.issue_code for row in work.document.issues})),
                blocked_student_count=sum(row.get("student_count") or 0 for row in limits["blocked_scopes"]))
            job = _Job(public, digest, work, actor)
            # create_task must actually accept execution before publishing the ID.
            task = asyncio.create_task(self._execute(job), name=f"teaching-generation:{public.job_id}")
            job.task = task
            self._jobs[public.job_id] = job
            self._keys[identity] = public.job_id
            return self._public(job)

    async def get(self, institution_id, job_id):
        job = self._jobs.get(job_id)
        if job is None or job.public.institution_id != institution_id:
            raise ApiError(404, "JOB_NOT_FOUND", "The generation job was not found in this institution.")
        return self._public(job)

    def _fail(self, job, category, message, *, retry=True):
        job.public.status = "FAILED"
        job.public.error = JobError(category=category, message=message, retry_with_new_key=retry)

    async def _execute(self, job):
        try:
            async with self._workers:
                job.public.status = "RUNNING"
                job.public.started_at = now()
                job.started_clock = monotonic()
                try:
                    outcome = await asyncio.wait_for(self.executor.run(job.work, job.public.job_id),
                        timeout=job.work.request.job_timeout_seconds)
                except TimeoutError:
                    self._fail(job, "JOB_TIMEOUT", "The isolated worker exceeded its wall timeout.")
                    return
                result = outcome.result
                job.public.solver_status = result.status.value
                job.public.solver_runtime_seconds = result.runtime_seconds
                try:
                    # Recompute in the parent. Never trust worker-attached validation.
                    attempt = await asyncio.to_thread(prepare_attempt, job.work.document, result)
                except Exception:
                    job.public.validation_status = "FAILED"
                    self._fail(job, "VALIDATION_FAILED", "Independent validation or exact occurrence coverage failed.", retry=False)
                    return
                if not attempt.success:
                    self._fail(job, "SOLVER_" + result.status.value,
                        "The solver did not produce a complete validated timetable. Private diagnostics were retained.")
                    return
                job.public.validation_status = "PASSED"
                job.public.scheduled_occurrences = attempt.scheduled_occurrences
                job.public.scope_completion = "ALL_SELECTED_KNOWN_OCCURRENCES_VALIDATED"
                job.persisting = True
                version, snapshot = await self.persistence.save(job.work, result, job.public.job_id, job.actor)
                job.public.timetable_version_id = version
                job.public.result_snapshot_id = snapshot
                job.public.status = "COMPLETED"
        except asyncio.CancelledError:
            self._fail(job, "WORKER_INTERRUPTED", "Development execution was interrupted; no automatic retry was performed.")
        except PersistenceConflict:
            self._fail(job, "PERSISTENCE_CONFLICT", "Draft history changed or persistence rejected the result.", retry=False)
        except WorkerFailure:
            self._fail(job, "WORKER_FAILED", "The isolated worker exited without a usable result.")
        except Exception:
            self._fail(job, "GENERATION_FAILED", "Generation or isolated persistence failed. Consult private diagnostics.")
        finally:
            job.public.finished_at = now()
            job.public.elapsed_seconds = monotonic() - job.started_clock if job.started_clock is not None else 0
            job.work = None  # Retain safe status/idempotency, not full owner rows.

    async def close(self):
        self._closed = True
        # Finish an already-started transaction; cancel only computation/queue waits.
        tasks = [job.task for job in self._jobs.values() if not job.task.done()]
        for job in self._jobs.values():
            if not job.task.done() and not job.persisting:
                job.task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Tasks cancelled before their coroutine started have no finally block.
        for job in self._jobs.values():
            if job.public.status in {"PENDING", "RUNNING"}:
                self._fail(job, "WORKER_INTERRUPTED", "The development application shut down before completion.")
                job.public.finished_at = now()
                job.work = None
