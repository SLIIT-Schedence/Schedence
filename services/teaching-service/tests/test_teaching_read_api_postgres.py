"""Real PostgreSQL HTTP checks on synthetic LOCAL data; no production writes."""

import asyncio
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.pool import NullPool
from schedence_db.runtime import run
from schedence_auth.testing import TokenFactory

from schedence_teaching.api.identity import IdentityPermissionsClient
from schedence_teaching.api.runtime import TeachingRuntime
from schedence_teaching.api.store import DatabaseTimetableStore
from schedence_teaching.main import create_app

from test_timetable_persistence_isolated import isolated_engine,setup,save
from test_persistence_contracts import TENANT,OTHER_TENANT,example

pytestmark=pytest.mark.skipif(os.environ.get('SCHEDENCE_TEACHING_ISOLATED_DB_TESTS')!='1',
    reason='SQL API tests use the explicit isolated localhost database only')


def test_real_sql_api_pagination_filters_rls_and_read_only_statements():
    async def seed():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            receipt=await save(factory,series,snapshot,result)
            changed=document.model_copy(deep=True);changed.sources[0].provenance['policy']='SYNTHETIC_API_V2'
            from schedence_teaching.persistence.repositories import teaching_unit_of_work
            async with teaching_unit_of_work(factory,TENANT) as repository:next_snapshot=await repository.store_snapshot(changed)
            second=await save(factory,series,next_snapshot,result,receipt.version_id)
            return engine.url,series,receipt,second
        finally:await engine.dispose()
    url,series,first,second=run(seed())
    # TestClient has its own loop; NullPool prevents cross-loop connection reuse.
    engine=create_async_engine(url,poolclass=NullPool,hide_parameters=True,echo=False)
    statements=[]
    @event.listens_for(engine.sync_engine,'before_cursor_execute')
    def capture(connection,cursor,statement,parameters,context,executemany):
        statements.append(statement.lstrip().split()[0].upper())
    tokens=TokenFactory();subject=str(uuid4());user=uuid4()
    profile=dict(user_id=str(user),cognito_sub=subject,account_status='ACTIVE',institutions=[
        dict(institution_id=str(TENANT),roles=['ACADEMIC_ADMIN'],permissions=['teaching:generate']),
        dict(institution_id=str(OTHER_TENANT),roles=['ACADEMIC_ADMIN'],permissions=['teaching:generate'])])
    http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=profile)))
    runtime=TeachingRuntime(tokens.verifier(),IdentityPermissionsClient('http://identity.test',http),
        DatabaseTimetableStore(async_sessionmaker(engine,expire_on_commit=False)),shutdown_hooks=(http.aclose,engine.dispose))
    headers={'Authorization':'Bearer '+tokens.access_token(subject)}
    query=dict(institution_id=str(TENANT),academic_year=4,semester=1,stream='WEEKEND',specialization='IT',
        student_group='A',lecturer='SYN-A',venue='SYN-V0',lifecycle_status='VALIDATED',page_size=1)
    with TestClient(create_app(runtime),raise_server_exceptions=False,
        backend_options={'loop_factory':asyncio.SelectorEventLoop}) as client:
        path=f'/api/teaching/timetables/{first.version_id}'
        detail=client.get(path,headers=headers,params={'institution_id':str(TENANT)})
        assert detail.status_code==200,detail.text
        assigned=client.get(path+'/sessions',headers=headers,params={'institution_id':str(TENANT),'lecturer':'SYN-A'})
        assert assigned.status_code==200 and assigned.json()['total']==1,assigned.text
        query['venue']=assigned.json()['items'][0]['venue']['venue_id']
        for page in (1,2):
            response=client.get(path+'/versions',params=dict(query,page=page),headers=headers)
            assert response.status_code==200,response.text
            assert response.json()['total']==2 and len(response.json()['items'])==1
        response=client.get(path+'/sessions',params=dict(query),headers=headers)
        assert response.status_code==200 and response.json()['total']==1,response.text
        assert response.json()['items'][0]['coverage'][0]['group_id']=='A'
        validation=client.get(path+'/validation',headers=headers,params={'institution_id':str(TENANT)})
        assert validation.status_code==200 and validation.json()['hard_violations']==0
        # Authorized in both institutions does not bypass record tenant isolation.
        assert client.get(path,headers=headers,params={'institution_id':str(OTHER_TENANT)}).status_code==404
        assert client.get('/api/teaching/timetables',headers=headers,
            params={'institution_id':str(TENANT),'lifecycle_status':'PUBLISHED'}).json()['total']==0
        assert client.get(path+'/versions',headers=headers,params=dict(query,lifecycle_status='DRAFT')).json()['total']==0
    assert statements and set(statements)<={'SELECT','SET'}
