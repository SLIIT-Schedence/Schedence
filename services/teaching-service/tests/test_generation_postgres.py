"""API job -> reviewed draft persistence on disposable localhost PostgreSQL only."""

import asyncio
from dataclasses import replace
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from schedence_auth.testing import TokenFactory
from schedence_db.runtime import run
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from schedence_teaching.api.identity import IdentityPermissionsClient
from schedence_teaching.api.runtime import TeachingRuntime
from schedence_teaching.generation.jobs import DevelopmentGenerationJobs
from schedence_teaching.generation.persistence import IsolatedDraftPersistence
from schedence_teaching.generation.worker import ProcessGenerationExecutor
from schedence_teaching.main import create_app
from schedence_teaching.persistence.models import TimetableVersion, InputSnapshot
from schedence_teaching.persistence.snapshots import content_digest
from schedence_teaching.persistence.timetable_service import timetable_reader

from test_generation_jobs import Inputs, Executor, request_for, finished
from test_persistence_contracts import TENANT, OTHER_TENANT, example
from test_timetable_persistence_isolated import isolated_engine

pytestmark = pytest.mark.skipif(os.environ.get('SCHEDENCE_TEACHING_ISOLATED_DB_TESTS') != '1',
    reason='generation persistence writes require explicit disposable localhost PostgreSQL')


def test_asynchronous_generation_persistence_idempotency_history_and_read_back():
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        sink=IsolatedDraftPersistence(engine.url)
        document,result=example();inputs=Inputs(document);executor=Executor(result)
        jobs=DevelopmentGenerationJobs(inputs,executor,persistence=sink,enabled=True)
        try:
            request=request_for(document)
            first=await jobs.submit(request,'first',uuid4());first=await finished(jobs,first.job_id)
            assert first.status=='COMPLETED' and first.timetable_version_id and first.result_snapshot_id
            async with timetable_reader(factory,TENANT) as repo:
                original=await repo.get_timetable(first.timetable_version_id)
                series=original.version['series_id']
                original_digest=content_digest((original.version,original.run,original.validation,original.deliveries,original.coverage))
                assert original.run['snapshot_id']==first.result_snapshot_id
                assert len(original.coverage)==first.expected_occurrences==first.scheduled_occurrences==2
                assert original.logical_status=='VALIDATED' and original.limitations['research_pending']
            again=await jobs.submit(request,'first',uuid4())
            assert again.timetable_version_id==first.timetable_version_id and executor.calls==1
            changed=document.model_copy(deep=True);changed.sources[0].provenance['policy']='SYNTHETIC_GENERATION_V2'
            inputs.document=changed
            second_request=request_for(changed,timetable_series_id=series,expected_previous_version_id=first.timetable_version_id)
            second=await jobs.submit(second_request,'second',uuid4());second=await finished(jobs,second.job_id)
            assert second.status=='COMPLETED' and second.timetable_version_id!=first.timetable_version_id
            async with timetable_reader(factory,TENANT) as repo:
                old=await repo.get_timetable(first.timetable_version_id)
                assert content_digest((old.version,old.run,old.validation,old.deliveries,old.coverage))==original_digest
                versions=await repo.list_versions(series)
                assert len(versions)==2 and versions[1].previous_version_id==first.timetable_version_id
            async with timetable_reader(factory,OTHER_TENANT) as repo:
                from schedence_teaching.persistence.ports import PersistenceConflict
                with pytest.raises(PersistenceConflict):await repo.get_timetable(first.timetable_version_id)
        finally:
            await jobs.close();await engine.dispose()
    run(execute())


def test_invalid_job_creates_no_postgresql_snapshot_or_version():
    async def execute():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        async def counts():
            async with timetable_reader(factory,TENANT) as repo:
                return tuple([await repo._session.scalar(select(func.count()).select_from(model))
                    for model in (TimetableVersion,InputSnapshot)])
        document,result=example();jobs=DevelopmentGenerationJobs(Inputs(document),
            Executor(replace(result,schedule=result.schedule[:1])),enabled=True,persistence=IsolatedDraftPersistence(engine.url))
        try:
            before=await counts()
            job=await jobs.submit(request_for(document),'bad',uuid4());job=await finished(jobs,job.job_id)
            assert job.status=='FAILED' and job.validation_status=='FAILED' and await counts()==before
        finally:await jobs.close();await engine.dispose()
    run(execute())


def test_real_http_native_process_and_isolated_postgresql_round_trip():
    from pathlib import Path
    engine=isolated_engine();url=engine.url
    document,_=example();request=request_for(document)
    directory=Path(__file__).resolve().parents[1]/'local-data/generation-preview/test-workers'/str(uuid4())
    jobs=DevelopmentGenerationJobs(Inputs(document),ProcessGenerationExecutor(directory),enabled=True,
        persistence=IsolatedDraftPersistence(url))
    tokens=TokenFactory();subject=str(uuid4())
    profile=dict(user_id=str(uuid4()),cognito_sub=subject,account_status='ACTIVE',institutions=[
        dict(institution_id=str(TENANT),roles=['ACADEMIC_ADMIN'],permissions=['teaching:generate'])])
    http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=profile)))
    runtime=TeachingRuntime(tokens.verifier(),IdentityPermissionsClient('http://identity.test',http),None,
        shutdown_hooks=(http.aclose,),generations=jobs)
    headers={'Authorization':'Bearer '+tokens.access_token(subject),'Idempotency-Key':'sql-native'}
    with TestClient(create_app(runtime),backend_options={'loop_factory':asyncio.SelectorEventLoop}) as client:
        accepted=client.post('/api/teaching/generations',json=request.model_dump(mode='json'),headers=headers)
        assert accepted.status_code==202,accepted.text
        import time
        for _ in range(3000):
            state=client.get(accepted.headers['Location'],headers=headers)
            if state.json()['status'] in {'COMPLETED','FAILED'}:break
            time.sleep(.01)
        assert state.json()['status']=='COMPLETED',state.text
        assert state.json()['validation_status']=='PASSED' and state.json()['timetable_version_id']
        repeat=client.post('/api/v1/teaching/generations',json=request.model_dump(mode='json'),headers=headers)
        assert repeat.json()['job_id']==accepted.json()['job_id']
