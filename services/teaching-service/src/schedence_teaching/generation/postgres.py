"""Tenant-bound PostgreSQL queue with transactional acceptance and fenced saves.

Execution is currently restricted to disposable localhost PostgreSQL. No startup
migrations, production credentials fallback or master-data writes are supported.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from schedence_teaching.api.errors import ApiError
from schedence_teaching.persistence.application import verify_runtime_security
from schedence_teaching.persistence.database_read import inspect_owner_security
from schedence_teaching.persistence.lifecycle import academic_limitations
from schedence_teaching.persistence.repositories import teaching_unit_of_work
from schedence_teaching.persistence.snapshots import canonical_json, content_digest
from schedence_teaching.persistence.timetable_service import persist_in_transaction, timetable_reader

from .contracts import GenerationJobOut, GenerationRequest, JobError
from .db_models import GenerationJob as Job, GenerationJobAttempt as Attempt
from .persistence import IsolatedDraftPersistence


@dataclass(frozen=True)
class JobClaim:
    job_id: object
    institution_id: object
    token: object
    attempt_number: int
    request: GenerationRequest
    snapshot_id: object
    snapshot_sha256: str
    actor: object
    deadline_at: object


def project(job, clock):
    summary = job.summary
    return GenerationJobOut(job_id=job.id, institution_id=job.institution_id, status=job.state,
        durability='POSTGRESQL_DURABLE', request=GenerationRequest.model_validate(job.request_payload),
        created_at=job.created_at, started_at=job.started_at, finished_at=job.finished_at,
        elapsed_seconds=max(0, ((job.finished_at or clock)-(job.started_at or clock)).total_seconds()),
        solver_status=job.solver_status, solver_runtime_seconds=job.solver_runtime_seconds,
        validation_status=job.validation_status, expected_occurrences=job.expected_occurrences,
        scheduled_occurrences=job.scheduled_occurrences, timetable_version_id=job.timetable_version_id,
        result_snapshot_id=job.work_snapshot_id, persistence='ISOLATED_POSTGRESQL',
        scope_completion='ALL_SELECTED_KNOWN_OCCURRENCES_VALIDATED' if job.state=='COMPLETED' else 'NOT_COMPLETE',
        calendar_label=summary['calendar_label'], unresolved_requirement_codes=tuple(summary['issue_codes']),
        blocked_student_count=summary['blocked_student_count'], attempt_number=job.attempt_number,
        max_attempts=job.max_attempts, last_heartbeat_at=job.last_heartbeat_at,
        error=JobError(category=job.safe_error_category, message=job.safe_error_message,
            retry_with_new_key=job.retry_with_new_key) if job.safe_error_category else None)


def key_digest(key):
    return sha256(key.encode('utf-8')).hexdigest()


class PostgresJobRepository:
    def __init__(self, url, *, institution_limit=2, running_limit=1, lease_seconds=30,
                 max_attempts=2, retry_backoff_seconds=1):
        if (min(institution_limit,running_limit,lease_seconds,max_attempts)<=0 or running_limit>institution_limit
                or max_attempts>5 or retry_backoff_seconds<0):
            raise ValueError('invalid durable job resource limits')
        self.engine = IsolatedDraftPersistence(url)._engine()  # fail closed before connection
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.institution_limit, self.running_limit = institution_limit,running_limit
        self.lease_seconds, self.max_attempts = lease_seconds,max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    @asynccontextmanager
    async def transaction(self, tenant, *, read=False):
        facade = timetable_reader if read else teaching_unit_of_work
        async with facade(self.factory, tenant) as repo:
            session = repo._session
            if not read:
                await verify_runtime_security(session)
            if (await session.scalar(text('SELECT current_database()'))!='schedence_step12b4_test' or
                    await session.scalar(text("SELECT current_setting('neon.project_id',true)")) is not None):
                raise ValueError('durable job execution is isolated-only in this step')
            _, tables = await inspect_owner_security(session,'teaching')
            for name in ('generation_jobs','generation_job_attempts'):
                table=next((row for row in tables if row['relname']==name),None)
                if (table is None or not table['can_select'] or table['owns_table'] or
                    not table['relrowsecurity'] or not table['relforcerowsecurity'] or table['policy_count']!=1):
                    raise ValueError('durable queue requires restricted runtime privileges and forced institution RLS')
                predicate="".join((table['using_expression'] or '').split()).replace('(','').replace(')','')
                expected="institution_id=NULLIFcurrent_setting'schedence.institution_id'::text,true,''::text::uuid"
                if predicate!=expected or table['check_expression']!=table['using_expression']:
                    raise ValueError('durable queue RLS policy differs from reviewed isolation')
            yield repo

    async def _clock(self, session):
        return await session.scalar(select(func.clock_timestamp()))

    async def _quota_lock(self, session, tenant):
        identity=int.from_bytes(sha256(tenant.bytes+b':generation-quota').digest()[:8],'big',signed=True)
        await session.execute(select(func.pg_advisory_xact_lock(identity)))

    async def _job(self, session, tenant, identifier, *, locked=False):
        query=select(Job).where(Job.institution_id==tenant,Job.id==identifier)
        row=await session.scalar(query.with_for_update() if locked else query)
        if row is None:raise ApiError(404,'JOB_NOT_FOUND','The generation job was not found in this institution.')
        return row

    async def get(self, tenant, identifier):
        async with self.transaction(tenant,read=True) as repo:
            return project(await self._job(repo._session,tenant,identifier),await self._clock(repo._session))

    async def lookup(self, request, key):
        async with self.transaction(request.institution_id,read=True) as repo:
            job=await repo._session.scalar(select(Job).where(Job.institution_id==request.institution_id,
                Job.idempotency_sha256==key_digest(key)))
            if job is None:return None
            self._same_payload(job,request)
            return project(job,await self._clock(repo._session))

    def _same_payload(self, job, request):
        if job.request_sha256!=content_digest(request):
            raise ApiError(409,'IDEMPOTENCY_CONFLICT','This key already belongs to a different generation request.')

    async def enqueue(self, work, key, actor):
        request,tenant=work.request,work.request.institution_id
        async with self.transaction(tenant) as repo:
            session=repo._session
            await self._quota_lock(session,tenant)
            job=await session.scalar(select(Job).where(Job.institution_id==tenant,Job.idempotency_sha256==key_digest(key)))
            if job is not None:
                self._same_payload(job,request)
                return project(job,await self._clock(session))
            active=(await session.scalars(select(Job).where(Job.institution_id==tenant,Job.state.in_(('PENDING','RUNNING'))))).all()
            digest=content_digest(request)
            if any(row.request_sha256==digest for row in active):
                raise ApiError(409,'DUPLICATE_ACTIVE_GENERATION','An identical generation is already active.')
            if len(active)>=self.institution_limit:
                raise ApiError(409,'INSTITUTION_JOB_LIMIT','The institution has reached its active generation limit.')
            if request.timetable_series_id is not None:
                latest=await repo.get_latest_draft(request.timetable_series_id)
                if (latest.version['id'] if latest else None)!=request.expected_previous_version_id:
                    raise ApiError(409,'STALE_VERSION','The selected timetable history has changed.')
            snapshot=await repo.store_snapshot(work.document)
            limits=academic_limitations(work.document);clock=await self._clock(session)
            job=Job(id=uuid4(),institution_id=tenant,requested_by_identity_user_id=actor,
                source_snapshot_reference=request.snapshot_id,work_snapshot_id=snapshot,work_sha256=content_digest(work.document),
                idempotency_sha256=key_digest(key),request_sha256=digest,request_payload=json.loads(canonical_json(request)),
                summary=dict(calendar_label=work.document.calendar_label,issue_codes=sorted({row.issue_code for row in work.document.issues}),
                    blocked_student_count=sum(row.get('student_count') or 0 for row in limits['blocked_scopes'])),
                state='PENDING',created_at=clock,next_attempt_at=clock,attempt_number=0,max_attempts=self.max_attempts,
                timeout_seconds=request.job_timeout_seconds,
                expected_occurrences=sum(row.sessions_per_week for row in work.document.teaching_input.session_requirements),
                scheduled_occurrences=0,validation_status='NOT_ASSESSED',retry_with_new_key=False)
            session.add(job);await session.flush()
            return project(job,clock) # UoW commits before this returns to the API.

    def _clear_lease(self, job):
        job.lease_token=job.lease_expires_at=job.deadline_at=None

    async def _attempt(self, session, job):
        return await session.scalar(select(Attempt).where(Attempt.institution_id==job.institution_id,
            Attempt.job_id==job.id,Attempt.attempt_number==job.attempt_number))

    async def _fail_locked(self, session, job, clock, category, *, retryable, report=None):
        attempt=await self._attempt(session,job)
        attempt.finished_at=clock;attempt.outcome=category;attempt.error_category=category
        attempt.report=report or dict(category=category,attempt_number=job.attempt_number)
        retry=retryable and job.attempt_number<job.max_attempts
        job.state='PENDING' if retry else 'FAILED'
        job.finished_at=None if retry else clock
        job.next_attempt_at=clock+timedelta(seconds=self.retry_backoff_seconds*job.attempt_number)
        job.safe_error_category=category
        job.safe_error_message='Execution will retry after recovery.' if retry else 'Generation failed; private attempt diagnostics were retained.'
        job.retry_with_new_key=not retry and (retryable or category in {'JOB_TIMEOUT','SOLVER_UNKNOWN'})
        self._clear_lease(job)

    async def claim(self, tenant, worker_id):
        async with self.transaction(tenant) as repo:
            session=repo._session
            await self._quota_lock(session,tenant)
            clock=await self._clock(session)
            expired=(await session.scalars(select(Job).where(Job.institution_id==tenant,Job.state=='RUNNING',
                Job.lease_expires_at<=clock).with_for_update(skip_locked=True))).all()
            for job in expired:
                timed_out=job.deadline_at<=clock
                await self._fail_locked(session,job,clock,'JOB_TIMEOUT' if timed_out else 'LEASE_EXPIRED',retryable=not timed_out)
            await session.flush()
            running=await session.scalar(select(func.count()).select_from(Job).where(Job.institution_id==tenant,Job.state=='RUNNING'))
            if running>=self.running_limit:return None
            job=await session.scalar(select(Job).where(Job.institution_id==tenant,Job.state=='PENDING',Job.next_attempt_at<=clock)
                .order_by(Job.created_at,Job.id).with_for_update(skip_locked=True).limit(1))
            if job is None:return None
            token=uuid4();job.state='RUNNING';job.attempt_number+=1
            job.started_at=job.started_at or clock
            job.last_heartbeat_at=clock
            job.deadline_at=clock+timedelta(seconds=job.timeout_seconds)
            job.lease_expires_at=min(clock+timedelta(seconds=self.lease_seconds),job.deadline_at)
            job.lease_token=token;job.safe_error_category=job.safe_error_message=None;job.retry_with_new_key=False
            session.add(Attempt(id=uuid4(),institution_id=tenant,job_id=job.id,attempt_number=job.attempt_number,
                lease_token=token,worker_id=worker_id,started_at=clock,outcome='RUNNING',report={}))
            await session.flush()
            return JobClaim(job.id,tenant,token,job.attempt_number,GenerationRequest.model_validate(job.request_payload),
                job.work_snapshot_id,job.work_sha256,job.requested_by_identity_user_id,job.deadline_at)

    async def heartbeat(self, claim):
        async with self.transaction(claim.institution_id) as repo:
            session=repo._session;job=await self._job(session,claim.institution_id,claim.job_id,locked=True)
            clock=await self._clock(session)
            if (job.state!='RUNNING' or job.lease_token!=claim.token or job.lease_expires_at<=clock or job.deadline_at<=clock):return False
            job.last_heartbeat_at=clock
            job.lease_expires_at=min(clock+timedelta(seconds=self.lease_seconds),job.deadline_at)
            return True

    async def get_work_snapshot(self, claim):
        async with self.transaction(claim.institution_id,read=True) as repo:
            document=await repo.get_snapshot(claim.snapshot_id)
            if content_digest(document)!=claim.snapshot_sha256:
                raise ValueError('accepted work snapshot integrity check failed')
            return document

    async def fail(self, claim, category, *, retryable=False, report=None, result=None, validation_failed=False):
        async with self.transaction(claim.institution_id) as repo:
            session=repo._session;job=await self._job(session,claim.institution_id,claim.job_id,locked=True)
            if job.state!='RUNNING' or job.lease_token!=claim.token:return False
            if result is not None:
                job.solver_status=result.status.value;job.solver_runtime_seconds=result.runtime_seconds
            if validation_failed:job.validation_status='FAILED'
            await self._fail_locked(session,job,await self._clock(session),category,retryable=retryable,report=report)
            return True

    async def complete(self, claim, result):
        async with self.transaction(claim.institution_id) as repo:
            session=repo._session;job=await self._job(session,claim.institution_id,claim.job_id,locked=True)
            clock=await self._clock(session)
            if job.state!='RUNNING' or job.lease_token!=claim.token or job.lease_expires_at<=clock or job.deadline_at<=clock:
                return False
            request=claim.request
            series=request.timetable_series_id or await repo.create_series(f'DURABLE_GENERATION:{job.id}')
            stored=await persist_in_transaction(repo,series,job.work_snapshot_id,result,
                expected_previous_version_id=request.expected_previous_version_id,created_by_identity_user_id=job.requested_by_identity_user_id)
            # Recheck DB time after independent validation, before atomic commit.
            clock=await self._clock(session)
            if job.lease_expires_at<=clock or job.deadline_at<=clock:
                raise LeaseLost('the lease expired during finalization')
            attempt=await self._attempt(session,job)
            attempt.finished_at=clock;attempt.outcome='COMPLETED';attempt.report=result.to_dict()
            job.state='COMPLETED';job.finished_at=clock;job.solver_status=result.status.value
            job.solver_runtime_seconds=result.runtime_seconds;job.validation_status='PASSED'
            job.scheduled_occurrences=job.expected_occurrences;job.timetable_version_id=stored.version_id;job.result_run_id=stored.run_id
            job.safe_error_category=job.safe_error_message=None;self._clear_lease(job)
            return True

    async def close(self):
        await self.engine.dispose()


class LeaseLost(Exception):
    pass
