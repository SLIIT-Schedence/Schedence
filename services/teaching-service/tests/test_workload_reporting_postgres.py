"""Report HTTP reads exercise real tenant RLS on isolated synthetic PostgreSQL."""
import asyncio
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from schedence_db.runtime import run
from schedence_auth.testing import TokenFactory

from schedence_teaching.api.identity import IdentityPermissionsClient
from schedence_teaching.api.runtime import TeachingRuntime
from schedence_teaching.api.store import DatabaseTimetableStore
from schedence_teaching.main import create_app
from schedence_teaching.persistence.timetable_service import timetable_reader
from test_timetable_persistence_isolated import isolated_engine, setup, save
from test_persistence_contracts import TENANT, OTHER_TENANT, example

pytestmark=pytest.mark.skipif(os.environ.get('SCHEDENCE_TEACHING_ISOLATED_DB_TESTS')!='1',
    reason='Explicit isolated loopback PostgreSQL only; never production writes')


def test_workload_sql_report_rls_read_only_and_no_timetable_mutation():
    async def seed():
        engine=isolated_engine();factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            document,result=example();series,snapshot=await setup(factory,document)
            receipt=await save(factory,series,snapshot,result)
            async with timetable_reader(factory,TENANT) as repository:
                assert await repository._session.scalar(text('SHOW transaction_read_only'))=='on'
                record=await repository.get_timetable(receipt.version_id)
                before=(record.version.copy(),record.deliveries,record.coverage)
            return engine.url,receipt,before
        finally:await engine.dispose()
    url,receipt,before=run(seed())
    engine=create_async_engine(url,poolclass=NullPool,hide_parameters=True,echo=False)
    factory=async_sessionmaker(engine,expire_on_commit=False);statements=[]
    @event.listens_for(engine.sync_engine,'before_cursor_execute')
    def capture(connection,cursor,statement,parameters,context,executemany):
        statements.append(statement.lstrip().split()[0].upper())
    tokens=TokenFactory();subject=str(uuid4())
    profile=dict(user_id=str(uuid4()),cognito_sub=subject,account_status='ACTIVE',institutions=[
        dict(institution_id=str(t),roles=['ACADEMIC_ADMIN'],permissions=['teaching:timetables:read']) for t in (TENANT,OTHER_TENANT)])
    http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=profile)))
    runtime=TeachingRuntime(tokens.verifier(),IdentityPermissionsClient('http://identity.test',http),
        DatabaseTimetableStore(factory),shutdown_hooks=(http.aclose,engine.dispose))
    headers={'Authorization':'Bearer '+tokens.access_token(subject)}
    params=dict(institution_id=str(TENANT),version_id=str(receipt.version_id))
    base='/api/teaching/reports/lecturer-workload'
    with TestClient(create_app(runtime),raise_server_exceptions=False,
        backend_options={'loop_factory':asyncio.SelectorEventLoop}) as client:
        response=client.get(base,headers=headers,params=params)
        assert response.status_code==200,response.text
        assert response.json()['totals']['assigned_teaching_minutes']==120
        response=client.get(base+'/SYN-A',headers=headers,params=params)
        assert response.status_code==200 and response.json()['totals']['assigned_teaching_minutes']==60
        response=client.get(base+'/export',headers=headers,params=dict(params,format='csv',kind='assignments'))
        assert response.status_code==200 and response.content.count(b'SAVED_RESPONSIBLE_STAFF_ASSIGNMENT')==2
        assert client.get(base,headers=headers,params=dict(params,institution_id=str(OTHER_TENANT))).status_code==404
        async def verify():
            async with timetable_reader(factory,TENANT) as repository:
                assert await repository._session.scalar(text('SHOW transaction_read_only'))=='on'
                record=await repository.get_timetable(receipt.version_id)
                assert (record.version,record.deliveries,record.coverage)==before
        client.portal.call(verify)
    assert statements and set(statements)<={'SELECT','SET','SHOW'}
