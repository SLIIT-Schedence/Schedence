"""Swagger and safe effective-permission inspection use existing shared auth."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from schedence_auth.testing import TokenFactory

from schedence_teaching.api.identity import IdentityPermissionsClient
from schedence_teaching.api.inspect_permissions import inspect_current_permissions
from test_teaching_read_api import api


def test_all_read_and_generation_aliases_are_visible_and_secured_in_swagger(api):
    response=api.client.get('/openapi.json')
    assert response.status_code==200
    schema=response.json()
    operation_ids=[]
    for prefix in ('/api/teaching','/api/v1/teaching'):
        for suffix,method in (('/timetables','get'),('/timetables/{version_id}','get'),
            ('/timetables/{version_id}/sessions','get'),('/timetables/{version_id}/validation','get'),
            ('/timetables/{version_id}/versions','get'),('/generations','post'),('/generations/{job_id}','get')):
            operation=schema['paths'][prefix+suffix][method]
            assert operation['security']==[{'BearerToken':[]}]
            operation_ids.append(operation['operationId'])
        post=schema['paths'][prefix+'/generations']['post']
        assert {'202','400','401','403','409','503'}<=post['responses'].keys()
        assert any(row['in']=='header' and row['name'].lower()=='idempotency-key' for row in post['parameters'])
    assert len(set(operation_ids))==14
    assert schema['components']['securitySchemes']['BearerToken']=={
        'type':'http','description':'Existing Cognito access token.','scheme':'bearer'}
    assert api.client.get('/docs').status_code==200
    assert '"persistAuthorization": false' in api.client.get('/docs').text
    properties=schema['components']['schemas']['GenerationJobOut']['properties']
    assert {'attempt_number','max_attempts','last_heartbeat_at','durability'}<=properties.keys()
    assert not {'lease_token','report','worker_id','request_sha256'} & properties.keys()


@pytest.mark.parametrize('permissions,generate,mode',[
    (['teaching:generate'],True,'ADMIN'),(['teaching:approve'],False,'ADMIN'),
    (['teaching:timetables:read'],False,'ADMIN'),([],False,None)])
def test_read_only_self_inspection_reports_effective_permissions_not_labels(permissions,generate,mode):
    async def execute():
        tokens=TokenFactory();subject=str(uuid4());user=uuid4();tenant=uuid4();token=tokens.access_token(subject)
        calls=[]
        async def identity(request):
            calls.append((request.method,request.url.path))
            return httpx.Response(200,json=dict(user_id=str(user),cognito_sub=subject,account_status='ACTIVE',
                institutions=[dict(institution_id=str(tenant),roles=['ACADEMIC_ADMIN'],permissions=permissions)]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(identity)) as http:
            output=await inspect_current_permissions(tokens.verifier(),IdentityPermissionsClient('https://identity.test',http),token)
        assert calls==[('GET','/api/v1/identity/me/permissions')]
        assert output['institutions'][0]['can_generate']==generate
        assert output['institutions'][0]['timetable_read_mode']==mode
        assert token not in json.dumps(output) and 'email' not in output
    asyncio.run(execute())


@pytest.mark.parametrize('kind',['expired','wrong-client','id-token'])
def test_self_inspection_rejects_invalid_auth_before_contacting_identity(kind):
    async def execute():
        tokens=TokenFactory();subject=str(uuid4());options={}
        if kind=='expired':options['expires_in']=-30
        if kind=='wrong-client':options['client_id']='wrong'
        if kind=='id-token':options['token_use']='id'
        class NoIdentity:
            async def principal_for(self,*args):pytest.fail('invalid token reached Identity')
        with pytest.raises(Exception):await inspect_current_permissions(tokens.verifier(),NoIdentity(),tokens.access_token(subject,**options))
    asyncio.run(execute())
