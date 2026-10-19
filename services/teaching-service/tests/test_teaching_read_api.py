"""Real shared JWT verification + trusted mock Identity contract; no production writes."""

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from schedence_auth.testing import TokenFactory, SigningKey
from schedence_teaching.api.authorization import AcademicBinding, MissingAcademicBindings, READ, READ_SELF, READ_SELF_DRAFTS, READ_PUBLISHED
from schedence_teaching.api.identity import IdentityPermissionsClient
from schedence_teaching.api.runtime import TeachingRuntime
from schedence_teaching.main import create_app
from schedence_teaching.persistence.lifecycle import academic_limitations
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.retrieval import assemble_timetable

from test_timetable_versioning import record_for
from test_persistence_contracts import TENANT, OTHER_TENANT, example

ROOT=Path(__file__).resolve().parents[3]
BASE='/api/teaching/timetables'


def make_record(*,number=1,series=None,tenant=TENANT,published=False,mode='PHYSICAL'):
    document,result=example(mode)
    document.institution_id=tenant
    document,version,run,report,bookings,coverage=record_for(document,result)
    version.update(id=uuid4(),series_id=series or uuid4(),version_number=number,academic_complete=False)
    run.update(snapshot_id=uuid4(),solver_status=result.status.value,runtime_seconds=result.runtime_seconds)
    record=assemble_timetable(document,version,run,report,bookings,coverage)
    if published:
        # Hypothetical future published fixture, not a production publication path.
        document=document.model_copy(deep=True)
        document.calendar_label='SUPPLIED_CALENDAR'
        document.calendar.synthetic_data=False
        document.issues=()
        document.sources[0].provenance_kind=type(document.sources[0].provenance_kind)('UNIVERSITY_SOURCED')
        document=type(document).model_validate(document.model_dump())
        record=replace(record,document=document,version=dict(version,lifecycle_status='PUBLISHED',academic_complete=True),
            logical_status='PUBLISHED',limitations=academic_limitations(document))
    return record


class MemoryStore:
    def __init__(self,records):self.records=records;self.calls=[]

    @asynccontextmanager
    async def read(self,institution):
        self.calls.append(institution)
        store=self
        class Repository:
            async def get_timetable(self,version_id):
                row=next((row for row in store.records if row.version['id']==version_id and row.document.institution_id==institution),None)
                if row is None:raise PersistenceConflict('not found')
                return row

            async def find_timetables(self,offset=0,limit=20,series_id=None,**filters):
                selected=[]
                for row in store.records:
                    if row.document.institution_id!=institution or series_id and row.version['series_id']!=series_id:continue
                    if filters['lifecycle_status'] and filters['lifecycle_status']!=row.logical_status:continue
                    scopes=[scope for scope in row.document.scopes if all(not filters[key] or value in filters[key] for key,value in (
                        ('years',scope.academic_year),('semesters',scope.semester),('streams',scope.stream.value),('specializations',scope.specialization)))]
                    if filters['groups']:scopes=[scope for scope in scopes if set(scope.group_references)&set(filters['groups'])]
                    if not scopes:continue
                    if filters['lecturer'] and not any(item['staff_reference']==filters['lecturer'] for item in row.deliveries):continue
                    if filters['venue'] and not any(item['venue_reference']==filters['venue'] for item in row.deliveries):continue
                    selected.append(row)
                selected.sort(key=lambda row:row.version['version_number'],reverse=True)
                return tuple(selected[offset:offset+limit]),len(selected)
        yield Repository()


class Bindings:
    def __init__(self):self.values={}
    async def resolve(self,principal,institution):return self.values.get((principal.user_id,institution))


@pytest.fixture
def api():
    tokens=TokenFactory();subject=str(uuid4());user=uuid4()
    first=make_record();second=make_record(number=2,series=first.version['series_id'],mode='ONLINE')
    other=make_record(tenant=OTHER_TENANT)
    records=[first,second,other]
    store=MemoryStore(records);bindings=Bindings()
    profile=dict(user_id=str(user),cognito_sub=subject,account_status='ACTIVE',
        institutions=[dict(institution_id=str(TENANT),roles=['ACADEMIC_ADMIN'],permissions=['teaching:generate','teaching:approve'])])
    identity=dict(profile=profile,status=200,calls=0)
    async def handler(request):
        identity['calls']+=1
        assert request.url.path=='/api/v1/identity/me/permissions'
        assert request.headers['Authorization'].startswith('Bearer ')
        return httpx.Response(identity['status'],json=identity['profile'])
    http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runtime=TeachingRuntime(tokens.verifier(),IdentityPermissionsClient('http://identity.test',http),store,bindings,
        shutdown_hooks=(http.aclose,))
    with TestClient(create_app(runtime),raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client,tokens=tokens,subject=subject,user=user,profile=profile,identity=identity,
            records=records,store=store,bindings=bindings,runtime=runtime,
            headers={'Authorization':'Bearer '+tokens.access_token(subject)})


def grant(api,role,permissions=()):
    api.profile['institutions'][0].update(roles=[role],permissions=list(permissions))


def bind(api,**values):
    if 'group_ids' in values:
        for name,value in dict(academic_years=frozenset({4}),semesters=frozenset({1}),
            streams=frozenset({'WEEKEND'}),specializations=frozenset({'IT'})).items():values.setdefault(name,value)
    result=AcademicBinding(api.user,TENANT,'SYNTHETIC_TRUSTED_OWNER_MAPPING',**values)
    api.bindings.values[(api.user,TENANT)]=result
    return result


def test_admin_can_read_all_five_endpoints_and_both_route_prefixes(api):
    first=api.records[0];identifier=first.version['id']
    for path in [BASE,BASE+f'/{identifier}',BASE+f'/{identifier}/sessions',BASE+f'/{identifier}/validation',BASE+f'/{identifier}/versions',
        '/api/v1/teaching/timetables']:
        response=api.client.get(path,headers=api.headers)
        assert response.status_code==200,response.text
        assert response.headers['Cache-Control']=='no-store'
        assert response.headers['X-Content-Type-Options']=='nosniff'
    assert set(api.store.calls)=={TENANT}


@pytest.mark.parametrize('suffix',['','/sessions','/validation','/versions'])
def test_missing_authentication_rejected_before_identity_or_database(api,suffix):
    response=api.client.get(BASE+f"/{api.records[0].version['id']}"+suffix)
    assert response.status_code==401 and response.headers['WWW-Authenticate']=='Bearer'
    assert not api.store.calls and api.identity['calls']==0


@pytest.mark.parametrize('kind',['expired','wrong_issuer','wrong_client','id_token','bad_signature','malformed'])
def test_invalid_or_expired_tokens_cannot_reach_identity_or_database(api,kind):
    options={}
    if kind=='expired':options['expires_in']=-30
    if kind=='wrong_issuer':options['iss']='https://untrusted.invalid'
    if kind=='wrong_client':options['client_id']='differentclient'
    if kind=='id_token':options['token_use']='id'
    if kind=='bad_signature':options['key']=SigningKey.generate()
    token='not-a-token' if kind=='malformed' else api.tokens.access_token(api.subject,**options)
    response=api.client.get(BASE,headers={'Authorization':'Bearer '+token})
    assert response.status_code==401,response.text
    assert not api.store.calls and api.identity['calls']==0


@pytest.mark.parametrize('role',['EXAMINATION_ADMIN','VENUE_MANAGER','INSTITUTION_ADMIN','HOD','PROGRAM_COORDINATOR','PROFESSOR'])
def test_other_roles_and_academic_titles_do_not_imply_teaching_access(api,role):
    grant(api,role,['academic:staff:read','academic:workload:read','venues:read'])
    assert api.client.get(BASE,headers=api.headers).status_code==403
    assert not api.store.calls


def test_spoofed_jwt_groups_roles_institution_and_headers_are_not_authority(api):
    grant(api,'STUDENT')
    token=api.tokens.access_token(api.subject,roles=['ACADEMIC_ADMIN'],**{'cognito:groups':['ACADEMIC_ADMIN'],'institution_id':str(TENANT)})
    headers={'Authorization':'Bearer '+token,'X-Role':'ACADEMIC_ADMIN','X-Institution-ID':str(TENANT),'X-Permissions':READ}
    assert api.client.get(BASE,headers=headers).status_code==403
    assert not api.store.calls


def test_explicit_identity_permission_can_authorize_cross_service_read(api):
    grant(api,'EXAMINATION_ADMIN',[READ])
    assert api.client.get(BASE,headers=api.headers).status_code==200


def test_existing_identity_catalogue_is_the_role_authority():
    catalog=runpy.run_path(str(ROOT/'services/identity-service/app/rbac/catalog.py'))
    roles={role.code:role for role in catalog['ROLES']}
    assert {'teaching:generate','teaching:approve'}<=roles['ACADEMIC_ADMIN'].permissions
    for code in ('SYSTEM_ADMIN','INSTITUTION_ADMIN'):
        assert not any(value.startswith('teaching:') for value in roles[code].permissions)
    assert 'HOD' not in roles and 'PROGRAM_COORDINATOR' not in roles


def test_platform_system_admin_grants_no_institution_teaching_access(api):
    # Identity lists System Admin only under `platform`, never inside an institution.
    api.profile.update(institutions=[],platform=dict(roles=['SYSTEM_ADMIN'],
        permissions=['platform:institutions:create','platform:institutions:read']))
    for params in ({},{'institution_id':str(TENANT)}):
        assert api.client.get(BASE,params=params,headers=api.headers).status_code==403
    assert not api.store.calls


def test_cross_institution_query_is_denied_before_database_read(api):
    response=api.client.get(BASE,params={'institution_id':str(OTHER_TENANT)},headers=api.headers)
    assert response.status_code==403 and not api.store.calls


def test_cross_institution_version_and_missing_version_return_same_404(api):
    response=api.client.get(BASE+f"/{api.records[2].version['id']}",headers=api.headers)
    missing=api.client.get(BASE+f'/{uuid4()}',headers=api.headers)
    assert response.status_code==missing.status_code==404
    assert response.json()['error']['message']==missing.json()['error']['message']


def test_multiple_authorized_institutions_require_explicit_selection(api):
    api.profile['institutions'].append(dict(institution_id=str(OTHER_TENANT),roles=['ACADEMIC_ADMIN'],permissions=[READ]))
    assert api.client.get(BASE,headers=api.headers).status_code==422
    response=api.client.get(BASE,params={'institution_id':str(OTHER_TENANT)},headers=api.headers)
    assert response.status_code==200 and response.json()['total']==1
    assert api.store.calls==[OTHER_TENANT]


def test_role_revocation_is_effective_on_the_next_request(api):
    assert api.client.get(BASE,headers=api.headers).status_code==200
    grant(api,'STUDENT')
    assert api.client.get(BASE,headers=api.headers).status_code==403
    assert api.identity['calls']==2


@pytest.mark.parametrize('problem',['disabled','wrong_subject','duplicate_institution','invalid_schema','upstream_401','upstream_403','redirect'])
def test_identity_contract_failures_are_closed(api,problem):
    expected=503
    if problem=='disabled':api.profile['account_status']='DISABLED';expected=403
    elif problem=='wrong_subject':api.profile['cognito_sub']='another-subject'
    elif problem=='duplicate_institution':api.profile['institutions'].append(deepcopy(api.profile['institutions'][0]))
    elif problem=='invalid_schema':api.profile['institutions'][0]['permissions']='teaching:generate'
    elif problem=='upstream_401':api.identity['status']=401;expected=401
    elif problem=='upstream_403':api.identity['status']=403;expected=403
    else:api.identity['status']=302
    response=api.client.get(BASE,headers=api.headers)
    assert response.status_code==expected and not api.store.calls


def test_pagination_filters_and_dashboard_provenance(api):
    response=api.client.get(BASE,params={'page':2,'page_size':1,'academic_year':4,'semester':1,'stream':'WEEKEND',
        'specialization':'IT','lifecycle_status':'VALIDATED'},headers=api.headers)
    body=response.json()
    assert response.status_code==200 and body['total']==2 and len(body['items'])==1
    assert body['items'][0]['version_number']==1
    item=body['items'][0]
    assert item['calendar']['label']=='PP1_EXPERIMENTAL_CALENDAR'
    assert item['lifecycle_status']=='VALIDATED' and item['storage_status']=='DRAFT_VALIDATED'
    assert item['academic_complete'] is False and item['input_snapshot_id']
    assert item['unresolved_issues'][0]['code']=='RESEARCH_PENDING_SESSION_MAPPING'
    assert item['policy_sources'] and item['full_scope_expected_occurrences']==2
    assert api.client.get(BASE,params={'academic_year':1},headers=api.headers).json()['total']==0
    assert api.client.get(BASE,params={'lifecycle_status':'PUBLISHED'},headers=api.headers).json()['total']==0


@pytest.mark.parametrize('params',[{'page':0},{'page_size':101},{'academic_year':5},{'semester':3},{'stream':'EVENING'},
    {'lifecycle_status':'DRAFT_VALIDATED'},{'role':'ACADEMIC_ADMIN'}])
def test_invalid_filter_values_are_rejected_without_database(api,params):
    assert api.client.get(BASE,params=params,headers=api.headers).status_code==422
    assert not api.store.calls


def test_session_filters_contact_hours_and_identifiers_are_preserved(api):
    record=api.records[0];version=record.version['id']
    response=api.client.get(BASE+f'/{version}/sessions',params={'lecturer':'SYN-A','student_group':'A'},headers=api.headers)
    body=response.json()
    assert response.status_code==200 and body['total']==1
    session=body['items'][0]
    assert session['lecturer']['staff_id']=='SYN-A' and session['delivery_mode']=='PHYSICAL'
    assert session['coverage'][0]['duration_minutes']==60 and session['coverage'][0]['offering_id']=='O-A'
    assert session['venue'] and session['start_time']=='08:30'
    all_sessions=api.client.get(BASE+f'/{version}/sessions',headers=api.headers).json()['items']
    assert {item['coverage'][0]['offering_id'] for item in all_sessions}=={'O-A','O-B'}
    assert {item['coverage'][0]['module_code'] for item in all_sessions}=={'SYN'}
    venue=session['venue']['venue_id']
    assert api.client.get(BASE+f'/{version}/sessions',params={'venue':venue},headers=api.headers).json()['total']==1


def test_responses_never_include_rosters_preferences_or_raw_snapshots(api):
    version=api.records[0].version['id']
    for suffix in ['',f'/{version}',f'/{version}/sessions',f'/{version}/validation',f'/{version}/versions']:
        text=api.client.get(BASE+suffix,headers=api.headers).text
        for forbidden in ['PP1_TEST:A1','student_ids','document_json','result_metadata','workload_assessment','normalized_deliveries_sha256']:
            assert forbidden not in text


def test_online_response_without_any_venue_contract_is_explicitly_venue_free(api):
    from test_online_attendance_solver import problem
    from test_persistence_contracts import snapshot_for
    from schedence_teaching.solver import solve_timetable
    prepared,config,_=problem(mode='ONLINE')
    document=snapshot_for(prepared,config)
    result=solve_timetable(prepared,config)
    doc,version,run,report,bookings,coverage=record_for(document,result)
    version.update(id=uuid4(),series_id=uuid4(),version_number=1,academic_complete=False)
    run.update(snapshot_id=uuid4(),solver_status=result.status.value)
    record=assemble_timetable(doc,version,run,report,bookings,coverage)
    api.records.append(record)
    response=api.client.get(BASE+f"/{version['id']}/sessions",headers=api.headers)
    assert response.status_code==200,response.text
    assert all(item['delivery_mode']=='ONLINE' and item['venue'] is None for item in response.json()['items'])


def test_lecturer_without_binding_or_additional_draft_grant_cannot_access_drafts(api):
    grant(api,'LECTURER',[READ_SELF])
    assert api.client.get(BASE,headers=api.headers).status_code==403
    bind(api,staff_id='SYN-A')
    assert api.client.get(BASE,headers=api.headers).json()['total']==0
    assert api.client.get(BASE+f"/{api.records[0].version['id']}",headers=api.headers).status_code==404
    assert api.client.get(BASE,params={'lifecycle_status':'VALIDATED'},headers=api.headers).status_code==403


def test_explicit_personal_draft_permission_only_returns_own_assignments(api):
    grant(api,'LECTURER',[READ_SELF,READ_SELF_DRAFTS]);bind(api,staff_id='SYN-A')
    version=api.records[0].version['id']
    response=api.client.get(BASE+f'/{version}/sessions',headers=api.headers)
    assert response.status_code==200 and response.json()['total']==1
    assert all(item['lecturer']['staff_id']=='SYN-A' for item in response.json()['items'])
    detail=api.client.get(BASE+f'/{version}',headers=api.headers).json()
    assert 'policy_sources' not in detail and 'full_scope_expected_occurrences' not in detail
    assert api.client.get(BASE+f'/{version}/sessions',params={'lecturer':'SYN-B'},headers=api.headers).status_code==403
    for suffix in ['/validation','/versions']:
        assert api.client.get(BASE+f'/{version}'+suffix,headers=api.headers).status_code==403


def test_wrong_user_academic_binding_is_rejected(api):
    grant(api,'LECTURER',[READ_SELF,READ_SELF_DRAFTS])
    api.bindings.values[(api.user,TENANT)]=AcademicBinding(uuid4(),TENANT,'SYNTHETIC',staff_id='SYN-A')
    assert api.client.get(BASE,headers=api.headers).status_code==403 and not api.store.calls


def test_student_group_id_alone_is_not_a_complete_trusted_academic_scope(api):
    grant(api,'STUDENT',[READ_PUBLISHED])
    api.bindings.values[(api.user,TENANT)]=AcademicBinding(api.user,TENANT,'SYNTHETIC_OWNER',group_ids=frozenset({'A'}))
    assert api.client.get(BASE,headers=api.headers).status_code==403 and not api.store.calls


def test_student_can_only_view_applicable_published_records(api):
    grant(api,'STUDENT',[READ_PUBLISHED]);bind(api,group_ids=frozenset({'A'}),academic_years=frozenset({4}),
        semesters=frozenset({1}),streams=frozenset({'WEEKEND'}),specializations=frozenset({'IT'}))
    assert api.client.get(BASE,headers=api.headers).json()['total']==0
    private=api.records[0].version['id']
    assert api.client.get(BASE+f'/{private}',headers=api.headers).status_code==404
    public=make_record(published=True);api.records.append(public)
    response=api.client.get(BASE,headers=api.headers)
    assert response.status_code==200 and response.json()['total']==1
    item=response.json()['items'][0]
    assert item['lifecycle_status']=='PUBLISHED' and 'input_snapshot_id' not in item
    sessions=api.client.get(BASE+f"/{public.version['id']}/sessions",headers=api.headers).json()
    assert sessions['total']==1 and sessions['items'][0]['coverage'][0]['group_id']=='A'
    assert api.client.get(BASE,params={'student_group':'B'},headers=api.headers).status_code==403
    assert api.client.get(BASE,params={'academic_year':3},headers=api.headers).status_code==403


def test_experimental_record_cannot_be_exposed_as_published_even_with_forged_status_metadata(api):
    grant(api,'STUDENT',[READ_PUBLISHED]);bind(api,group_ids=frozenset({'A'}))
    original=api.records[0]
    api.records[0]=replace(original,logical_status='PUBLISHED',version=dict(original.version,lifecycle_status='PUBLISHED',academic_complete=True))
    assert api.client.get(BASE+f"/{original.version['id']}",headers=api.headers).status_code==404
    assert api.client.get(BASE,headers=api.headers).json()['total']==0


@pytest.mark.parametrize('method',['post','patch','delete'])
def test_no_timetable_management_routes_exist_even_for_venue_manager(api,method):
    grant(api,'VENUE_MANAGER',['venues:manage'])
    response=getattr(api.client,method)(BASE+f"/{api.records[0].version['id']}",headers=api.headers)
    assert response.status_code==405 and not api.store.calls


def test_openapi_preserves_read_routes_and_existing_health(api):
    schema=api.client.get('/openapi.json').json()
    assert len([path for path in schema['paths'] if path.startswith('/api/v1/teaching/timetables')])==5
    assert all(set(operations)=={'get'} for path,operations in schema['paths'].items() if '/timetables' in path)
    assert set(schema['paths']['/api/v1/teaching/generations'])=={'post'}
    assert set(schema['paths']['/api/v1/teaching/generations/{job_id}'])=={'get'}
    assert api.client.get('/health').json()=={'status':'ok','service':'teaching-timetable'}
