"""Portable owner HTTP integration; no live DB, local-data, CP-SAT or external HTTP."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from schedence_teaching.api.errors import ApiError
from schedence_teaching.api.owner_http import OwnerApiClient
from schedence_teaching.api.runtime import TeachingSettings
from schedence_teaching.forms.http_directory import ApiLecturerDirectory
from schedence_teaching.generation.http_readiness import ApiGenerationReadiness, DatabasePreparedDocuments, owner_rows
from schedence_teaching.generation.readiness import ReadinessScope
from readiness_fixtures import api, forbid_solver
from test_database_generation import database_fixture
from test_elective_enrollment import elective_fixture
from test_persistence_contracts import TENANT, OTHER_TENANT


def export(owner, rows):
    return json.loads(json.dumps(dict(institution_id=str(TENANT), complete=True, tables=rows), default=str))


def test_http_owner_inputs_reuse_existing_pre_solve_without_private_files(database_fixture):
    evidence, _ = database_fixture
    calls = []

    async def scenario():
        async def handler(request):
            calls.append(request)
            assert request.method == 'GET' and request.headers['Authorization'] == 'Bearer caller-one'
            assert request.url.params['institution_id'] == str(TENANT)
            owner = 'academic' if request.url.host == 'academic.test' else 'resource'
            return httpx.Response(200, json=export(owner, getattr(evidence, owner)))
        class Prepared:
            async def get(self, institution, semester):
                assert institution == TENANT and semester == 1
                return evidence.document
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            owners = OwnerApiClient(http, 'https://academic.test', 'https://resource.test')
            readiness = ApiGenerationReadiness(owners, Prepared(), academic_path='/api/v1/academic/teaching-inputs',
                resource_path='/api/v1/resource/teaching-inputs')
            result = await readiness.assess(TENANT, ReadinessScope(1), bearer_token='caller-one')
            assert result['ready'], result['checks']
            assert result['inputs']['expected_sessions'] > 0
    asyncio.run(scenario())
    assert len(calls) == 2


@pytest.mark.parametrize('status,expected', [(401,401),(403,403),(404,503),(302,503),(500,503)])
def test_owner_errors_and_redirects_fail_closed_without_leaking_body(status, expected):
    async def scenario():
        calls=[]
        async def handler(request):
            calls.append(request)
            return httpx.Response(status, text='secret token host=private', headers={'Location':'https://attacker.test'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            owner=OwnerApiClient(http,'https://academic.test','https://resource.test')
            with pytest.raises(ApiError) as error:
                await owner.get('academic','/api/v1/academic/teaching-inputs',TENANT,'caller')
            assert error.value.status == expected
            assert 'secret' not in error.value.message and len(calls)==1
    asyncio.run(scenario())


def test_tokens_are_forwarded_per_call_without_caching_or_owner_logins():
    async def scenario():
        tokens=[]
        async def handler(request):
            tokens.append(request.headers['Authorization'])
            return httpx.Response(200,json={'institution_id':str(TENANT)})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            owner=OwnerApiClient(http,'https://academic.test','https://resource.test')
            await owner.get('academic','/api/test',TENANT,'first')
            await owner.get('academic','/api/test',TENANT,'second')
            assert tokens==['Bearer first','Bearer second']
            assert not any('first' == value or 'second' == value for value in vars(owner).values())
    asyncio.run(scenario())


@pytest.mark.parametrize('origin',['http://academic.example','https://user:password@academic.example','https://academic.example/path'])
def test_configured_owner_origin_cannot_leak_bearer_credentials(origin):
    with pytest.raises(ValueError): OwnerApiClient(object(),origin,'https://resource.test')


def test_cross_tenant_owner_envelope_is_rejected():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'institution_id':str(OTHER_TENANT)}))) as http:
            with pytest.raises(ApiError):
                await OwnerApiClient(http,'https://academic.test','https://resource.test').get('academic','/api/test',TENANT,'caller')
    asyncio.run(scenario())


def test_partial_or_cross_tenant_owner_rows_are_rejected(database_fixture):
    evidence,_=database_fixture
    payload=export('academic',evidence.academic)
    payload['complete']=False
    with pytest.raises(ApiError): owner_rows(payload,'academic',TENANT)
    payload['complete']=True
    payload['tables']['staff'][0]['institution_id']=str(OTHER_TENANT)
    with pytest.raises(ApiError): owner_rows(payload,'academic',TENANT)


def test_missing_snapshot_is_reported_as_missing_never_fabricated(database_fixture):
    evidence,_=database_fixture
    class Owners:
        async def get(self,owner,*args,**kwargs): return export(owner,getattr(evidence,owner))
    class Prepared:
        async def get(self,*args): return None
    async def scenario():
        ready=ApiGenerationReadiness(Owners(),Prepared(),academic_path='/api/academic',resource_path='/api/resource')
        result=await ready.assess(TENANT,ReadinessScope(1),bearer_token='caller')
        assert not result['ready'] and result['inputs'] is None
        assert result['checks'][0]['key']=='prepared-input'
    asyncio.run(scenario())


def test_authenticated_route_forwards_the_same_verified_token(api):
    class Ready:
        requires_bearer_token=True
        async def assess(self,institution,scope,rules,*,bearer_token):
            assert bearer_token==api.headers['Authorization'].removeprefix('Bearer ')
            return dict(ready=False,checks=[],inputs=None)
    api.runtime.readiness=Ready()
    assert api.client.get('/api/v1/teaching/generation-inputs',params={'semester':1},headers=api.headers).status_code==200


def test_configuration_failures_keep_timetables_and_form_drafts_available(monkeypatch,tmp_path,caplog):
    import schedence_db
    import schedence_teaching.api.runtime as module
    from schedence_teaching.main import create_app
    from schedence_teaching.api.identity import IdentityPermissionsClient
    from schedence_auth.testing import TokenFactory
    from test_teaching_read_api import MemoryStore
    env=tmp_path/'.env'
    env.write_text('DATABASE_URL=postgresql://runtime@localhost/shared\nAPP_ENV=production\n')
    settings=TeachingSettings.model_construct(app_env='production')
    monkeypatch.setenv('SCHEDENCE_ENV_FILE',str(env))
    monkeypatch.setattr(module,'load_settings_model',lambda *args:settings)
    tokens=TokenFactory()
    monkeypatch.setattr(module,'build_verifier',lambda settings:tokens.verifier())
    async def dispose(): pass
    monkeypatch.setattr(schedence_db,'load_settings',lambda **kwargs:object())
    monkeypatch.setattr(schedence_db,'create_db_engine',lambda *args,**kwargs:SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(schedence_db,'create_session_factory',lambda engine:object())
    def unavailable(*args,**kwargs): raise ValueError('password=must-not-log')
    monkeypatch.setattr(module,'readiness_runtime',unavailable)
    monkeypatch.setattr(module,'configure_form_sending',unavailable)
    runtime=module.build_runtime()
    assert runtime.readiness is None and runtime.form_sending is None and runtime.forms is not None
    assert 'must-not-log' not in caplog.text
    user,subject=uuid4(),str(uuid4())
    async def permissions(request):
        return httpx.Response(200,json=dict(user_id=str(user),cognito_sub=subject,account_status='ACTIVE',institutions=[
            dict(institution_id=str(TENANT),roles=['ACADEMIC_ADMIN'],permissions=['teaching:timetables:read','teaching:generate','academic:staff:read'])]))
    http=httpx.AsyncClient(transport=httpx.MockTransport(permissions))
    runtime.permissions=IdentityPermissionsClient('https://identity.test',http)
    runtime.shutdown_hooks+=(http.aclose,)
    runtime.store=MemoryStore([])
    class Forms:
        async def list(self,*args): return (),0
    runtime.forms=Forms()
    headers={'Authorization':'Bearer '+tokens.access_token(subject)}
    with TestClient(create_app(runtime)) as client:
        assert client.get('/api/v1/teaching/timetables',params={'institution_id':str(TENANT)},headers=headers).status_code==200
        assert client.get('/api/v1/teaching/form-templates',params={'institution_id':str(TENANT)},headers=headers).status_code==200
        r=client.get('/api/v1/teaching/generation-inputs',params={'semester':1},headers=headers)
        assert r.status_code==503 and r.json()['error']['code']=='READINESS_UNAVAILABLE'


def test_lecturer_identity_lookup_uses_self_endpoint_not_admin_roster():
    user,staff=uuid4(),uuid4()
    class Owners:
        async def get(self,owner,path,institution,token,**kwargs):
            assert path=='/api/self' and token=='caller' and institution==TENANT
            return dict(institution_id=str(TENANT),complete=True,items=[dict(staff_id=str(staff),staff_number='L1',
                full_name='Synthetic Lecturer',identity_user_id=str(user),link_status='READY')])
    async def scenario():
        rows=await ApiLecturerDirectory(Owners(),'/api/roster','/api/self').eligible(TENANT,bearer_token='caller',for_user=user)
        assert len(rows)==1 and rows[0].identity_user_id==user
        with pytest.raises(ApiError):
            await ApiLecturerDirectory(Owners(),'/api/roster','/api/self').eligible(TENANT,bearer_token='caller',for_user=uuid4())
    asyncio.run(scenario())


def test_form_storage_rejects_owner_or_bypassrls_database_login():
    from schedence_teaching.forms.store import SqlFormTemplateStore
    class Session:
        bind = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        rolled_back = False
        closed = False
        async def execute(self,*args,**kwargs): pass
        async def scalar(self,*args,**kwargs): return False
        async def rollback(self): self.rolled_back=True
        async def close(self): self.closed=True
    session=Session()
    async def scenario():
        with pytest.raises(ApiError) as error:
            async with SqlFormTemplateStore(lambda:session).transaction(TENANT,read=True):
                pytest.fail('An elevated connection must never access Forms')
        assert error.value.code=='FORM_STORE_UNAVAILABLE' and session.rolled_back and session.closed
    asyncio.run(scenario())
