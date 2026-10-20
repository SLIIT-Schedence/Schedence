"""Asynchronous job state, authorization and exact-coverage regression checks."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import time
from uuid import uuid4

import pytest
from pydantic import ValidationError

from schedence_teaching.api.errors import ApiError
from schedence_teaching.api.runtime import TeachingSettings
from schedence_teaching.generation.contracts import GenerationRequest
from schedence_teaching.generation.inputs import GenerationWork, prepare_database_work
from schedence_teaching.generation.jobs import DevelopmentGenerationJobs
from schedence_teaching.generation.persistence import IsolatedDraftPersistence, NoDraftPersistence
from schedence_teaching.generation.worker import GenerationOutcome, ProcessGenerationExecutor, WorkerFailure
from schedence_teaching.persistence.snapshots import content_digest, prepare_attempt
from schedence_teaching.solver import SolverResult, SolverStatus

from test_persistence_contracts import TENANT, OTHER_TENANT, example
from test_teaching_read_api import api, grant
from test_database_generation import database_fixture
from test_elective_enrollment import elective_fixture

BASE = '/api/teaching/generations'


def request_for(document, **changes):
    sources = {row.label: row.sha256 for row in document.sources}
    scope = next(row for row in document.scopes if row.scope_status == 'SUPPORTED')
    return GenerationRequest.model_validate(dict(institution_id=document.institution_id, snapshot_id=uuid4(),
        semester=scope.semester, academic_years=[scope.academic_year], streams=[scope.stream],
        specializations=[scope.specialization], calendar_label=document.calendar_label,
        policy_version='HISTORICAL_SNAPSHOT', academic_term_reference=document.academic_term_reference,
        calendar_reference=sources.get('PP1:calendar-configuration', 'a'*64),
        scheduling_policy_reference=sources.get('PP1:synthetic-elective-registration-evidence', 'b'*64),
        max_time_seconds=1.0, job_timeout_seconds=30.0) | changes)


class Inputs:
    def __init__(self, document):
        self.document, self.calls, self.error = document, 0, None

    async def prepare(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return GenerationWork(request, self.document)


class Executor:
    def __init__(self, result, *, delay=0, error=None):
        self.result, self.delay, self.error = result, delay, error
        self.calls = self.cancelled = 0

    async def run(self, work, job_id):
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            return GenerationOutcome(self.result)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class Sink(NoDraftPersistence):
    mode = 'ISOLATED_POSTGRESQL'
    def __init__(self): self.calls = 0
    async def save(self, *args):
        self.calls += 1
        return uuid4(), uuid4()


async def finished(jobs, identifier):
    for _ in range(3000):
        job = await jobs.get(TENANT, identifier)
        if job.status in {'COMPLETED', 'FAILED'}:
            return job
        await asyncio.sleep(.01)
    raise AssertionError('generation failed to finish')


def manager(*, result=None, delay=0, **options):
    document, successful = example()
    inputs = Inputs(document)
    executor = Executor(result or successful, delay=delay)
    jobs = DevelopmentGenerationJobs(inputs, executor, enabled=True, **options)
    return jobs, request_for(document), executor


def install(api, **options):
    jobs, request, executor = manager(**options)
    api.runtime.generations = jobs
    return jobs, request.model_dump(mode='json'), executor


def post(api, body, key='synthetic-1', **options):
    return api.client.post(BASE, json=body, headers=api.headers | {'Idempotency-Key':key}, **options)


def poll(api, job_id):
    for _ in range(3000):
        response = api.client.get(BASE+'/'+str(job_id), headers=api.headers)
        assert response.status_code == 200, response.text
        if response.json()['status'] in {'COMPLETED','FAILED'}:
            return response.json()
        time.sleep(.01)
    raise AssertionError('HTTP job did not finish')


def test_authorized_acceptance_both_prefixes_and_safe_result(api):
    jobs, body, executor = install(api, delay=.05)
    response = post(api, body)
    assert response.status_code == 202, response.text
    assert response.headers['Location'].startswith('/api/v1/teaching/generations/')
    value = response.json()
    assert value['status'] == 'PENDING' and value['durability'] == 'DEVELOPMENT_NON_DURABLE'
    completed = poll(api, value['job_id'])
    assert completed['status'] == 'COMPLETED'
    assert completed['expected_occurrences'] == completed['scheduled_occurrences'] == 2
    assert completed['validation_status'] == 'PASSED'
    assert completed['scope_completion'] == 'ALL_SELECTED_KNOWN_OCCURRENCES_VALIDATED'
    assert not completed['academic_complete'] and not completed['university_calendar_approved']
    assert completed['calendar_label'] == 'PP1_EXPERIMENTAL_CALENDAR'
    assert completed['unresolved_requirement_codes'] == ['RESEARCH_PENDING_SESSION_MAPPING']
    assert completed['persistence'] == 'DISABLED' and 'timetable_version_id' not in completed
    assert not any(key in json.dumps(completed) for key in ('staff_name','worker.log','solver-result.json','student_ids'))
    assert api.client.get(response.headers['Location'], headers=api.headers).status_code == 200
    assert api.client.post('/api/v1/teaching/generations',json=body,
        headers=api.headers | {'Idempotency-Key':'synthetic-1'}).json()['job_id'] == value['job_id']
    assert executor.calls == 1


@pytest.mark.parametrize('role,permissions', [('ACADEMIC_ADMIN',['teaching:timetables:read']),
    ('ACADEMIC_ADMIN',['teaching:approve']), ('INSTITUTION_ADMIN',[]), ('LECTURER',['teaching:timetables:read:self']),
    ('HOD',[]), ('DEAN',[]), ('PROFESSOR',[]), ('PROGRAM_COORDINATOR',[])])
def test_read_roles_and_appointments_do_not_grant_generation(api, role, permissions):
    jobs,body,_ = install(api)
    grant(api,role,permissions)
    assert post(api,body).status_code == 403
    assert api.client.get(BASE+'/'+str(uuid4()),headers=api.headers).status_code == 403
    assert jobs.inputs.calls == 0


@pytest.mark.parametrize('kind',['missing','expired','malformed','cross_tenant','spoofed'])
def test_generation_authentication_and_tenant_boundaries(api,kind):
    jobs,body,_ = install(api)
    if kind == 'cross_tenant':
        body['institution_id'] = str(OTHER_TENANT)
        assert post(api,body).status_code == 403
    else:
        if kind == 'missing': headers={}
        elif kind == 'malformed': headers={'Authorization':'Bearer invalid'}
        elif kind == 'expired': headers={'Authorization':'Bearer '+api.tokens.access_token(api.subject,expires_in=-30)}
        else:
            grant(api,'STUDENT')
            token=api.tokens.access_token(api.subject,roles=['ACADEMIC_ADMIN'],permissions=['teaching:generate'])
            headers={'Authorization':'Bearer '+token,'X-Institution-ID':str(TENANT),'X-Permissions':'teaching:generate'}
        status=403 if kind=='spoofed' else 401
        assert api.client.post(BASE,json=body,headers=headers).status_code==status
    assert jobs.inputs.calls == 0


@pytest.mark.parametrize('changes',[{'academic_years':[0]}, {'semester':3}, {'streams':['EVENING']},
    {'academic_years':[]}, {'specializations':[]}, {'snapshot_id':str(uuid4())+'bad'},
    {'calendar_reference':'wrong'}, {'policy_version':'LATEST'}, {'max_time_seconds':-1},
    {'job_timeout_seconds':.1}, {'assigned_staff_id':'LEC001'}, {'expected_previous_version_id':str(uuid4())}])
def test_invalid_requests_are_not_queued(api,changes):
    jobs,body,_=install(api)
    assert post(api,body | changes).status_code==422
    assert jobs.inputs.calls==0


def test_default_disabled_no_false_acceptance_or_production_access(api):
    _,body,_=install(api)
    api.runtime.generations=None
    response=post(api,body)
    assert response.status_code==503 and response.json()['error']['code']=='GENERATION_DISABLED'
    assert not api.store.calls
    assert TeachingSettings.model_fields['teaching_generation_mode'].default=='DISABLED'


def test_missing_idempotency_and_deployment_timeout_limit(api):
    jobs,body,_=install(api,max_timeout_seconds=2)
    assert api.client.post(BASE,json=body,headers=api.headers).status_code==400
    assert post(api,body).status_code==400
    assert jobs.inputs.calls==0


def test_idempotency_consistency_and_active_duplicate(api):
    jobs,body,executor=install(api,delay=.2)
    first=post(api,body)
    assert first.status_code==202
    assert post(api,body).json()['job_id']==first.json()['job_id']
    assert post(api,body | {'max_time_seconds':.5}).status_code==409
    assert post(api,body,key='different').status_code==409
    assert jobs.inputs.calls==1


def test_job_lookup_is_scoped_and_fresh_permissions_are_required(api):
    _,body,_=install(api)
    first=post(api,body).json()
    assert api.client.get(BASE+'/'+str(uuid4()),headers=api.headers).status_code==404
    assert api.client.get(BASE+'/'+first['job_id'],params={'institution_id':str(OTHER_TENANT)},headers=api.headers).status_code==403
    api.profile['institutions']=[dict(institution_id=str(OTHER_TENANT),roles=['ACADEMIC_ADMIN'],permissions=['teaching:generate'])]
    assert api.client.get(BASE+'/'+first['job_id'],headers=api.headers).status_code==404
    grant(api,'ACADEMIC_ADMIN',[])
    assert api.client.get(BASE+'/'+first['job_id'],headers=api.headers).status_code==403


def test_http_polling_and_health_continue_during_long_generation(api):
    _,body,_=install(api,delay=.3)
    started=time.monotonic();accepted=post(api,body)
    assert accepted.status_code==202 and time.monotonic()-started<.25
    assert api.client.get('/health').status_code==200
    running=api.client.get(BASE+'/'+accepted.json()['job_id'],headers=api.headers).json()
    assert running['status']=='RUNNING' and running['elapsed_seconds']>=0
    assert poll(api,accepted.json()['job_id'])['status']=='COMPLETED'


def test_failed_preflight_does_not_reserve_key_or_create_job(api):
    jobs,body,_=install(api)
    jobs.inputs.error=RuntimeError('private credentials never returned')
    first=post(api,body)
    assert first.status_code==503 and 'credentials' not in first.text
    assert not jobs._jobs and not jobs._keys
    jobs.inputs.error=None
    assert post(api,body).status_code==202


@pytest.mark.parametrize('status',[SolverStatus.UNKNOWN,SolverStatus.INFEASIBLE,SolverStatus.MODEL_INVALID])
def test_unsuccessful_solver_status_is_separate_from_failed_job_and_never_saved(status):
    async def run():
        failed=SolverResult(status,(),.1,None,original_occurrence_count=2)
        sink=Sink();jobs,request,_=manager(result=failed,persistence=sink)
        try:
            accepted=await jobs.submit(request,'native-failure',uuid4());end=await finished(jobs,accepted.job_id)
            assert end.status=='FAILED' and end.solver_status==status.value
            assert end.validation_status=='NOT_ASSESSED' and end.scheduled_occurrences==0
            assert end.error.category=='SOLVER_'+status.value and sink.calls==0
            assert (await jobs.submit(request,'native-failure',uuid4())).job_id==accepted.job_id
        finally:await jobs.close()
    asyncio.run(run())


@pytest.mark.parametrize('corruption',['partial','wrong_staff','overlap','duplicate'])
def test_independent_parent_validation_precedes_persistence(corruption):
    async def run():
        _,result=example();bookings=list(result.schedule)
        if corruption=='partial':bookings=bookings[:1]
        if corruption=='wrong_staff':bookings[0]=replace(bookings[0],assigned_staff_id='UNKNOWN')
        if corruption=='overlap':bookings[1]=replace(bookings[1],assigned_staff_id=bookings[0].assigned_staff_id,
            day=bookings[0].day,start_minute=bookings[0].start_minute,end_minute=bookings[0].end_minute)
        if corruption=='duplicate':bookings[1]=bookings[0]
        sink=Sink();jobs,request,_=manager(result=replace(result,schedule=tuple(bookings)),persistence=sink)
        try:
            job=await jobs.submit(request,'invalid',uuid4());end=await finished(jobs,job.job_id)
            assert end.status=='FAILED' and end.error.category=='VALIDATION_FAILED'
            assert end.validation_status=='FAILED' and not end.timetable_version_id and sink.calls==0
        finally:await jobs.close()
    asyncio.run(run())


def test_atomic_concurrent_requests_limits_and_timeout_release():
    async def run():
        jobs,request,executor=manager(delay=.2,per_institution_limit=1)
        request=request.model_copy(update={'job_timeout_seconds':.05,'max_time_seconds':.01})
        try:
            accepted=await asyncio.gather(*(jobs.submit(request,'same',uuid4()) for _ in range(12)))
            assert len({job.job_id for job in accepted})==1 and jobs.inputs.calls==1
            with pytest.raises(ApiError) as error:
                await jobs.submit(request.model_copy(update={'max_time_seconds':.02}),'other',uuid4())
            assert error.value.code=='INSTITUTION_JOB_LIMIT'
            end=await finished(jobs,accepted[0].job_id)
            assert end.error.category=='JOB_TIMEOUT' and executor.cancelled==1
            retry=await jobs.submit(request,'new-key',uuid4())
            assert retry.job_id!=end.job_id
        finally:await jobs.close()
    asyncio.run(run())


def test_worker_crash_and_abandoned_pending_jobs_are_explicit():
    async def run():
        jobs,request,executor=manager(delay=10)
        first=await jobs.submit(request,'first',uuid4())
        second=await jobs.submit(request.model_copy(update={'max_time_seconds':.5}),'second',uuid4())
        await asyncio.sleep(.01)
        await jobs.close()
        assert (await jobs.get(TENANT,first.job_id)).error.category=='WORKER_INTERRUPTED'
        assert (await jobs.get(TENANT,second.job_id)).error.category=='WORKER_INTERRUPTED'
        assert executor.cancelled==1
        with pytest.raises(ApiError):await jobs.submit(request,'third',uuid4())
        fresh,request,executor=manager();executor.error=WorkerFailure('secret log')
        accepted=await fresh.submit(request,'crash',uuid4());end=await finished(fresh,accepted.job_id)
        assert end.error.category=='WORKER_FAILED' and 'secret' not in end.model_dump_json()
        with pytest.raises(ApiError):await fresh.get(TENANT,first.job_id) # no restart durability claimed
        await fresh.close()
    asyncio.run(run())


@pytest.mark.parametrize('url',['postgresql+psycopg://user:password@remote.invalid/neondb',
    'postgresql+psycopg://user:password@127.0.0.1:5432/neondb',
    'postgresql+psycopg://user:password@localhost:5432/schedence_step12b4_test',
    'postgresql+psycopg://user:password@127.0.0.1:5432/schedence_step12b4_test?host=remote.invalid'])
def test_production_write_adapter_cannot_be_enabled(url):
    with pytest.raises(ValueError,match='isolated loopback'):IsolatedDraftPersistence(url)


def test_database_provider_reuses_source_contracts_and_captures_exact_selected_scope(database_fixture):
    evidence,_=database_fixture
    request=request_for(evidence.document,snapshot_id=evidence.snapshot_id,academic_years=[4],streams=['WEEKDAY'])
    work=prepare_database_work(evidence,request)
    assert work.document.external_references==evidence.document.external_references
    assert work.document.institution_id==TENANT
    assert all(row.academic_year==4 and row.stream.value=='WEEKDAY' for row in work.document.scopes)
    assert 'RESEARCH_PENDING_SESSION_MAPPING' in {row.issue_code for row in work.document.issues}
    assert content_digest(evidence.document)!=content_digest(work.document)
    assert not any(row.source_reference=='invented' for row in work.document.external_references)


@pytest.mark.parametrize('field,value',[('calendar_reference','f'*64),('scheduling_policy_reference','f'*64),
    ('academic_term_reference','WRONG'),('institution_id',OTHER_TENANT)])
def test_snapshot_references_cannot_be_replaced_by_caller(database_fixture,field,value):
    evidence,_=database_fixture
    request=request_for(evidence.document,snapshot_id=evidence.snapshot_id,**{field:value})
    with pytest.raises(ApiError) as error:prepare_database_work(evidence,request)
    assert error.value.status==400


def test_real_spawned_worker_native_solver_and_private_validation(tmp_path):
    # Real worker artifacts must remain under ignored service local-data.
    directory=Path(__file__).resolve().parents[1]/'local-data/generation-preview/test-workers'/str(uuid4())
    async def run():
        document,result=example();request=request_for(document)
        jobs=DevelopmentGenerationJobs(Inputs(document),ProcessGenerationExecutor(directory),enabled=True)
        try:
            job=await jobs.submit(request,'real-native',uuid4());end=await finished(jobs,job.job_id)
            assert end.status=='COMPLETED',end.model_dump_json()
            assert end.validation_status=='PASSED' and end.scheduled_occurrences==2
            assert (directory/str(job.job_id)/'independent-validation.json').exists()
        finally:await jobs.close()
    asyncio.run(run())


def test_real_worker_timeout_reaps_spawned_process():
    import multiprocessing
    directory=Path(__file__).resolve().parents[1]/'local-data/generation-preview/test-workers'/str(uuid4())
    async def run():
        document,_=example()
        request=request_for(document,max_time_seconds=.01,job_timeout_seconds=.05)
        before={child.pid for child in multiprocessing.active_children()}
        executor=ProcessGenerationExecutor(directory)
        with pytest.raises(TimeoutError):await executor.run(GenerationWork(request,document),uuid4())
        assert {child.pid for child in multiprocessing.active_children()}==before
    asyncio.run(run())


def test_database_inputs_and_evidence_are_stable_in_a_fresh_worker_process(database_fixture):
    evidence,_=database_fixture
    request=request_for(evidence.document,snapshot_id=evidence.snapshot_id,academic_years=[4],streams=['WEEKDAY'])
    work=prepare_database_work(evidence,request)
    directory=Path(__file__).resolve().parents[1]/'local-data/generation-preview/test-workers'/str(uuid4())
    async def run():
        executor=ProcessGenerationExecutor(directory)
        outcome=await executor.run(work,uuid4())
        attempt=prepare_attempt(work.document,outcome.result)
        assert attempt.success and attempt.scheduled_occurrences==attempt.expected_occurrences
    asyncio.run(run())


def test_shutdown_during_input_preparation_never_accepts_a_late_job():
    async def run():
        jobs,request,_=manager()
        started=asyncio.Event();release=asyncio.Event()
        original=jobs.inputs.prepare
        async def prepare(request):
            started.set();await release.wait();return await original(request)
        jobs.inputs.prepare=prepare
        submission=asyncio.create_task(jobs.submit(request,'during-shutdown',uuid4()))
        await started.wait();await jobs.close();release.set()
        with pytest.raises(ApiError) as error:await submission
        assert error.value.code=='GENERATION_DISABLED' and not jobs._jobs
    asyncio.run(run())


def test_bounded_registry_fails_without_false_enqueue_or_expiring_idempotency():
    async def run():
        jobs,request,_=manager(retained_job_limit=1)
        try:
            first=await jobs.submit(request,'first',uuid4());await finished(jobs,first.job_id)
            with pytest.raises(ApiError) as error:await jobs.submit(request,'second',uuid4())
            assert error.value.code=='JOB_REGISTRY_FULL'
            assert (await jobs.submit(request,'first',uuid4())).job_id==first.job_id
        finally:await jobs.close()
    asyncio.run(run())
