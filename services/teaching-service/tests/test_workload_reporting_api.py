"""Existing Cognito/Identity authority on private reporting endpoints."""
from uuid import uuid4

import pytest

from test_teaching_read_api import api, grant
from test_persistence_contracts import OTHER_TENANT
from schedence_teaching.api.authorization import READ, READ_SELF, READ_PUBLISHED

BASE='/api/teaching/reports/lecturer-workload'


def query(api,**extra):
    return dict(version_id=str(api.records[0].version['id']),**extra)


@pytest.mark.parametrize('prefix',['/api/teaching','/api/v1/teaching'])
def test_admin_report_and_individual_use_selected_saved_version(api,prefix):
    base=prefix+'/reports/lecturer-workload'
    result=api.client.get(base,headers=api.headers,params=query(api))
    assert result.status_code==200,result.text
    data=result.json()
    assert data['totals']['assigned_teaching_minutes']==120
    assert data['totals']['unique_actual_deliveries']==2
    assert result.headers['Cache-Control']=='no-store'
    individual=api.client.get(base+'/SYN-A',headers=api.headers,params=query(api))
    assert individual.status_code==200 and individual.json()['totals']['staff_count']==1
    # A different saved version is selected independently, never summed.
    other=api.client.get(base,headers=api.headers,params=dict(version_id=str(api.records[1].version['id'])))
    assert other.status_code==200 and other.json()['totals']['assigned_teaching_minutes']==120
    assert all(r['online_minutes']==60 for r in other.json()['staff'])


@pytest.mark.parametrize('suffix',['','/SYN-A','/export'])
@pytest.mark.parametrize('token',['missing','expired','malformed'])
def test_report_authentication_rejected_before_database(api,suffix,token):
    headers={} if token=='missing' else {'Authorization':'Bearer '+(
        'invalid' if token=='malformed' else api.tokens.access_token(api.subject,expires_in=-30))}
    response=api.client.get(BASE+suffix,headers=headers,params=query(api))
    assert response.status_code==401 and not api.store.calls


@pytest.mark.parametrize('permission',[None,READ_SELF,READ_PUBLISHED])
def test_labels_and_self_only_permissions_cannot_read_staff_report(api,permission):
    grant(api,'ACADEMIC_ADMIN',[] if permission is None else [permission])
    response=api.client.get(BASE,headers=api.headers,params=query(api))
    assert response.status_code==403 and not api.store.calls


def test_explicit_trusted_read_permission_not_title_is_authority(api):
    grant(api,'EXAMINATION_ADMIN',[READ])
    assert api.client.get(BASE,headers=api.headers,params=query(api)).status_code==200
    grant(api,'HOD',[])
    assert api.client.get(BASE,headers=api.headers,params=query(api)).status_code==403


def test_disabled_account_is_rejected(api):
    api.profile['account_status']='DISABLED'
    assert api.client.get(BASE,headers=api.headers,params=query(api)).status_code==403
    assert not api.store.calls


def test_cross_institution_filter_denied_before_database(api):
    assert api.client.get(BASE,headers=api.headers,params=query(api,institution_id=str(OTHER_TENANT))).status_code==403
    assert not api.store.calls


def test_cross_tenant_version_indistinguishable_from_missing(api):
    for version in [api.records[2].version['id'],uuid4()]:
        assert api.client.get(BASE,headers=api.headers,params={'version_id':str(version)}).status_code==404


@pytest.mark.parametrize('kind',['summary','assignments','targets','fairness'])
def test_csv_download_is_private_and_export_not_dynamic_staff_route(api,kind):
    response=api.client.get(BASE+'/export',headers=api.headers,params=query(api,format='csv',kind=kind))
    assert response.status_code==200,response.text
    assert response.headers['content-type'].startswith('text/csv')
    assert 'attachment;' in response.headers['content-disposition']
    assert response.headers['cache-control']=='no-store'
    assert str(api.records[0].version['id']) in response.content.decode('utf-8-sig')


def test_pdf_download(api):
    pytest.importorskip('reportlab')
    response=api.client.get(BASE+'/export',headers=api.headers,params=query(api,format='pdf',kind='summary'))
    assert response.status_code==200,response.text
    assert response.headers['content-type']=='application/pdf'
    assert response.content.startswith(b'%PDF-')


@pytest.mark.parametrize('params',[{}, {'semester':3},{'unknown':'secret'},{'format':'xlsx'},{'format':'pdf','kind':'fairness'}])
def test_missing_version_invalid_query_and_export_combination(api,params):
    values={} if not params else query(api,**params)
    path=BASE+'/export' if 'format' in params else BASE
    assert api.client.get(path,headers=api.headers,params=values).status_code==422


def test_unknown_staff_and_conflicting_filters(api):
    assert api.client.get(BASE+'/UNKNOWN',headers=api.headers,params=query(api)).status_code==404
    assert api.client.get(BASE+'/SYN-A',headers=api.headers,params=query(api,lecturer='SYN-B')).status_code==422


def test_swagger_reports_require_bearer_and_version(api):
    schema=api.client.get('/openapi.json').json()
    for prefix in ['/api/teaching','/api/v1/teaching']:
        for suffix in ['', '/export','/{staff_id}']:
            operation=schema['paths'][prefix+'/reports/lecturer-workload'+suffix]['get']
            assert operation['security']
            version=next(p for p in operation['parameters'] if p['name']=='version_id')
            assert version['required'] is True
