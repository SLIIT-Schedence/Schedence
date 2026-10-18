"""Durability, fencing and API integration on disposable PostgreSQL only."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4, UUID

from fastapi.testclient import TestClient
import httpx
import pytest
from schedence_auth.testing import TokenFactory
from schedence_db.runtime import run
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import async_sessionmaker

from schedence_teaching.api.errors import ApiError
from schedence_teaching.api.identity import IdentityPermissionsClient
from schedence_teaching.api.runtime import TeachingRuntime
from schedence_teaching.api.store import DatabaseTimetableStore
from schedence_teaching.generation.db_models import GenerationJob, GenerationJobAttempt
from schedence_teaching.generation.durable import DurableGenerationJobs, DurableWorker
from schedence_teaching.generation.inputs import GenerationWork
from schedence_teaching.generation.postgres import PostgresJobRepository
from schedence_teaching.generation.worker import ProcessGenerationExecutor, WorkerFailure
from schedence_teaching.main import create_app
from schedence_teaching.persistence.snapshots import content_digest
from schedence_teaching.solver import SolverResult, SolverStatus

from test_generation_jobs import Inputs, Executor, request_for
from test_persistence_contracts import TENANT, OTHER_TENANT, example
from test_timetable_persistence_isolated import isolated_engine

pytestmark=pytest.mark.skipif(os.environ.get('SCHEDENCE_TEACHING_ISOLATED_DB_TESTS')!='1',
    reason='durable job writes require the dedicated disposable localhost PostgreSQL database')


@pytest.fixture(scope='module',autouse=True)
def job_schema():
    async def apply():
        admin=isolated_engine(admin=True);runtime=isolated_engine()
        try:
            async with admin.begin() as connection:
                assert await connection.scalar(text('SELECT current_database()'))=='schedence_step12b4_test'
                assert await connection.scalar(text("SELECT current_setting('neon.project_id',true)")) is None
                exists=await connection.scalar(text("SELECT to_regclass('teaching.generation_jobs')"))
                if exists is None:
                    path=Path(__file__).resolve().parents[1]/'migrations/versions/20261016_0002_generation_jobs.sql'
                    for statement in path.read_text(encoding='utf-8').split(';\n'):
                        if statement.strip():await connection.execute(text(statement.strip()))
                role=admin.dialect.identifier_preparer.quote(runtime.url.username)
                await connection.execute(text(f'GRANT SELECT, INSERT, UPDATE ON teaching.generation_jobs, teaching.generation_job_attempts TO {role}'))
        finally:await admin.dispose();await runtime.dispose()
    run(apply())


class SnapshotInputs:
    """Synthetic isolated SQL fixtures only; real runtime uses owner-data builder."""
    def __init__(self,repository):self.repository=repository
    async def prepare(self,request):
        async with self.repository.transaction(request.institution_id,read=True) as repo:
            document=await repo.get_snapshot(request.snapshot_id)
        return GenerationWork(request,document)


async def seeded(**options):
    engine=isolated_engine();url=engine.url;await engine.dispose()
    repository=PostgresJobRepository(url,retry_backoff_seconds=0,**options)
    document,result=example();document.institution_id=uuid4() # independent quota/queue per test
    async with repository.transaction(document.institution_id) as repo:
        snapshot=await repo.store_snapshot(document)
    request=request_for(document,snapshot_id=snapshot)
    return repository,GenerationWork(request,document),result


async def attempts(repository,tenant,job_id):
    async with repository.transaction(tenant,read=True) as repo:
        return (await repo._session.scalars(select(GenerationJobAttempt).where(
            GenerationJobAttempt.institution_id==tenant,GenerationJobAttempt.job_id==job_id)
            .order_by(GenerationJobAttempt.attempt_number))).all()


def test_persisted_acceptance_survives_repository_restart_and_idempotency():
    async def execute():
        store,work,result=await seeded()
        first=await store.enqueue(work,'durable-1',uuid4());url=store.engine.url
        assert first.status=='PENDING' and first.durability=='POSTGRESQL_DURABLE'
        await store.close();second=PostgresJobRepository(url)
        try:
            saved=await second.get(work.request.institution_id,first.job_id)
            assert saved.job_id==first.job_id and saved.request==work.request
            assert (await second.lookup(work.request,'durable-1')).job_id==first.job_id
            changed=work.request.model_copy(update={'max_time_seconds':.5})
            with pytest.raises(ApiError) as error:await second.lookup(changed,'durable-1')
            assert error.value.status==409
        finally:await second.close()
    run(execute())


def test_atomic_duplicate_requests_and_institution_quota_across_connections():
    async def execute():
        store,work,_=await seeded()
        try:
            accepted=await asyncio.gather(*(store.enqueue(work,'same',uuid4()) for _ in range(8)))
            assert len({row.job_id for row in accepted})==1
            with pytest.raises(ApiError) as error:await store.enqueue(work,'another-key',uuid4())
            assert error.value.code=='DUPLICATE_ACTIVE_GENERATION'
            changed=replace(work,request=work.request.model_copy(update={'max_time_seconds':.5}))
            await store.enqueue(changed,'second-payload',uuid4())
            third=replace(work,request=work.request.model_copy(update={'max_time_seconds':.4}))
            with pytest.raises(ApiError) as error:await store.enqueue(third,'third',uuid4())
            assert error.value.code=='INSTITUTION_JOB_LIMIT'
        finally:await store.close()
    run(execute())


def test_concurrent_workers_claim_once_and_running_limit_is_atomic():
    async def execute():
        store,work,result=await seeded()
        try:
            first=await store.enqueue(work,'first',uuid4())
            await store.enqueue(replace(work,request=work.request.model_copy(update={'max_time_seconds':.5})),'second',uuid4())
            claims=await asyncio.gather(*(store.claim(work.request.institution_id,uuid4()) for _ in range(8)))
            claimed=[row for row in claims if row is not None]
            assert len(claimed)==1 and claimed[0].job_id==first.job_id
            assert len(await attempts(store,work.request.institution_id,first.job_id))==1
        finally:await store.close()
    run(execute())


def test_lease_heartbeat_expiration_and_fencing_stale_worker():
    async def execute():
        store,work,result=await seeded(lease_seconds=.5)
        tenant=work.request.institution_id
        try:
            accepted=await store.enqueue(work,'lease',uuid4());old=await store.claim(tenant,uuid4())
            assert await store.heartbeat(old)
            await asyncio.sleep(.6)
            newer=await store.claim(tenant,uuid4())
            assert newer.job_id==old.job_id and newer.attempt_number==2 and newer.token!=old.token
            assert not await store.heartbeat(old)
            assert not await store.complete(old,result)
            assert not await store.fail(old,'WORKER_FAILED')
            # Use a longer renewed lease for actual independent persistence.
            store.lease_seconds=30;assert await store.heartbeat(newer)
            assert await store.complete(newer,result)
            saved=await store.get(tenant,accepted.job_id)
            assert saved.status=='COMPLETED' and saved.validation_status=='PASSED'
            assert [row.outcome for row in await attempts(store,tenant,accepted.job_id)]==['LEASE_EXPIRED','COMPLETED']
        finally:await store.close()
    run(execute())


def test_exhausted_lease_recovery_is_terminal_and_cross_tenant_rls_holds():
    async def execute():
        store,work,_=await seeded(lease_seconds=.1,max_attempts=1)
        tenant=work.request.institution_id
        try:
            first=await store.enqueue(work,'last-lease',uuid4());await store.claim(tenant,uuid4())
            await asyncio.sleep(.15)
            assert await store.claim(tenant,uuid4()) is None
            assert (await store.get(tenant,first.job_id)).status=='FAILED'
            with pytest.raises(ApiError) as error:await store.get(OTHER_TENANT,first.job_id)
            assert error.value.status==404
            async with store.transaction(OTHER_TENANT,read=True) as repo:
                # Even an accidentally unfiltered query cannot see the other tenant.
                assert await repo._session.scalar(select(func.count()).select_from(GenerationJob).where(GenerationJob.id==first.job_id))==0
        finally:await store.close()
    run(execute())


@pytest.mark.parametrize('status',[SolverStatus.UNKNOWN,SolverStatus.INFEASIBLE,SolverStatus.MODEL_INVALID])
def test_native_failure_reports_are_persisted_without_drafts(status):
    async def execute():
        store,work,result=await seeded()
        try:
            accepted=await store.enqueue(work,'native',uuid4())
            failed=SolverResult(status,(),.1,None,original_occurrence_count=2)
            worker=DurableWorker(store,SnapshotInputs(store),Executor(failed))
            assert await worker.run_once(work.request.institution_id)
            end=await store.get(work.request.institution_id,accepted.job_id)
            assert end.status=='FAILED' and end.solver_status==status.value and end.timetable_version_id is None
            reports=await attempts(store,work.request.institution_id,accepted.job_id)
            assert reports[0].report['solver_result']['status']==status.value
            assert end.attempt_number==1 # no automatic solver-policy retries
        finally:await store.close()
    run(execute())


@pytest.mark.parametrize('kind',['partial','staff','duplicate'])
def test_independent_validation_blocks_corrupt_result_before_draft_creation(kind):
    async def execute():
        store,work,result=await seeded()
        if kind=='partial':result=replace(result,schedule=result.schedule[:1])
        if kind=='staff':result=replace(result,schedule=(replace(result.schedule[0],assigned_staff_id='INVALID'),result.schedule[1]))
        if kind=='duplicate':result=replace(result,schedule=(result.schedule[0],result.schedule[0]))
        try:
            accepted=await store.enqueue(work,'invalid',uuid4())
            await DurableWorker(store,SnapshotInputs(store),Executor(result)).run_once(work.request.institution_id)
            end=await store.get(work.request.institution_id,accepted.job_id)
            assert end.validation_status=='FAILED' and end.error.category=='VALIDATION_FAILED' and end.timetable_version_id is None
        finally:await store.close()
    run(execute())


def test_timeout_and_safe_bounded_worker_failure_retries():
    async def execute():
        store,work,result=await seeded(lease_seconds=.5)
        work=replace(work,request=work.request.model_copy(update={'job_timeout_seconds':.1,'max_time_seconds':.01}))
        try:
            accepted=await store.enqueue(work,'timeout',uuid4())
            executor=Executor(result,delay=2)
            await DurableWorker(store,SnapshotInputs(store),executor).run_once(work.request.institution_id)
            end=await store.get(work.request.institution_id,accepted.job_id)
            assert end.error.category=='JOB_TIMEOUT' and executor.cancelled==1
            # Infrastructure crashes are bounded retries; no secret exception text.
            second_work=replace(work,request=work.request.model_copy(update={'job_timeout_seconds':10.,'max_time_seconds':1.}))
            accepted=await store.enqueue(second_work,'crash',uuid4())
            worker=DurableWorker(store,SnapshotInputs(store),Executor(result,error=WorkerFailure('secret database URL')))
            await worker.run_once(work.request.institution_id)
            assert (await store.get(work.request.institution_id,accepted.job_id)).status=='PENDING'
            await worker.run_once(work.request.institution_id)
            end=await store.get(work.request.institution_id,accepted.job_id)
            assert end.status=='FAILED' and end.attempt_number==2 and 'secret' not in end.model_dump_json()
        finally:await store.close()
    run(execute())


def test_atomic_draft_and_job_finalization_roll_back_together(monkeypatch):
    async def execute():
        import schedence_teaching.generation.postgres as module
        store,work,result=await seeded();tenant=work.request.institution_id
        try:
            accepted=await store.enqueue(work,'atomic',uuid4());claim=await store.claim(tenant,uuid4())
            original=module.persist_in_transaction
            async def fail_after_save(*args,**kwargs):
                await original(*args,**kwargs);raise RuntimeError('simulate failure before commit')
            monkeypatch.setattr(module,'persist_in_transaction',fail_after_save)
            with pytest.raises(RuntimeError):await store.complete(claim,result)
            assert (await store.get(tenant,accepted.job_id)).status=='RUNNING'
            from schedence_teaching.persistence.models import TimetableVersion
            async with store.transaction(tenant,read=True) as repo:
                assert await repo._session.scalar(select(func.count()).select_from(TimetableVersion).where(TimetableVersion.institution_id==tenant))==0
            monkeypatch.setattr(module,'persist_in_transaction',original)
            assert await store.complete(claim,result)
            end=await store.get(tenant,accepted.job_id)
            async with store.transaction(tenant,read=True) as repo:
                version=await repo.get_timetable(end.timetable_version_id)
                assert len(version.coverage)==2 and not version.version['academic_complete']
        finally:await store.close()
    run(execute())


def test_heartbeat_keeps_long_running_work_owned_and_changed_inputs_fail_safely():
    async def execute():
        store,work,result=await seeded(lease_seconds=.6)
        tenant=work.request.institution_id
        try:
            accepted=await store.enqueue(work,'long',uuid4())
            worker=DurableWorker(store,SnapshotInputs(store),Executor(result,delay=1.2))
            task=asyncio.create_task(worker.run_once(tenant))
            await asyncio.sleep(.9)
            assert (await store.get(tenant,accepted.job_id)).last_heartbeat_at is not None
            assert await store.claim(tenant,uuid4()) is None
            await task
            assert (await store.get(tenant,accepted.job_id)).status=='COMPLETED'
            next_work=replace(work,request=work.request.model_copy(update={'max_time_seconds':.5}))
            accepted=await store.enqueue(next_work,'changed',uuid4())
            changed=work.document.model_copy(deep=True);changed.sources[0].provenance['changed']=True
            await DurableWorker(store,Inputs(changed),Executor(result)).run_once(tenant)
            end=await store.get(tenant,accepted.job_id)
            assert end.error.category=='INPUTS_CHANGED' and end.status=='FAILED' and not end.timetable_version_id
        finally:await store.close()
    run(execute())


def test_graceful_worker_restart_requeues_and_preserves_the_same_job():
    async def execute():
        store,work,result=await seeded()
        tenant=work.request.institution_id
        jobs=DurableGenerationJobs(store,SnapshotInputs(store),Executor(result,delay=10),institutions=[tenant],enabled=True,poll_seconds=.01)
        accepted=await jobs.submit(work.request,'shutdown',uuid4())
        for _ in range(100):
            if (await jobs.get(tenant,accepted.job_id)).status=='RUNNING':break
            await asyncio.sleep(.01)
        await jobs.close()
        fresh=PostgresJobRepository(store.engine.url,retry_backoff_seconds=0)
        try:
            assert (await fresh.get(tenant,accepted.job_id)).status=='PENDING'
            await DurableWorker(fresh,SnapshotInputs(fresh),Executor(result)).run_once(tenant)
            end=await fresh.get(tenant,accepted.job_id)
            assert end.status=='COMPLETED' and end.attempt_number==2
        finally:await fresh.close()
    run(execute())


def test_real_process_exit_recovery_and_new_worker_completes(tmp_path):
    async def execute():
        store,work,result=await seeded(lease_seconds=.2);tenant=work.request.institution_id
        accepted=await store.enqueue(work,'process-exit',uuid4())
        # No URL/token in argv; the child reuses the ignored local config path.
        script=tmp_path/'crashed_worker.py'
        script.write_text('''import os,json\nfrom pathlib import Path\nfrom uuid import UUID,uuid4\nfrom schedence_db.runtime import run\nfrom schedence_teaching.generation.postgres import PostgresJobRepository\nasync def main():\n config=json.loads(Path(os.environ['TEACHING_ISOLATED_DATABASE_CONFIG']).read_text())\n store=PostgresJobRepository(config['runtime_url'],lease_seconds=.2,retry_backoff_seconds=0)\n claim=await store.claim(UUID(os.environ['DURABLE_TEST_TENANT']),uuid4())\n assert claim is not None\n os._exit(0)\nrun(main())\n''',encoding='utf-8')
        environment=dict(os.environ,DURABLE_TEST_TENANT=str(tenant))
        completed=await asyncio.to_thread(subprocess.run,[sys.executable,str(script)],env=environment,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        assert completed.returncode==0
        await store.close();await asyncio.sleep(.3)
        fresh=PostgresJobRepository(store.engine.url,lease_seconds=30,retry_backoff_seconds=0)
        try:
            executor=Executor(result)
            assert await DurableWorker(fresh,SnapshotInputs(fresh),executor).run_once(tenant)
            end=await fresh.get(tenant,accepted.job_id)
            assert end.status=='COMPLETED' and end.attempt_number==2 and end.timetable_version_id
            assert [row.outcome for row in await attempts(fresh,tenant,end.job_id)]==['LEASE_EXPIRED','COMPLETED']
        finally:await fresh.close()
    run(execute())


def test_real_http_swagger_native_process_persistence_and_restart():
    async def setup():return await seeded()
    store,work,_=run(setup());tenant=work.request.institution_id
    directory=Path(__file__).resolve().parents[1]/'local-data/generation-preview/durable-test-workers'/str(uuid4())
    # Use a fresh engine in TestClient's loop.
    url=store.engine.url;run(store.close());store=PostgresJobRepository(url)
    jobs=DurableGenerationJobs(store,SnapshotInputs(store),ProcessGenerationExecutor(directory),institutions=[tenant],enabled=True,poll_seconds=.05)
    tokens=TokenFactory();subject=str(uuid4());user=uuid4()
    profile=dict(user_id=str(user),cognito_sub=subject,account_status='ACTIVE',institutions=[
        dict(institution_id=str(tenant),roles=['ACADEMIC_ADMIN'],permissions=['teaching:generate'])])
    http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=profile)))
    runtime=TeachingRuntime(tokens.verifier(),IdentityPermissionsClient('http://identity.test',http),
        DatabaseTimetableStore(store.factory),shutdown_hooks=(http.aclose,),generations=jobs)
    headers={'Authorization':'Bearer '+tokens.access_token(subject),'Idempotency-Key':'durable-api'}
    with TestClient(create_app(runtime),backend_options={'loop_factory':asyncio.SelectorEventLoop}) as client:
        assert client.get('/docs').status_code==200
        schema=client.get('/openapi.json').json()
        for prefix in ('/api/teaching','/api/v1/teaching'):
            assert 'post' in schema['paths'][prefix+'/generations']
            assert 'get' in schema['paths'][prefix+'/generations/{job_id}']
            assert 'get' in schema['paths'][prefix+'/timetables']
        response=client.post('/api/teaching/generations',json=work.request.model_dump(mode='json'),headers=headers)
        assert response.status_code==202,response.text
        for _ in range(1000):
            state=client.get(response.headers['Location'],headers=headers)
            if state.json()['status'] in ('COMPLETED','FAILED'):break
            time.sleep(.01)
        assert state.json()['status']=='COMPLETED',state.text
        version=state.json()['timetable_version_id']
        detail=client.get('/api/teaching/timetables/'+version,headers=headers)
        assert detail.status_code==200 and detail.json()['academic_complete'] is False
        assert client.get('/api/teaching/timetables/'+version+'/sessions',headers=headers).json()['total']==2
        assert client.post('/api/teaching/generations',json=work.request.model_dump(mode='json'),headers=headers).json()['job_id']==state.json()['job_id']
        profile['institutions'][0]['permissions']=['teaching:timetables:read']
        assert client.get(response.headers['Location'],headers=headers).status_code==403
        assert client.post('/api/teaching/generations',json=work.request.model_dump(mode='json'),headers=headers).status_code==403
        assert client.get('/api/teaching/timetables/'+version,headers=headers).status_code==200
        profile['institutions'][0]['permissions']=['teaching:generate']
        expired={'Authorization':'Bearer '+tokens.access_token(subject,expires_in=-30)}
        assert client.get(response.headers['Location'],headers=expired).status_code==401
        assert client.post('/api/teaching/generations',json=work.request.model_dump(mode='json'),headers=expired).status_code==401
        assert client.get('/api/teaching/generations/'+state.json()['job_id']+'?institution_id='+str(OTHER_TENANT),headers=headers).status_code==403
