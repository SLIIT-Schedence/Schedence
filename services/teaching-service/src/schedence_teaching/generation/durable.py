"""Durable API facade and independently restartable PostgreSQL worker loop."""

import asyncio
from datetime import datetime, timezone
import re
from uuid import uuid4

from schedence_teaching.api.errors import ApiError
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.snapshots import content_digest, prepare_attempt

from .postgres import LeaseLost
from .worker import WorkerFailure


class DurableWorker:
    def __init__(self, repository, inputs, executor):
        self.repository,self.inputs,self.executor=repository,inputs,executor
        self.worker_id=uuid4()

    async def _heartbeat(self, claim, lost):
        try:
            while True:
                await asyncio.sleep(self.repository.lease_seconds/3)
                if not await self.repository.heartbeat(claim):
                    lost.set();return
        except asyncio.CancelledError:
            raise
        except Exception:
            lost.set() # stop computation if ownership can no longer be verified

    async def run_once(self, tenant):
        claim=await self.repository.claim(tenant,self.worker_id)
        if claim is None:return False
        lost=asyncio.Event();heartbeat=asyncio.create_task(self._heartbeat(claim,lost))
        computation=None;lost_waiter=None
        async def calculate():
            accepted=await self.repository.get_work_snapshot(claim)
            work=await self.inputs.prepare(claim.request)
            if (work.request!=claim.request or work.document.institution_id!=tenant or
                    content_digest(work.document)!=content_digest(accepted)):
                raise InputsChanged('database inputs differ from accepted immutable work')
            outcome=await self.executor.run(work,claim.token) # unique directory per attempt
            try:
                assessment=await asyncio.to_thread(prepare_attempt,accepted,outcome.result)
            except Exception:
                raise InvalidResult(outcome.result) from None
            return outcome.result,assessment
        try:
            remaining=max(0,(claim.deadline_at-datetime.now(timezone.utc)).total_seconds())
            async with asyncio.timeout(remaining):
                computation=asyncio.create_task(calculate())
                lost_waiter=asyncio.create_task(lost.wait())
                ready,_=await asyncio.wait((computation,lost_waiter),return_when=asyncio.FIRST_COMPLETED)
                if lost_waiter in ready:
                    raise LeaseLost('the worker lease could not be retained')
                result,assessment=await computation
            if not assessment.success:
                await self.repository.fail(claim,'SOLVER_'+result.status.value,result=result,
                    report=dict(solver_result=result.to_dict(),validation='NOT_ASSESSED'))
            else:
                # Job status and validated immutable draft commit together.
                finalization=asyncio.create_task(self.repository.complete(claim,result))
                try:
                    await asyncio.shield(finalization)
                except asyncio.CancelledError:
                    await finalization
                    raise
        except asyncio.CancelledError:
            if computation is not None and not computation.done():
                computation.cancel();await asyncio.gather(computation,return_exceptions=True)
            await self.repository.fail(claim,'WORKER_INTERRUPTED',retryable=True)
            raise
        except TimeoutError:
            await self.repository.fail(claim,'JOB_TIMEOUT',report=dict(category='JOB_TIMEOUT',timeout_seconds=claim.request.job_timeout_seconds))
        except InvalidResult as error:
            await self.repository.fail(claim,'VALIDATION_FAILED',validation_failed=True,result=error.result,
                report=dict(validation='FAILED',solver_result=error.result.to_dict()))
        except InputsChanged:
            await self.repository.fail(claim,'INPUTS_CHANGED',report=dict(category='INPUTS_CHANGED',accepted_snapshot_sha256=claim.snapshot_sha256))
        except LeaseLost:
            # A subsequent worker owns recovery; never finalize using stale authority.
            pass
        except PersistenceConflict:
            await self.repository.fail(claim,'PERSISTENCE_CONFLICT')
        except ApiError as error:
            await self.repository.fail(claim,'INPUTS_UNAVAILABLE',retryable=error.status==503,
                report=dict(category='INPUTS_UNAVAILABLE',source_error_code=error.code))
        except WorkerFailure:
            await self.repository.fail(claim,'WORKER_FAILED',retryable=True)
        except Exception as error:
            await self.repository.fail(claim,'WORKER_FAILED',retryable=True,
                report=dict(category='WORKER_FAILED',exception_type=type(error).__name__))
        finally:
            for task in (computation,lost_waiter,heartbeat):
                if task is not None and not task.done():task.cancel()
            await asyncio.gather(*(task for task in (computation,lost_waiter,heartbeat) if task is not None),return_exceptions=True)
        return True


class InputsChanged(Exception):
    pass


class InvalidResult(Exception):
    def __init__(self,result):self.result=result


class DurableGenerationJobs:
    durability='POSTGRESQL_DURABLE'

    def __init__(self, repository, inputs, executor, *, institutions, enabled=False,
                 max_timeout_seconds=600, preflight_timeout_seconds=90, worker_count=1, poll_seconds=.5):
        if not institutions or worker_count<1 or poll_seconds<=0:
            raise ValueError('durable workers require explicit institutions and positive limits')
        self.repository,self.inputs,self.executor=repository,inputs,executor
        self.institutions=frozenset(institutions);self.enabled=enabled
        self.max_timeout_seconds,self.preflight_timeout_seconds=max_timeout_seconds,preflight_timeout_seconds
        self.worker_count,self.poll_seconds=worker_count,poll_seconds
        self._tasks=[];self._closed=False

    def start(self):
        if self._closed or not self.enabled or self._tasks:return
        for _ in range(self.worker_count):
            self._tasks.append(asyncio.create_task(self._poll()))

    async def _poll(self):
        worker=DurableWorker(self.repository,self.inputs,self.executor)
        while not self._closed:
            worked=False
            for tenant in sorted(self.institutions,key=str):
                if self._closed:return
                try:
                    worked=await worker.run_once(tenant) or worked
                except asyncio.CancelledError:raise
                except Exception:
                    # Durable leases reconcile database outages on recovery.
                    pass
            if not worked:await asyncio.sleep(self.poll_seconds)

    async def submit(self, request, key, actor):
        if not self.enabled or self._closed or request.institution_id not in self.institutions:
            raise ApiError(503,'GENERATION_DISABLED','Durable generation is not enabled for this institution.')
        if key is None or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',key) is None:
            raise ApiError(400,'IDEMPOTENCY_KEY_REQUIRED','Supply an Idempotency-Key of 1–128 safe characters.')
        if request.job_timeout_seconds>self.max_timeout_seconds:
            raise ApiError(400,'TIMEOUT_LIMIT','The requested wall timeout exceeds the deployment limit.')
        previous=await self.repository.lookup(request,key)
        if previous is not None:
            self.start();return previous
        try:
            async with asyncio.timeout(self.preflight_timeout_seconds):
                work=await self.inputs.prepare(request)
            if work.request!=request or work.document.institution_id!=request.institution_id:
                raise ApiError(503,'INPUTS_UNAVAILABLE','The trusted input provider returned an inconsistent scope.')
            if self._closed:raise ApiError(503,'GENERATION_DISABLED','The worker is shutting down.')
            result=await self.repository.enqueue(work,key,actor)
        except ApiError:raise
        except PersistenceConflict:
            raise ApiError(400,'INVALID_GENERATION_INPUTS','The selected input scope is unavailable or inconsistent.') from None
        except Exception:
            raise ApiError(503,'INPUTS_UNAVAILABLE','Durable acceptance failed; no acceptance is claimed. Retry with the same key.') from None
        self.start()
        return result

    async def get(self, tenant, job_id):
        result=await self.repository.get(tenant,job_id)
        self.start()
        return result

    async def close(self):
        self._closed=True
        for task in self._tasks:task.cancel()
        if self._tasks:await asyncio.gather(*self._tasks,return_exceptions=True)
        await self.repository.close()
